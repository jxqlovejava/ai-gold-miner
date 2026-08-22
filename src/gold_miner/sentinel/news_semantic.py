"""突发新闻 AI 语义推理 — 判定金价传导链.

三层架构的 Stage 2:
  Stage 1 关键词快筛 (news_monitor._HIGH_IMPACT_RULES) 选出候选
  Stage 2 本模块: DeepSeek 批量语义推理, 输出 方向/程度/优先级/类目/传导链
  Stage 3 news_monitor 校验与降级 (枚举白名单 + confidence 门槛 + 回退 regex)

设计约束:
  - 仅处理语义模糊类目 (geopolitical/energy/trade/policy/election),
    确定性经济类目 (fed/macro/market) 仍走 regex, 不引入幻觉风险.
  - 单次批量调用控成本; 失败/非法输出返回 {} → 调用方回退 regex.
  - 客户端已处理 thinking 块, 此处只拿 text.
"""

from __future__ import annotations

from loguru import logger

from gold_miner.config import settings
from gold_miner.llm.client import LLMClient

# ── 枚举白名单 ──
DIRECTIONS = ("bullish", "bearish", "neutral")
SEVERITIES = ("major", "moderate", "minor")
PRIORITIES = ("P0", "P1", "P2")
CATEGORIES = (
    "geopolitical",
    "fed",
    "macro",
    "market",
    "energy",
    "central_bank",
    "trade",
    "policy",
    "election",
)

# ── 系统提示: 金价传导框架 + 语义判别规则 + few-shot ──
_SYSTEM_FRAMEWORK = """你是一个黄金市场突发新闻语义分析员。给定若干条中文新闻标题，逐条判定其对金价的影响方向、程度、优先级与完整传导链。

## 金价影响传导框架（多通道，必须综合判断）
1. 避险/战争溢价：地缘冲突升级→避险买盘+战争溢价↑→利多金价（勿标利空）
2. 咽喉/供应：霍尔木兹/红海等关键航道封锁或通行限制→供应中断风险→油价↑→通胀↑；对黄金短期利多（避险+抗通胀），但若油价持续上行推升加息预期则远期承压
3. 缓和/协议：地缘缓和、协议达成→短期战争溢价回吐（利空）vs 长期油价↓→降息预期↑（利多）→ 方向矛盾，链中须标注此矛盾
4. 利率硬规则：降息→实际利率↓→利多；加息→实际利率↑→利空
5. 通胀/就业：CPI/通胀/非农超预期→加息压力↑→利空；回落/疲软→利多
6. 央行：央行购金/去美元化→长期利多

## 语义判别规则（严格遵循）
1. 出现"协议"字样≠缓和！判别依据是动作动词：
   - 缓和词：达成、签署、停火、缓和、重开、开放、谈判取得进展、原则同意
   - 升级/限制词：不得、禁止、不允许、封锁、关闭、袭击、轰炸、导弹、威胁、开火、战争
   - 例："伊朗披露阿曼协议细节：美国和以色列船只不得通过霍尔木兹海峡" 是【通行限制/升级】，不是缓和
2. 纯提及/关注/分析/预测/疑问（无实质动作）→ is_real_event=false，不告警
3. 未落地（等待/谈判中/未签署/悬而未决/观望/待定）→ is_pending=true，方向中性
4. 否定/减弱句（不会/否认/取消/推迟/暂不/削弱/降温/回落/放缓/减弱/降低 + 预期）→ 按减弱后的实际含义判断方向，方向反转：
   '削弱/降温/回落加息预期' = 加息预期↓ = 实际利率预期↓ = 利多（不是利空）；'削弱/降温/回落降息预期' = 降息预期↓ = 实际利率预期↑ = 利空；拿不准判中性
5. 传导链必须完整：原因→机制→对金价方向(含caveat)，禁止只给方向不给链

## 输出格式（严格 JSON，不要任何多余文字）
{"results":[{"index":1,"headline":"原始标题原文","is_real_event":true/false,"is_pending":true/false,"direction":"bullish|bearish|neutral","severity":"major|moderate|minor","priority":"P0|P1|P2","category":"geopolitical|fed|macro|market|energy|central_bank|trade|policy|election","transmission_chain":"完整传导链","confidence":0.0-1.0,"reasoning":"一句话依据"}]}

## 示例
输入: 伊阿霍尔木兹原则上达成协议 分道航行重开海峡
输出: {"index":1,"headline":"伊阿霍尔木兹原则上达成协议 分道航行重开海峡","is_real_event":true,"is_pending":false,"direction":"bullish","severity":"major","priority":"P0","category":"energy","transmission_chain":"霍尔木兹协议/缓和→供应中断缓解→油价↓→通胀↓→降息预期↑→利多金价(短期溢价回吐,长期降息利多)","confidence":0.9,"reasoning":"明确缓和动词'达成/重开'，走降息利多链并标注短期溢价回吐"}

输入: 伊朗披露阿曼协议细节：美国和以色列船只不得通过霍尔木兹海峡
输出: {"index":1,"headline":"伊朗披露阿曼协议细节：美国和以色列船只不得通过霍尔木兹海峡","is_real_event":true,"is_pending":false,"direction":"bullish","severity":"major","priority":"P0","category":"energy","transmission_chain":"霍尔木兹通行限制→供应中断风险→油价↑→利多金价(避险+抗通胀)；若油价↑持续推升加息预期则远期承压","confidence":0.85,"reasoning":"'不得通过'是限制/升级动作而非缓和，走供应中断利多链并附油价承压caveat"}

输入: 美股周四午盘走低，交易员关注伊朗局势
输出: {"index":1,"headline":"美股周四午盘走低，交易员关注伊朗局势","is_real_event":false,"is_pending":false,"direction":"neutral","severity":"minor","priority":"P2","category":"geopolitical","transmission_chain":"纯提及/关注，无实质动作→不告警","confidence":0.9,"reasoning":"仅'关注'提及伊朗，无冲突/升级动作"}"""


def _build_prompt(headlines: list[dict]) -> str:
    """组装批量推理 prompt. headlines 需含 title 字段, 编号从 1 起."""
    lines = "\n".join(f"{i}. {h['title']}" for i, h in enumerate(headlines, 1))
    return f"{_SYSTEM_FRAMEWORK}\n\n## 待分析新闻\n{lines}\n"


def _validate_item(item: dict) -> dict | None:
    """枚举白名单逐字段校验; 非法字段省略, 无任何实质字段则丢弃."""
    if not isinstance(item, dict):
        return None
    out: dict = {}
    has_field = False
    if isinstance(item.get("is_real_event"), bool):
        out["is_real_event"] = item["is_real_event"]
        has_field = True
    if isinstance(item.get("is_pending"), bool):
        out["is_pending"] = item["is_pending"]
        has_field = True
    if item.get("direction") in DIRECTIONS:
        out["direction"] = item["direction"]
        has_field = True
    if item.get("severity") in SEVERITIES:
        out["severity"] = item["severity"]
        has_field = True
    if item.get("priority") in PRIORITIES:
        out["priority"] = item["priority"]
        has_field = True
    if item.get("category") in CATEGORIES:
        out["category"] = item["category"]
        has_field = True
    chain = str(item.get("transmission_chain") or "").strip()
    if chain:
        out["transmission_chain"] = chain
        has_field = True
    if item.get("reasoning"):
        out["reasoning"] = str(item["reasoning"])[:200]
    if not has_field:
        return None
    try:
        conf = float(item.get("confidence", 0.0))
        out["confidence"] = max(0.0, min(1.0, conf))
    except (TypeError, ValueError):
        out["confidence"] = 0.0
    return out


class SemanticNewsAnalyzer:
    """突发新闻语义分析器 — 批量推理传导链.

    用法:
        analyzer = SemanticNewsAnalyzer()
        results = analyzer.classify_many(headlines)   # {title: {字段...}}
    """

    def __init__(
        self,
        client: LLMClient | None = None,
        categories: list[str] | None = None,
        max_headlines: int | None = None,
    ) -> None:
        self.client = client or LLMClient()
        # 分类任务用 flash (news_llm_model): 全局 pro 扩展思考吃 tokens, max_tokens=3000
        # 下 chat 常返回空 → 语义层静默禁用 → 规则误判 (事故 2026-08-22).
        if client is None:
            self.client.model = getattr(settings, "news_llm_model", "deepseek-v4-flash")
        # getattr 兜底: 部署非原子期间旧 config 可能缺 news_llm_* 字段, 避免夜间哨兵崩溃
        self.categories = set(
            categories
            or getattr(settings, "news_llm_categories", ["geopolitical", "energy", "trade", "policy", "election"])
        )
        self.max_headlines = max_headlines or getattr(settings, "news_llm_max_headlines", 12)

    @property
    def enabled(self) -> bool:
        """是否可用: 总开关 + 有 API key."""
        return bool(getattr(settings, "news_llm_enabled", True)) and self.client.enabled

    def classify_many(self, headlines: list[dict]) -> dict[str, dict]:
        """批量语义推理 → {原始标题: 校验后的结果字段}.

        仅处理路由类目; 失败/无结果返回 {} (调用方回退 regex).
        """
        if not self.enabled or not headlines:
            return {}
        # 仅过滤带类目的严格候选; category=None 的候选B (宽泛提及) 一律交 AI 裁决
        routed = [
            h for h in headlines
            if h.get("category") is None
            or h.get("category") in self.categories
            or h.get("escalate")
        ]
        if not routed:
            return {}
        routed = routed[: self.max_headlines]

        prompt = _build_prompt(routed)
        # max_tokens/timeout 放大: flash 无扩展思考, 但完整 framework prompt + 批量
        # JSON 输出仍需余量; 30s/3000 曾因 thinking 块吃满导致空返回 (2026-08-22).
        data = self.client.chat_json(prompt, timeout=60.0, max_tokens=4000)
        if not data:
            logger.warning("语义推理无返回, 回退关键词规则")
            return {}

        results = data.get("results")
        if not isinstance(results, list):
            logger.warning("语义推理结果非 list, 回退关键词规则")
            return {}

        # index → 标题 映射 (LLM 按编号回填, 避免标题转写漂移)
        index_map: dict[int, str] = {i: h["title"] for i, h in enumerate(routed, 1)}
        title_map: dict[str, str] = {h["title"]: h["title"] for h in routed}
        out: dict[str, dict] = {}
        for item in results:
            if not isinstance(item, dict):
                continue
            validated = _validate_item(item)
            if not validated:
                continue
            # 定位原始标题: 优先 index, 其次 exact title
            key = ""
            if isinstance(item.get("index"), int) and item["index"] in index_map:
                key = index_map[item["index"]]
            elif item.get("headline") in title_map:
                key = title_map[item["headline"]]
            if key and key not in out:
                out[key] = validated
        return out
