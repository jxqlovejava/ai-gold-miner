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

BEIJING = timezone(timedelta(hours=8))
_DEDUP_FILE = Path("/tmp/gold_news_dedup.json")
_DEDUP_TTL = 21600  # 6 小时去重
_NEWS_MAX_AGE = 7200  # 仅 2h 内新闻
_MIN_KEYWORD_OVERLAP = 0.6  # 去重: 关键词重合度阈值

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
    r"不确定|未定|待定|观望|静观",
    # 方向矛盾信号: 协议已谈成但溢价回吐/利好出尽 = 短期利空 vs 长期利多对冲 → 中性
    r"溢价回吐|回吐|获利了结|利多出尽|利好兑现|sell.?the.?news|买预期卖事实",
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
            ContextOverride(
                r"协议|达成|停火|缓和|重开|重放|开放|谈判.*进展|原则.*同意|签署|明朗",
                "bullish",
                "major",
                "霍尔木兹协议/缓和→供应危机缓解→油价↓→通胀↓→降息预期↑→利多金价",
            ),
            # 无封锁/袭击动作词的"提及式"标题 → 不标利多, 交未落地/中性处理
            ContextOverride(
                r"(?:交易员|投资者|市场).*(?:等待|关注|观望|留意).*霍尔木兹",
                "neutral",
                "minor",
                "霍尔木兹局势未落地(等待/观望)→方向未定，等待明确信号",
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
                r"协议|达成|停火|缓和|重开|开放|谈判.*进展|原则.*同意|签署|明朗",
                "bullish",
                "major",
                "红海/胡塞缓和→供应中断缓解→油价↓→通胀↓→降息预期↑→利多金价",
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
        pattern=r"科威特|巴林|卡塔尔|约旦|阿联酋|沙特.*遭.*袭击",
        category="geopolitical",
        priority="P1",
        direction="bullish",
        severity="moderate",
        reason="冲突外溢→区域不稳定→避险买盘→利多金价",
    ),
    # ═ 地缘降级 (独立规则, 供共用) ═
    ImpactRule(
        pattern=r"协议|达成|停火|和谈|谈判.*进展|原则.*同意|签署",
        category="geopolitical",
        priority="P0",
        direction="bullish",
        severity="major",
        reason="地缘降级/协议达成→战争溢价回吐→油价↓→通胀↓→降息预期↑→利多金价",
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
        pattern=r"就业.*崩|失业.*飙升|非农.*大跌|非农.*不及预期",
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
                        "level": rule.priority,
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
        settings.news_llm_categories
    )
    routed = [c for c in strict if c.get("category") in routed_categories] + broad
    llm_results: dict[str, dict] = {}
    if routed and getattr(analyzer, "enabled", False):
        try:
            llm_results = analyzer.classify_many(routed) or {}
        except Exception:
            logger.exception("语义推理异常, 回退关键词规则")
            llm_results = {}

    # ── Stage 3: 校验与降级, 生成最终告警 ──
    alerts: list[dict] = []

    def _finalize(c: dict, llm: dict | None) -> dict | None:
        """合并 LLM 字段并应用确定性守卫. 返回告警 dict 或 None(丢弃)."""
        conf = llm.get("confidence", 0.0) if llm else 0.0
        if llm is not None and conf >= _LLM_CONF_THRESHOLD:
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
        lines.append(f"当前 XAUUSD: ${gold_price:.0f} ({emoji} {gold_change:+.1f}%)")

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
    return format_news_alerts(alerts, gold_price=gold_price, gold_change=gold_change)
