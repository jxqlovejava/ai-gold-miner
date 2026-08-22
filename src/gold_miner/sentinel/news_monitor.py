"""突发新闻监控 — 智能检测金价相关 breaking news.

v2 改进:
  1. NLP 否定句过滤 — "不会加息" ≠ 加息信号
  2. 时间过滤 — 仅处理 2h 内新闻，排除旧闻重推
  3. 市场联动 — 结合金价涨跌判断新闻是否已被定价
  4. 语义去重 — MD5 + 关键词重合度双重去重，6h TTL
  5. 扩展信源 — 新浪黄金+7×24+东财+金十数据
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from loguru import logger

from gold_miner.config import settings
from gold_miner.direction_lexicon import (
    DECLINE_VERBS,
    DOVE_REVERSAL_PATTERN,
    HAWK_REVERSAL_PATTERN,
)

from .models import symbol_cn

BEIJING = timezone(timedelta(hours=8))
_DEDUP_FILE = Path("/tmp/gold_news_dedup.json")
_DEDUP_TTL = 21600  # 6 小时去重
_NEWS_MAX_AGE = 7200  # 仅 2h 内新闻
_MIN_KEYWORD_OVERLAP = 0.6  # 去重: 关键词重合度阈值

# ── 跨运行推送去重 (2026-08-13) ──
# 背景: MD5 去重只防完全相同的标题; 同一故事以不同标题跨运行出现时(如
# 新浪/东财/金十多源标题措辞不同), 每 10 分钟重复告警, 叠加 iLink 限流.
# 方案: 持久化已推送标题, 新告警与近期已推送标题关键词重合度 > 阈值 → 抑制.
_PUSHED_TITLES_FILE = Path("/tmp/gold_news_pushed.json")
_PUSHED_TTL = 21600  # 6 小时
_MIN_REPEAT_OVERLAP = 0.6  # 与已推送标题重合度阈值 → 视为重复故事

# ── AI 判定层健康监控 (2026-08-11) ──
# 背景: AI 层失败时静默回退关键词规则, 用户收到的是规则判定的低质量推送且无从分辨.
# 方案: 记录回退事件到状态文件, 2h 滑窗内 ≥3 次 → 推送健康告警 + 规则判定条目打标.
_AI_FALLBACK_STATE = Path("/tmp/gold_news_ai_fallback.json")
_AI_FALLBACK_WINDOW = 7200  # 2h 滑窗
_AI_FALLBACK_THRESHOLD = 3  # 2h 内回退次数阈值 → 告警

# ── 否定句模式 (命中则跳过, 避免方向误判) ──
_NEGATION_PATTERNS: list[str] = [
    r"(不会|没有|否认|排除|暂不|未必|难以|不太可能|推迟|搁置|取消|叫停)"
    r"\s*({kw})",
    r"({kw}).*(不会|没有|否认|排除|暂缓|暂停)",
    r"(降溫|緩和|降溫|缓和|停火|协议达成|谈判取得)",
]

# ── 未落地模式 (命中则降级为中性: 等待/尚未/悬而未决 = 方向未定) ──
_PENDING_PATTERNS: list[str] = [
    r"等待.*明朗|等待.*结果|等待.*落地|等待.*进展|尚未.*达成|尚未.*签署|悬而未决",
    r"谈判.*进行中|谈判.*仍|仍在.*磋商|接近.*但.*未|等待.*谈判",
    r"不确定|未定|待定|观望|静观|前景未明|未明朗|尚不明朗|尚无定论|重开.*未明|重开.*未卜",
    # 方向矛盾信号: 协议已谈成但溢价回吐/利好出尽 = 短期利空 vs 长期利多对冲 → 中性
    r"溢价回吐|回吐|获利了结|利多出尽|利好兑现|sell.?the.?news|买预期卖事实",
]

# ── 假想/条件语气 (命中则降级, 2026-08-11) ──
# 背景: '哈塞特称如果他身在美联储 会维持利率不变或降息' 命中裸'美联储.*降息'
#       → 顶格 P0 重大利多. 但该句为白宫顾问的假想表态(非实际政策), 权重应大幅降低.
# 形态: 如果/假设/倘若/或将 等条件词 + 政策动作; '或将/或会' 覆盖市场传闻/预测.
_HYPOTHETICAL_PATTERNS: list[str] = [
    r"如果.{0,24}?(?:会|将|则|或|应该|应当)",
    r"假设|倘若|若.{0,10}?(?:会|将|则|或)",
    r"或将|或会|或应|考虑.{0,8}?(?:降息|加息|购买|增持)",
]

# ── 否定词 + 动作词 (全标题否定检测, 不依赖 matched_text 范围) ──
_NEGATIVE_WORDS: tuple[str, ...] = (
    "不会",
    "没有",
    "否认",
    "排除",
    "暂不",
    "未必",
    "难以",
    "不太可能",
    "推迟",
    "搁置",
    "取消",
    "叫停",
    "暂缓",
    "暂停",
    "拒绝",
)
_ACTION_WORDS: tuple[str, ...] = (
    "加息",
    "降息",
    "维持",
    "协议",
    "达成",
    "停火",
    "封锁",
    "袭击",
    "轰炸",
    "批准",
    "签署",
    "谈判",
    "关税",
    "制裁",
    "购金",
    "增持",
    "攻击",
    "威胁",
    "决议",
    "决策",
)


def _contains_negation(title: str) -> bool:
    """全标题否定检测: 否定词前后窗口内出现动作词 → 判定为否定句."""
    for neg in _NEGATIVE_WORDS:
        idx = title.find(neg)
        if idx == -1:
            continue
        window = title[max(0, idx - 14) : idx + 14 + len(neg)]
        if any(w in window for w in _ACTION_WORDS):
            return True
    return False


# ── 方向/程度 → 中文标签映射 ──
_DIRECTION_LABELS = {"bullish": "利多", "bearish": "利空", "neutral": "中性"}
_SEVERITY_LABELS = {"major": "重大", "moderate": "中度", "minor": "轻度"}


@dataclass(frozen=True)
class ContextOverride:
    """上下文修正规则 — 命中 context_pattern 时覆盖基础方向/程度/因果链."""

    context_pattern: str
    direction: str  # "bullish"/"bearish"/"neutral"
    severity: str  # "major"/"moderate"/"minor"
    reason: str  # 覆盖后的因果链说明


@dataclass(frozen=True)
class ImpactRule:
    """结构化高影响新闻规则."""

    pattern: str  # 关键词正则
    category: str  # geopolitical/fed/macro/...
    priority: str  # "P0"/"P1" (排序用)
    direction: str  # 基础方向 bullish/bearish/neutral
    severity: str  # 基础程度 major/moderate/minor
    reason: str  # 基础因果链说明 (方向已修正)
    context_rules: list[ContextOverride] = field(default_factory=list)


def _is_pending(title: str) -> bool:
    """未落地信号 → 方向未定, 降级为中性."""
    return any(re.search(pat, title) for pat in _PENDING_PATTERNS)


def _is_hypothetical(title: str) -> bool:
    """假想/条件语气 → 事件未落地, 降级权重 (如'如果他身在美联储会降息'=假设非政策).

    仅降级程度/优先级, 不翻转方向 — 假想的鸽派表态仍是弱利多, 只是不该顶格 P0.
    """
    return any(re.search(pat, title) for pat in _HYPOTHETICAL_PATTERNS)


# ── 高优先级关键词 → (结构化规则: 方向 + 程度 + 因果链 + 上下文修正) ──
_HIGH_IMPACT_RULES: list[ImpactRule] = [
    # ═ 地缘冲突 ═
    ImpactRule(
        pattern=r"美伊.*(?:冲突|升级|袭击|空袭|导弹|威胁|开火|对峙|封锁|战争|交火)"
        r"|(?:伊朗|美国).*(?:冲突升级|袭击|空袭|开战|战争扩大)",
        category="geopolitical",
        priority="P0",
        direction="bullish",
        severity="major",
        reason="美伊冲突升级→避险买盘+战争溢价↑→利多金价(若油价↑推升加息预期则远期承压)",
        context_rules=[
            ContextOverride(
                r"协议|达成|停火|缓和|和谈|谈判.*进展|原则.*同意|签署",
                "bullish",
                "major",
                "美伊缓和→降息预期↑+油价↓→通胀↓→利多金价(短期溢价回吐, 长期降息利多)",
            ),
        ],
    ),
    ImpactRule(
        pattern=r"霍尔木兹(?:.*(?:封锁|关闭|袭击|触雷|爆炸|中断))?|海峡.*封锁|油轮.*爆炸|油轮.*触雷|海峡.*关闭",
        category="energy",
        priority="P0",
        direction="bullish",
        severity="major",
        reason="霍尔木兹封锁→避险+供应危机→利多金价(若油价↑推升加息预期则远期承压)",
        context_rules=[
            # 未落地/观望式标题 → 中性. 必须置于'协议/重开'泛词之前, 否则被遮蔽:
            # '交易员关注霍尔木兹海峡协议' 仅'关注'未达成, 却先命中'协议'被误标 P0 利多 (2026-08-11)
            ContextOverride(
                r"(?:交易员|投资者|市场|交易商|机构).{0,24}?(?:等待|关注|观望|留意|紧盯|聚焦).{0,16}?霍尔木兹",
                "neutral",
                "minor",
                "霍尔木兹局势未落地(等待/观望)→方向未定，等待明确信号",
            ),
            # 注: '重开'为子串泛词, '重开前景未明'会被 _is_pending 二次降级中性
            # 升级 override 须在缓和 override 之前 (first-match-wins): '协议...不得通过' 是
            # 通行限制/升级, 不是缓和 (framework 铁律: 判别动作动词). 2026-08-22.
            ContextOverride(
                r"不得|禁止|不允许|限制通行|封锁|关闭|袭击|爆炸|触雷|导弹|开火",
                "bullish",
                "major",
                "霍尔木兹封锁/通行限制→避险+供应危机→利多金价(若油价↑推升加息预期则远期承压)",
            ),
            # 缓和词覆盖扩容 (2026-08-22): 通话/恢复谈判/协助护航/通过通航等也属缓和方向,
            #   此前漏配 → '商讨恢复谈判'/'美军协助6.6亿桶通过' 落默认"封锁→利多"错误文本.
            ContextOverride(
                r"协议|达成|停火|缓和|重开|重放|开放|谈判.*(?:进展|恢复)|原则.*同意|签署|明朗"
                r"|通话|会谈|磋商|协商|接触|恢复(?:谈判|通航|航运|通行)|协助|护航|护送"
                r"|通过.*海峡|海峡.*(?:通航|通行|开放|恢复)|航道.*(?:开放|恢复)",
                "bullish",
                "major",
                "霍尔木兹协议/缓和→供应危机缓解→油价↓→通胀↓→降息预期↑→利多金价(短期战争溢价回吐, 勿标'封锁')",
            ),
        ],
    ),
    # 红海/胡塞/油轮袭击 (P0 主题 israel_houthi 对应代码规则)
    ImpactRule(
        pattern=r"红海.*(?:袭击|封锁|关闭|中断)|胡塞.*(?:袭击|攻击|导弹|无人机)|油轮.*(?:遭.*袭击|被.*袭击|遇袭|触雷|爆炸)",
        category="geopolitical",
        priority="P0",
        direction="bullish",
        severity="major",
        reason="红海/胡塞冲突→避险+供应中断→利多金价(若油价↑推升加息预期则远期承压)",
        context_rules=[
            ContextOverride(
                r"不得|禁止|不允许|限制通行|封锁|关闭|袭击|爆炸|触雷|导弹|开火",
                "bullish",
                "major",
                "红海/胡塞封锁/冲突→避险+供应中断→利多金价(若油价↑推升加息预期则远期承压)",
            ),
            ContextOverride(
                r"协议|达成|停火|缓和|重开|开放|谈判.*(?:进展|恢复)|原则.*同意|签署|明朗"
                r"|通话|会谈|磋商|协商|接触|恢复(?:谈判|通航|航运|通行)|协助|护航|护送"
                r"|通过.*海峡|海峡.*(?:通航|通行|开放|恢复)|航道.*(?:开放|恢复)",
                "bullish",
                "major",
                "红海/胡塞缓和→供应中断缓解→油价↓→通胀↓→降息预期↑→利多金价(短期战争溢价回吐, 勿标'封锁')",
            ),
        ],
    ),
    ImpactRule(
        pattern=r"空袭|导弹.*袭击|无人机.*攻击|轰炸|军事.*打击",
        category="geopolitical",
        priority="P0",
        direction="bullish",
        severity="major",
        reason="军事冲突→避险买盘+战争溢价↑→利多金价(若油价↑推升加息预期则远期承压)",
    ),
    ImpactRule(
        pattern=r"宣战|全面.*战争|军事.*升级|战争.*扩大",
        category="geopolitical",
        priority="P0",
        direction="bullish",
        severity="major",
        reason="战争升级→避险买盘+战争溢价↑→利多金价(若油价↑推升加息预期则远期承压)",
    ),
    ImpactRule(
        pattern=r"美军.*增派|航母.*部署|部队.*调动",
        category="geopolitical",
        priority="P1",
        direction="bullish",
        severity="moderate",
        reason="军事部署→地缘风险↑→避险买盘→利多金价",
    ),
    ImpactRule(
        # 2026-08-10 修复: 旧 pattern 为裸国名交替 '科威特|巴林|卡塔尔|约旦|阿联酋|沙特.*遭.*袭击',
        # '阿联酋退出欧佩克后 Adnoc Gas 扩产' 等纯能源/商业新闻仅含国名即命中, 套用'冲突外溢'利多模板
        # (与同日收紧'签署'规则同构)。现要求国名 + 军事冲突动作同现(双向), 裸国名不再命中。
        pattern=(
            r"(?:科威特|巴林|卡塔尔|约旦|阿联酋|沙特)(?:.{0,8}?)(?:遭袭|遇袭|袭击|被袭|空袭|轰炸|导弹|冲突|局势升级|动荡|交火|开火|战争)"
            r"|(?:袭击|空袭|轰炸|导弹|冲突|战争|交火).{0,8}?(?:科威特|巴林|卡塔尔|约旦|阿联酋|沙特)"
        ),
        category="geopolitical",
        priority="P1",
        direction="bullish",
        severity="moderate",
        reason="海湾国家冲突→冲突外溢→区域不稳定→避险买盘→利多金价",
    ),
    # ═ 地缘降级 (独立规则, 供共用) ═
    # 2026-08-10 收紧: 旧 pattern 为裸'协议|达成|停火|和谈|谈判.*进展|原则.*同意|签署',
    # '迅策与天合算力签署战略合作备忘录'等公司签约新闻命中'签署'被误判 P0 利多.
    # 现要求地缘主体词前置; 纯商业'协议/签署'标题不再命中, 由相关性闸门(_has_gold_relevance)兜底.
    ImpactRule(
        pattern=r"(?:美伊|伊朗|以色列|胡塞|也门|霍尔木兹|红海|曼德海峡|波斯湾|沙特|叙利亚|黎巴嫩|真主党|"
        r"俄乌|乌克兰|俄罗斯|朝鲜|台海|南海|中东|加沙|巴勒斯坦|哈马斯|半岛|海湾)"
        r".{0,16}?(?:停火|和谈|谈判.*(?:进展|达成|同意)|原则.*同意|协议|签署|达成|和平|调停|斡旋|缓和)",
        category="geopolitical",
        priority="P0",
        direction="bullish",
        severity="major",
        reason="地缘降级/协议达成→战争溢价回吐→油价↓→通胀↓→降息预期↑→利多金价",
    ),
    # ═ 加息/降息预期反转 (2026-08-08: '削弱/降温/回落加息预期'=方向反转, 修复前误标利空)
    #   2026-08-10: 词表收敛至 direction_lexicon, 补 '走低/下滑/下探/降至' + 押注/定价, 修复 '加息概率走低' 误标利空) ═
    # 置于 '美联储.*加息' 规则之前, 优先命中反转构式, 免于被裸'加息'子串误判利空
    ImpactRule(
        pattern=rf"削弱.*加息预期|{HAWK_REVERSAL_PATTERN}",
        category="fed",
        priority="P0",
        direction="bullish",
        severity="major",
        reason="加息预期/概率走低→实际利率预期↓→降息预期↑→利多金价",
    ),
    ImpactRule(
        pattern=rf"削弱.*降息预期|{DOVE_REVERSAL_PATTERN}",
        category="fed",
        priority="P0",
        direction="bearish",
        severity="major",
        reason="降息预期/概率走低→宽松预期消退→实际利率预期↑→利空金价",
    ),
    # ═ 美联储 ═
    ImpactRule(
        pattern=r"美联储.*加息|Fed.*hike|FOMC.*加息|加息.*预期.*升温",
        category="fed",
        priority="P0",
        direction="bearish",
        severity="major",
        reason="加息→实际利率↑→强烈利空金价",
    ),
    ImpactRule(
        pattern=r"美联储.*降息|Fed.*cut|FOMC.*降息|降息.*预期.*升温",
        category="fed",
        priority="P0",
        direction="bullish",
        severity="major",
        reason="降息→实际利率↓→强烈利多金价",
    ),
    ImpactRule(
        pattern=r"美联储.*维持|按兵不动|hold.*rate",
        category="fed",
        priority="P1",
        direction="neutral",
        severity="moderate",
        reason="维持利率→短期中性, 关注点阵图",
    ),
    ImpactRule(
        pattern=r"沃什|Warsh|美联储主席|鲍威尔|Powell",
        category="fed",
        priority="P0",
        direction="neutral",
        severity="minor",
        reason="联储主席讲话→政策信号→方向取决于措辞",
        context_rules=[
            ContextOverride(
                r"鹰派|加息|抗通胀|缩表|维持紧缩",
                "bearish",
                "moderate",
                "联储主席鹰派→加息预期↑→利空金价",
            ),
            ContextOverride(
                r"鸽派|降息|支持.*降息|宽松",
                "bullish",
                "moderate",
                "联储主席鸽派→降息预期↑→利多金价",
            ),
        ],
    ),
    ImpactRule(
        pattern=r"点阵图|dot.plot|利率.*预测",
        category="fed",
        priority="P1",
        direction="neutral",
        severity="moderate",
        reason="利率路径预期→方向取决于鹰/鸽分布",
    ),
    # ═ 宏观数据 ═
    ImpactRule(
        pattern=r"非农.*大幅.*超预期|就业.*强劲|失业.*下降",
        category="macro",
        priority="P0",
        direction="bearish",
        severity="major",
        reason="就业强劲→加息压力↑→利空金价",
        context_rules=[
            ContextOverride(
                r"不及预期|低于预期|疲软|放缓",
                "bullish",
                "major",
                "就业疲软→加息压力↓→利多金价",
            ),
        ],
    ),
    ImpactRule(
        pattern=r"就业.*崩|失业.*飙升|非农.*大跌|非农.*不及预期|非农.*爆冷|非农.*远不及预期|非农.*大幅低于预期",
        category="macro",
        priority="P0",
        direction="bullish",
        severity="major",
        reason="劳动力恶化→衰退风险→降息预期↑→利多金价",
    ),
    ImpactRule(
        pattern=r"CPI.*超预期|通胀.*超预期|PCE.*超预期",
        category="macro",
        priority="P0",
        direction="bearish",
        severity="major",
        reason="通胀超预期→加息压力↑→利空金价",
    ),
    ImpactRule(
        pattern=r"CPI.*低于预期|通胀.*放缓|PCE.*低于|通胀.*回落",
        category="macro",
        priority="P0",
        direction="bullish",
        severity="major",
        reason="通胀回落→加息压力↓→利多金价",
    ),
    # ═ 极端行情 ═
    ImpactRule(
        pattern=r"金价.*暴跌.*[5-9]%|黄金.*大跌.*[5-9]%|gold.*crash",
        category="market",
        priority="P0",
        direction="bearish",
        severity="major",
        reason="极端行情→恐慌抛售→技术面恶化→利空(关注止损)",
    ),
    ImpactRule(
        pattern=r"金价.*暴涨.*[3-9]%|黄金.*大涨.*[3-9]%|gold.*surge",
        category="market",
        priority="P0",
        direction="bullish",
        severity="major",
        reason="极端行情→快速拉升→动量利多(关注止盈)",
    ),
    ImpactRule(
        pattern=r"金价.*跌破.*[34]\d{3}|gold.*below.*[34]\d{3}",
        category="market",
        priority="P0",
        direction="bearish",
        severity="moderate",
        reason="跌破关键位→技术面恶化→利空",
    ),
    # ═ 央行 ═
    ImpactRule(
        pattern=r"央行.*购金.*[1-9]\d{2}吨|央行.*增持.*黄金",
        category="central_bank",
        priority="P1",
        direction="bullish",
        severity="moderate",
        reason="央行购金→结构性利多→长期支撑",
    ),
    ImpactRule(
        pattern=r"去美元化|de-dollarization|外汇储备.*黄金",
        category="central_bank",
        priority="P1",
        direction="bullish",
        severity="moderate",
        reason="货币体系变化→长期利多金价",
    ),
    # ═ 油价 ═
    ImpactRule(
        pattern=r"油价.*飙|原油.*暴涨|Brent.*[89]\d|WTI.*[89]\d",
        category="energy",
        priority="P1",
        direction="bearish",
        severity="moderate",
        reason="油价↑→通胀预期↑→加息→利空金价",
    ),
    ImpactRule(
        pattern=r"油价.*崩|原油.*暴跌|Brent.*跌破.*[56]\d|油价.*回落",
        category="energy",
        priority="P1",
        direction="bullish",
        severity="moderate",
        reason="油价↓→通胀降温→加息压力↓→利多金价",
    ),
    # ═ 能源→加息预期→利空 (2026-08-11): '能源价格飙升推高加息预期' 应判利空,
    #   此前仅命中欧洲能源中性规则 → 漏判利空链. 置于欧洲能源规则之前防被中性遮蔽.
    ImpactRule(
        pattern=(
            r"(?:能源|天然气|油价|原油|石油|通胀).{0,12}?(?:推高|推升|飙升|加剧|升温|走高).{0,12}?加息预期"
            r"|加息预期.{0,12}?(?:推高|推升|飙升|加剧|升温|走高|强化)"
            r"|(?:推高|推升|飙升).{0,16}?加息预期"
        ),
        category="energy",
        priority="P1",
        direction="bearish",
        severity="moderate",
        reason="能源涨价→通胀预期↑→加息预期↑→实际利率↑→利空金价",
    ),
    # ═ 欧洲能源 (传导弱, 避免误判) ═
    ImpactRule(
        pattern=r"欧洲.*天然气|天然气.*上涨|欧洲.*能源|能源.*价格",
        category="energy",
        priority="P1",
        direction="neutral",
        severity="minor",
        reason="欧洲天然气价格对美国金价传导弱→方向未定",
        context_rules=[
            ContextOverride(
                r"霍尔木兹|原油|供应危机",
                "bullish",
                "moderate",
                "能源供应危机→避险+油价↑→利多金价(若油价↑推升加息预期则远期承压)",
            ),
            ContextOverride(
                r"下跌|回落|降",
                "bullish",
                "moderate",
                "能源价格回落→通胀降温→利多金价",
            ),
        ],
    ),
    # ═ 贸易/制裁 ═
    ImpactRule(
        pattern=r"关税.*加征|trade.*war|贸易.*战|制裁.*升级",
        category="trade",
        priority="P1",
        direction="bullish",
        severity="moderate",
        reason="贸易战升级→避险利多(但通胀复杂)",
    ),
    ImpactRule(
        pattern=r"资本.*管制|外汇.*管制|资金.*封锁",
        category="policy",
        priority="P1",
        direction="bearish",
        severity="moderate",
        reason="流动性危机→短期利空一切资产",
    ),
    # ═ 印度黄金需求 ═
    ImpactRule(
        pattern=r"印度.*关税|India.*tariff|India.*duty.*gold",
        category="india_gold",
        priority="P2",
        direction="neutral",
        severity="minor",
        reason="印度关税调整→需求变化→边际影响金价",
    ),
    ImpactRule(
        pattern=r"印度.*黄金.*进口|India.*gold.*import",
        category="india_gold",
        priority="P2",
        direction="neutral",
        severity="minor",
        reason="印度进口数据→全球第二大需求国动向",
    ),
    # ═ 美国选举 ═
    ImpactRule(
        pattern=r"中期选举|midterm.*election|美国.*选举",
        category="election",
        priority="P1",
        direction="bullish",
        severity="moderate",
        reason="政策不确定性→避险买盘→利多金价",
    ),
    ImpactRule(
        pattern=r"民调|poll.*congress|选举.*民调",
        category="election",
        priority="P1",
        direction="neutral",
        severity="minor",
        reason="选举民调→不确定性定价→影响金价",
    ),
]

# ── 语义推理层 (Stage 2) ──
_LLM_CONF_THRESHOLD = 0.5  # AI 置信度门槛, 低于则回退关键词规则

# 宽泛提及桶 (候选B): 未命中严格规则但提及风险主体/主题 → 交 AI 裁决"纯提及 vs 真实事件"
_BROAD_MENTION_PATTERNS: list[str] = [
    r"伊朗|以色列|胡塞|霍尔木兹|红海|曼德海峡|波斯湾|沙特|叙利亚|黎巴嫩|真主党"
    r"|俄乌|乌克兰|俄罗斯|朝鲜|台海|南海",
    r"制裁|关税|停火|和谈|谈判|外交|军事|冲突|战争|袭击|导弹|空袭|封锁|威胁",
    r"油价|原油|天然气|能源危机|美联储|FOMC|CPI|非农|PCE|降息|加息|央行|利率决议",
]

# ── fed/macro 语义反转信号 (2026-08-08: 确定性类目命中此 → 升级路由 LLM 裁决) ──
# 设计约束: fed/macro/market 默认不路由 LLM (确定性类目防幻觉), 但'削弱/降温加息预期'等
# 反转构式破坏确定性假设 → 命中反转信号时升级路由, 由 LLM 裁决方向/传导链.
_SEMANTIC_AMBIGUITY_PATTERNS: list[str] = [
    # 下降词表与 direction_lexicon.DECLINE_VERBS 共用 (2026-08-10: 补走低/下滑/降至/押注等)
    rf"(?:削弱|{DECLINE_VERBS}|缓解|减轻|泼冷水|见顶|转向)"
    rf"\s*[^，。;；]{{0,16}}?(?:加息|降息|利率|政策)\s*(?:预期|压力|概率|步伐|周期|押注|定价)",
    rf"(?:加息|降息)\s*(?:预期|压力|概率|步伐|周期|押注|定价)\s*(?:{DECLINE_VERBS}|见顶|转向)",
    r"(?:非农|就业|CPI|PCE|通胀).{0,8}?(?:爆冷|不及预期|低于预期|超预期|高于预期|意外|疲软|强劲|走弱|回升|反弹)",
]


def _has_ambiguity_signal(title: str) -> bool:
    """fed/macro 确定性类目是否携带语义反转/模糊信号 → 升级路由 LLM."""
    return any(re.search(p, title) for p in _SEMANTIC_AMBIGUITY_PATTERNS)


def _semantic_analyzer():
    """构建语义分析器 (惰性导入, 避免监控链路强制依赖 LLM)."""
    from .news_semantic import SemanticNewsAnalyzer

    return SemanticNewsAnalyzer()


def _market_adjust(level: str, gold_change_pct: float, impact: str) -> tuple[str, str]:
    """市场联动: 利多新闻+金价已跌=可能错杀(升级P0); 利空+金价反涨=已被定价(降级P1)."""
    extra = ""
    if abs(gold_change_pct) > 1.5:
        if gold_change_pct < 0 and "利多" in impact:
            extra = f" (金价已跌{gold_change_pct:.1f}%, 可能尚未定价)"
            level = "P0"
        elif gold_change_pct > 0 and "利空" in impact:
            extra = f" (金价反涨{gold_change_pct:.1f}%, 市场可能已预期)"
            level = "P1"
    return level, extra


# 中等优先级 — 仅做背景，不推送
_MED_IMPACT_PATTERNS: list[str] = [
    r"黄金ETF|GLD|SPDR|gold.*etf",
    r"COMEX.*持仓|CFTC.*黄金|投机.*仓位",
    r"美元指数|DXY|dollar.*index",
    r"美债.*收益|Treasury.*yield",
    r"金银比|gold.silver",
    r"技术分析|technical.*analysis.*gold",
]

# ── 金价相关性闸门 (2026-08-10): AI 不可用时最后的兜底 ──
# 候选A命中规则但标题不含任何金价维度 → 疑似无关(如'XX公司与YY签署战略合作备忘录'), 丢弃.
# 设计: 词表覆盖黄金本体/强地缘主体/军事动作/制裁/联储/宏观/能源/避险;
#       纯中性商业词(协议/达成/签署/谈判)故意不在列——它们正是泛词误报的来源.
_GOLD_RELEVANT_PATTERNS: list[str] = [
    # 黄金本体
    r"黄金|金价|金条|金币|金银|贵金属|XAU|Gold|购金|实物金",
    # 地缘主体 (强) — 含英文 (规则 pattern 同源词)
    r"伊朗|以色列|胡塞|也门|霍尔木兹|红海|曼德海峡|波斯湾|沙特|叙利亚|黎巴嫩|真主党|Iran|Israel|Houthi|Hormuz",
    r"俄乌|乌克兰|俄罗斯|朝鲜|台海|南海|中东|加沙|巴勒斯坦|哈马斯|半岛|海湾|Russia|Ukraine|Gaza",
    # 地缘/军事动作 (强缓和+冲突)
    r"停火|和谈|和平|调停|斡旋|缓和|战争|冲突|袭击|导弹|空袭|轰炸|开火|封锁|军事|美军|航母|部队|增兵|撤军",
    r"ceasefire|truce|war|missile|strike|attack|sanction|invasion|military",
    # 制裁/关税/资本管制
    r"制裁|关税|贸易战|资本管制|外汇管制|tariff|trade.?war",
    # 联储/利率/货币
    r"美联储|FOMC|Fed|鲍威尔|沃什|点阵图|美元|美债|收益率|实际利率|加息|降息|央行|利率决议|去美元化|外汇储备",
    r"hike|cut|dot.?plot|yield|dollar|rate",
    # 通胀/宏观
    r"CPI|PCE|PPI|NFP|非农|就业|失业|通胀|通缩|衰退|unemployment|inflation|recession",
    # 能源
    r"油价|原油|Brent|WTI|天然气|能源危机|石油|oil|crude|energy",
    # 避险/市场 + 政治选举
    r"避险|避险资产|risk.?off|safe.?haven|金价.*(?:涨|跌|新高|新低)|gold.*(?:surge|crash|price)",
    r"中期选举|选举|民调|election|midterm|poll|congress",
]


def _has_gold_relevance(title: str) -> bool:
    """标题是否含金价相关信号词 — AI 不可用时兜底, 防无关新闻混入突发预警."""
    return any(re.search(p, title, re.IGNORECASE) for p in _GOLD_RELEVANT_PATTERNS)


def _parse_sina_time(ctime_str: str) -> float | None:
    """解析新浪 ctime 为 Unix 时间戳."""
    if not ctime_str:
        return None
    try:
        ts = int(ctime_str)
        if ts > 1_000_000_000_000:
            ts = ts // 1000  # 毫秒 → 秒
        return float(ts)
    except (ValueError, TypeError):
        pass
    # 尝试解析常见日期格式
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%m-%d %H:%M:%S"]:
        try:
            dt = datetime.strptime(ctime_str, fmt)
            return dt.replace(tzinfo=BEIJING).timestamp()
        except ValueError:
            continue
    return None


def _keyword_overlap(title1: str, title2: str) -> float:
    """两条标题的关键词重合度 (0-1)."""

    # 简单分词: 取 2-gram 字符
    def _ngrams(s: str, n: int = 2) -> set[str]:
        s = re.sub(r"[^一-鿿\w]", "", s)
        return {s[i : i + n] for i in range(len(s) - n + 1)}

    g1 = _ngrams(title1)
    g2 = _ngrams(title2)
    if not g1 or not g2:
        return 0.0
    return len(g1 & g2) / min(len(g1), len(g2))


def fetch_gold_headlines() -> list[dict]:
    """多源抓取黄金相关头条 (国内可访问).

    数据源: 新浪黄金 + 7×24快讯 + 东方财富 + 金十数据
    """
    headlines: list[dict] = []
    now = time.time()

    def _fetch(url: str, source: str, parser, headers=None) -> int:
        try:
            h = headers or {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
            resp = httpx.get(url, headers=h, timeout=8.0)
            if resp.status_code == 200:
                return parser(resp, source)
        except Exception:
            pass
        return 0

    # 1. 新浪黄金频道
    def _parse_sina(resp, src):
        added = 0
        for item in resp.json().get("result", {}).get("data", []):
            title = item.get("title", "").strip()
            ctime = item.get("ctime", "")
            ts = _parse_sina_time(ctime)
            if title and (ts is None or now - ts < _NEWS_MAX_AGE):
                headlines.append(
                    {
                        "title": title,
                        "time": ctime,
                        "ts": ts,
                        "source": src,
                        "url": item.get("url", ""),
                    }
                )
                added += 1
        return added

    _fetch(
        "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&k=&num=20&page=1",
        "新浪黄金",
        _parse_sina,
    )

    # 2. 7×24 快讯
    _fetch(
        "https://feed.mix.sina.com.cn/api/roll/get?pageid=154&lid=2637&k=&num=30&page=1",
        "7×24快讯",
        _parse_sina,
    )

    # 3. 东方财富黄金
    def _parse_em(resp, src):
        added = 0
        for item in resp.json().get("data", {}).get("diff", []):
            title = item.get("f14", "").strip()
            if title:
                headlines.append(
                    {
                        "title": title,
                        "time": "",
                        "ts": None,
                        "source": src,
                        "url": "",
                    }
                )
                added += 1
        return added

    _fetch(
        "https://push2.eastmoney.com/api/qt/clist/get"
        "?np=1&fltt=2&fields=f13,f14&fid=f13&fs=m:116&pn=1&pz=15",
        "东方财富",
        _parse_em,
    )

    # 4. 金十数据 (flash news)
    def _parse_jin10(resp, src):
        added = 0
        try:
            data = resp.json()
            # jin10 API 格式: {data: [{content: "...", time: "..."}]}
            items = data if isinstance(data, list) else data.get("data", [])
            for item in items[:20]:
                title = (item.get("content") or item.get("title") or "").strip()
                if title:
                    headlines.append(
                        {
                            "title": title,
                            "time": item.get("time", ""),
                            "ts": None,
                            "source": src,
                            "url": "",
                        }
                    )
                    added += 1
        except Exception:
            pass
        return added

    _fetch(
        "https://flash-api.jin10.com/get_flash_list?channel=-8200&vip=1&_=" + str(int(now * 1000)),
        "金十数据",
        _parse_jin10,
    )

    return headlines


def analyze_headlines(
    headlines: list[dict],
    gold_change_pct: float = 0.0,
    semantic: object | None = None,
) -> list[dict]:
    """智能分析头条: 否定句过滤 + 时间过滤 + 语义去重 + 市场联动 + AI 语义推理.

    三层架构:
      Stage 1  关键词快筛 (strict 候选A + 宽泛提及候选B)
      Stage 2  AI 语义推理判定方向/程度/传导链 (批量一次)
      Stage 3  校验与降级 (枚举白名单 + confidence 门槛 + 确定性守卫 + 回退 regex)

    Args:
        headlines: 新闻列表
        gold_change_pct: 当前金价日内涨跌幅 (用于市场联动)
        semantic: 语义分析器实例 (测试注入), None 时自动构建并仅在可用时启用

    Returns:
        [{"title":..., "impact":..., "level":"P0"/"P1", "matched_kw":..., "category":...}]
    """
    dedup_cache = _load_dedup()
    now = time.time()
    strict: list[dict] = []  # 候选A: 关键词规则命中 (带预分类)
    broad: list[dict] = []   # 候选B: 宽泛提及, 交 AI 裁决纯提及 vs 真实事件

    # ── Stage 1a: 关键词规则命中 (候选A) ──
    for h in headlines:
        title = h.get("title", "")
        if not title or len(title) < 6:
            continue

        # ── 时间过滤 ──
        ts = h.get("ts")
        if ts is not None and now - ts > _NEWS_MAX_AGE:
            continue

        # ── MD5 去重 ──
        thash = _title_hash(title)
        if thash in dedup_cache:
            continue

        # ── 关键词匹配 + 否定句过滤 + 上下文修正 ──
        for rule in _HIGH_IMPACT_RULES:
            try:
                match = re.search(rule.pattern, title)
                if not match:
                    continue

                matched_text = match.group(0)
                # 否定句检查 (全标题否定词检测)
                if _contains_negation(title):
                    continue

                # 去重: 检查与已有候选的关键词重合度
                is_dup = False
                for existing in strict:
                    if _keyword_overlap(title, existing["title"]) > _MIN_KEYWORD_OVERLAP:
                        is_dup = True
                        break
                if is_dup:
                    continue

                # ── 上下文修正: 命中 context_rules → 覆盖方向/程度/因果链 ──
                direction, severity, impact = rule.direction, rule.severity, rule.reason
                for ctx in rule.context_rules:
                    if re.search(ctx.context_pattern, title):
                        direction, severity, impact = (
                            ctx.direction,
                            ctx.severity,
                            ctx.reason,
                        )
                        break

                # ── 未落地信号: 等待/尚未/悬而未决 → 降级中性 (方向未定) ──
                if _is_pending(title):
                    direction = "neutral"
                    severity = "minor"
                    impact = "事件方向未定(未落地或利多/利空对冲)，等待明确信号再评估"

                # ── 假想/条件语气 → 降级权重 (2026-08-11: '如果他身在美联储会降息'
                #    命中裸'美联储.*降息'被顶格 P0, 但属假设非实际政策) ──
                level = rule.priority
                if _is_hypothetical(title):
                    if severity == "major":
                        severity = "moderate"
                    if level == "P0":
                        level = "P1"
                    # impact 前缀统一在 _finalize 加, 避免双重前缀

                strict.append(
                    {
                        "title": title,
                        "ts": ts,
                        "source": h.get("source", ""),
                        "time": h.get("time", ""),
                        "hash": thash,
                        "matched_kw": matched_text,
                        "category": rule.category,
                        "direction": direction,
                        "severity": severity,
                        "impact": impact,
                        "level": level,
                    }
                )
                break
            except re.error:
                continue

    # ── Stage 1b: 宽泛提及 (候选B) — 未 strict 命中但提及风险主体/主题 ──
    strict_titles = {c["title"] for c in strict}
    for h in headlines:
        title = h.get("title", "")
        if not title or len(title) < 6 or title in strict_titles:
            continue
        ts = h.get("ts")
        if ts is not None and now - ts > _NEWS_MAX_AGE:
            continue
        if _title_hash(title) in dedup_cache or _contains_negation(title):
            continue
        if any(re.search(p, title) for p in _BROAD_MENTION_PATTERNS):
            broad.append(
                {
                    "title": title,
                    "ts": ts,
                    "source": h.get("source", ""),
                    "time": h.get("time", ""),
                    "hash": _title_hash(title),
                    "matched_kw": "",
                    "category": None,
                    "level": "P1",  # 默认 P1, 由 LLM priority 覆盖
                }
            )

    # ── Stage 2: AI 语义推理 (批量一次, 仅路由类目 + 候选B) ──
    analyzer = semantic if semantic is not None else _semantic_analyzer()
    routed_categories = getattr(analyzer, "categories", None) or set(
        # getattr 兜底: 部署非原子期间旧 config 可能缺该字段, 避免夜间哨兵崩溃
        getattr(settings, "news_llm_categories", ["geopolitical", "energy", "trade", "policy", "election"])
    )
    routed = [c for c in strict if c.get("category") in routed_categories] + broad
    # fed/macro 确定性类目带反转信号 → 升级路由 LLM 裁决 (修复'削弱加息预期'误判利空)
    # 2026-08-11: 假想/条件语气('如果他身在美联储会降息'=纯假设个人观点)也升级路由,
    #   关键词规则的裸'美联储.*降息'会把假想顶格成 P0, AI 判 real=False 直接不告警更准确.
    for c in strict:
        if c.get("category") in routed_categories:
            continue
        if _has_ambiguity_signal(c["title"]) or _is_hypothetical(c["title"]):
            ec = dict(c)
            ec["escalate"] = True
            routed.append(ec)
    llm_results: dict[str, dict] = {}
    if routed and getattr(analyzer, "enabled", False):
        try:
            llm_results = analyzer.classify_many(routed) or {}
        except Exception:
            logger.exception("语义推理异常, 回退关键词规则")
            llm_results = {}
    # AI 判定层健康监控: 有路由候选但 AI 零返回 → 本次判定退化 (记录, 超阈值推送告警)
    if routed and not llm_results and getattr(analyzer, "enabled", False):
        _record_ai_fallback()

    # 本应交 AI 判定的标题 (路由类目或升级) — 无 LLM 结果 → 打规则判定标
    routed_titles = {c2["title"] for c2 in routed}

    # ── Stage 3: 校验与降级, 生成最终告警 ──
    alerts: list[dict] = []

    def _finalize(c: dict, llm: dict | None) -> dict | None:
        """合并 LLM 字段并应用确定性守卫. 返回告警 dict 或 None(丢弃)."""
        conf = llm.get("confidence", 0.0) if llm else 0.0
        llm_active = llm is not None and conf >= _LLM_CONF_THRESHOLD
        if llm_active:
            # AI 判定为纯提及 → 丢弃
            if llm.get("is_real_event") is False:
                return None
            # 逐字段采用 (仅白名单校验过的有效字段覆盖)
            if "direction" in llm:
                c["direction"] = llm["direction"]
            if "severity" in llm:
                c["severity"] = llm["severity"]
            if "priority" in llm:
                c["level"] = llm["priority"]
            if "category" in llm:
                c["category"] = llm["category"]
            if "transmission_chain" in llm:
                c["impact"] = llm["transmission_chain"]
            # 未落地守卫: LLM 或确定性信号任一命中 → 强制中性 (覆盖 LLM 方向)
            if llm.get("is_pending") or _is_pending(c["title"]):
                c["direction"] = "neutral"
                c["severity"] = "minor"
                c["impact"] = "事件方向未定(未落地或利多/利空对冲)，等待明确信号再评估"
        # ── 假想/条件语气守卫: 覆盖 LLM 方向, 降级权重 (2026-08-11) ──
        if _is_hypothetical(c["title"]):
            if c.get("severity") == "major":
                c["severity"] = "moderate"
            if c.get("level") == "P0":
                c["level"] = "P1"
            if c.get("direction") in ("bullish", "bearish"):
                c["impact"] = f"假想/条件表述(非实际政策动作), 权重降低: {c.get('impact', '')}"
        # ── 规则判定打标 (2026-08-11): 本应交 AI 判定但 AI 无返回 → 标注质量,
        #    让用户分辨推送是 AI 判定还是关键词兜底 (事故: AI 挂时收到规则判定误判推送) ──
        if llm is None and c["title"] in routed_titles:
            c["impact"] = (c.get("impact") or "") + " ⚠️规则判定·LLM不可用"
        # ── 金价相关性兜底 (2026-08-10): AI 未生效时, 标题无任何金价维度 → 疑似无关, 丢弃 ──
        # 背景: 'XX公司与YY签署战略合作备忘录'等纯商业新闻命中泛词规则被误判 P0 利多.
        # 语义闸门(AI)不可用时由关键词相关性闸门兜底; AI 已裁决(含纯提及丢弃)则不再重复拦截.
        if not llm_active and not _has_gold_relevance(c["title"]):
            logger.debug(f"[news_monitor] 疑似无关新闻, 丢弃: {c['title']}")
            return None
        # 候选B 无有效类目 → 无法归档, 丢弃
        if c.get("category") is None:
            return None
        # ── 市场联动: 调整优先级 ──
        level, extra = _market_adjust(c["level"], gold_change_pct, c["impact"])
        c["level"] = level
        c["impact"] = c["impact"] + extra
        c["label"] = (
            f"{_DIRECTION_LABELS.get(c['direction'], '中性')}"
            f"·{_SEVERITY_LABELS.get(c['severity'], '轻度')}"
        )
        return c

    for c in strict:
        fin = _finalize(dict(c), llm_results.get(c["title"]))
        if fin is not None:
            alerts.append(fin)

    for c in broad:
        llm = llm_results.get(c["title"])
        if llm is None or llm.get("confidence", 0) < _LLM_CONF_THRESHOLD:
            continue  # AI 不可用/低置信 → 宁缺毋滥
        if llm.get("is_real_event") is False:
            continue
        if "category" not in llm:
            continue  # 无有效类目 → 丢弃
        fin = _finalize(c, llm)
        if fin is not None:
            alerts.append(fin)

    # 更新去重缓存 (含被评估标题, 避免 6h 内重复 LLM 计费)
    for c in strict + broad:
        dedup_cache[c["hash"]] = now
    dedup_cache = {k: v for k, v in dedup_cache.items() if now - v < _DEDUP_TTL}
    _save_dedup(dedup_cache)

    # 排序: P0 在前, 同类归组
    alerts.sort(key=lambda a: (0 if a["level"] == "P0" else 1, a.get("category", "")))
    return alerts


def _record_ai_fallback(now: float = time.time()) -> None:
    """记录一次 AI 判定层回退 (2h 滑窗内计数).

    在 analyze_headlines 中 AI 被咨询但无返回时调用.
    """
    try:
        data: dict = {"events": [], "last_warned": None}
        if _AI_FALLBACK_STATE.exists():
            data = json.loads(_AI_FALLBACK_STATE.read_text())
        events = [e for e in data.get("events", []) if now - e < _AI_FALLBACK_WINDOW]
        events.append(now)
        data["events"] = events
        _AI_FALLBACK_STATE.write_text(json.dumps(data))
    except Exception:
        pass


def _ai_health_warning(now: float = time.time()) -> str:
    """AI 回退超阈值 → 返回健康告警文本 (每窗口限频一条, 防止刷屏).

    由 run_news_check 调用: 即使无新闻也推送, 让用户知晓当前推送为规则判定.
    """
    try:
        if not _AI_FALLBACK_STATE.exists():
            return ""
        data = json.loads(_AI_FALLBACK_STATE.read_text())
        events = [e for e in data.get("events", []) if now - e < _AI_FALLBACK_WINDOW]
        if len(events) < _AI_FALLBACK_THRESHOLD:
            return ""
        last = data.get("last_warned")
        if last and now - last < _AI_FALLBACK_WINDOW:
            return ""  # 本窗口已告警过
        data["last_warned"] = now
        _AI_FALLBACK_STATE.write_text(json.dumps(data))
        minutes = int((now - min(events)) / 60) if events else 0
        return (
            f"⚠️ AI判定层异常: 近2h {len(events)} 次回退关键词规则"
            f"(持续约{minutes}分钟). 当前突发新闻推送为规则判定, 质量下降."
            f"请检查 LLM_API_KEY 与 DeepSeek 服务."
        )
    except Exception:
        return ""


def _load_dedup() -> dict[str, float]:
    if not _DEDUP_FILE.exists():
        return {}
    try:
        data = json.loads(_DEDUP_FILE.read_text())
        now = time.time()
        return {k: v for k, v in data.items() if now - v < _DEDUP_TTL}
    except Exception:
        return {}


def _save_dedup(cache: dict[str, float]) -> None:
    with contextlib.suppress(Exception):
        _DEDUP_FILE.write_text(json.dumps(cache))


def _load_pushed() -> list[dict]:
    """加载近期推送过的新闻标题 (跨运行去重用)."""
    if not _PUSHED_TITLES_FILE.exists():
        return []
    try:
        data = json.loads(_PUSHED_TITLES_FILE.read_text())
        now = time.time()
        return [p for p in data if now - p["ts"] < _PUSHED_TTL]
    except Exception:
        return []


def _save_pushed(pushed: list[dict]) -> None:
    with contextlib.suppress(Exception):
        _PUSHED_TITLES_FILE.write_text(json.dumps(pushed))


def _is_repeat_story(title: str, pushed: list[dict]) -> bool:
    """与近期已推送标题关键词重合度 > 阈值 → 视为同一故事重复, 抑制推送."""
    return any(_keyword_overlap(title, p["title"]) > _MIN_REPEAT_OVERLAP for p in pushed)


def _title_hash(title: str) -> str:
    return hashlib.md5(title.strip().encode()).hexdigest()[:12]


def _short_time(alert: dict) -> str:
    """从告警提取短时间 (HH:MM 或空). 优先 ts 转北京时间, 其次解析原始 time 字段."""
    ts = alert.get("ts")
    if ts:
        return datetime.fromtimestamp(ts, BEIJING).strftime("%H:%M")
    m = re.search(r"\d{1,2}:\d{2}", alert.get("time", ""))
    return m.group(0) if m else ""


def format_news_alerts(alerts: list[dict], gold_price: float = 0, gold_change: float = 0) -> str:
    """格式化新闻告警为微信卡片."""
    if not alerts:
        return ""

    p0 = [a for a in alerts if a["level"] == "P0"]
    p1 = [a for a in alerts if a["level"] == "P1"]

    if not p0 and not p1:
        return ""

    lines = ["📰 突发新闻预警", ""]

    # 行情背景
    if gold_price > 0:
        emoji = "🔴" if gold_change < 0 else "🟢"
        lines.append(f"当前{symbol_cn('XAUUSD')}: {gold_price:.0f} 美元 ({emoji} {gold_change:+.1f}%)")

    categories = {
        "geopolitical": "地缘冲突",
        "fed": "美联储",
        "macro": "宏观数据",
        "market": "极端行情",
        "energy": "能源危机",
        "central_bank": "央行动向",
        "trade": "贸易制裁",
        "policy": "政策变动",
    }

    def _impact_line(a: dict) -> str:
        """生成 💡 [方向·程度] 因果链 一行 (方向 emoji 前缀)."""
        emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(
            a.get("direction", "neutral"), "⚪"
        )
        return f"    💡 {emoji}[{a.get('label', '中性·轻度')}] {a.get('impact', '')}"

    if p0:
        lines.append("")
        lines.append("🚨 重大突发:")
        for a in p0:
            cat = categories.get(a.get("category", ""), "")
            t = _short_time(a)
            tag = f"{cat} {t}" if t else cat
            lines.append(f"  • [{tag}] {a['title'][:80]}")
            lines.append(_impact_line(a))

    if p1:
        lines.append("")
        lines.append("⚠️ 关注:")
        for a in p1:
            cat = categories.get(a.get("category", ""), "")
            t = _short_time(a)
            tag = f"{cat} {t}" if t else cat
            lines.append(f"  • [{tag}] {a['title'][:80]}")
            lines.append(_impact_line(a))

    lines.append("")
    sources = sorted({a.get("source", "") for a in p0 + p1})
    now_str = datetime.now(BEIJING).strftime("%m-%d %H:%M")
    lines.append(f"📡 来源: {', '.join(sources)} | 🕐 {now_str}")
    return "\n".join(lines)


def run_news_check() -> str:
    """执行一次新闻检查, 返回需推送的消息 (空=无异动).

    包含市场联动: 先获取金价, 再分析新闻.
    """
    # 获取当前金价 (用于市场联动)
    gold_price = 0.0
    gold_change = 0.0
    try:
        from .quotes import _from_sina

        xau = _from_sina()
        if xau:
            gold_price = xau["price"]
            gold_change = xau["change_pct"]
    except Exception:
        pass

    headlines = fetch_gold_headlines()
    alerts = analyze_headlines(headlines, gold_change_pct=gold_change)

    # ── 跨运行去重 (2026-08-13): 抑制与近期已推送故事重复的告警 ──
    # 同一事件多源标题措辞不同 → MD5 不命中 → 每 10 分钟重复告警, 叠加 iLink 限流.
    pushed = _load_pushed()
    fresh = [a for a in alerts if not _is_repeat_story(a["title"], pushed)]
    if len(fresh) != len(alerts):
        logger.info(f"[news_monitor] 跨运行去重: {len(alerts)}→{len(fresh)} 条 (抑制重复故事)")
    if fresh:
        now_ts = time.time()
        _save_pushed([p for p in pushed if now_ts - p["ts"] < _PUSHED_TTL]
                     + [{"title": a["title"], "ts": now_ts} for a in fresh])

    message = format_news_alerts(fresh, gold_price=gold_price, gold_change=gold_change)

    # AI 判定层健康告警 (2026-08-11): 回退超阈值 → 即使无新闻也推送,
    # 让用户知晓当前推送为规则判定, 避免静默降级 (事故: 8/10晚 5 条规则判定误判推送).
    health = _ai_health_warning()
    if health:
        message = f"{message}\n\n{health}" if message else health
    return message
