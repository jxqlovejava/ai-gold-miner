"""近期事件结果时效性加权信号.

从事件日历中读取最近已发布且有实际结果的事件，
按发布时间衰减加权，生成时效性信号注入分析管线。

时效性衰减规则:
  <24h   → weight=1.0  市场正在定价中
  24-48h → weight=0.7  已大部分消化
  48-72h → weight=0.5  影响递减中
  3-7d   → weight=0.3  已基本定价，仅作背景参考
  >7d    → 不纳入

初请/续请失业金方向判定 (2026-08-11 增强):
  初请是反向指标 — 申请人数低 = 劳动力强 = 偏鹰 = 利空黄金。
  但「劳动力稳健」不能只看与预期的差距，必须综合:
    1. 预期差 (低于预期 → 偏鹰)
    2. 环比涨幅 (环比上升 = 边际恶化 → 利多, 抵消"低于预期"的鹰派解读)
    3. 分母幻觉 (参与率下降时, 低初请部分反映退出者不再申领, 不代表雇佣强劲)
  参见 _infer_claims_direction。
"""

from __future__ import annotations

import re

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

from loguru import logger

from gold_miner.data.calendar import EventCalendar
from gold_miner.direction_lexicon import infer_rate_expectation_direction
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength


@dataclass
class RecencyWeightConfig:
    """时效性衰减配置."""

    lookback_days: int = 7
    staleness_penalty: float = 0.5   # fast-evolving 事件过时权重乘数
    weights: list[tuple[float, float]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.weights is None:
            # (hours_threshold, weight) — 从低到高排列
            self.weights = [
                (24, 1.0),
                (48, 0.7),
                (72, 0.5),
                (168, 0.3),  # 7 days
            ]

    def compute_weight(self, hours_ago: float) -> float:
        """根据距今小时数计算衰减权重."""
        for threshold, weight in self.weights:
            if hours_ago <= threshold:
                return weight
        return 0.0


# ---------------------------------------------------------------------------
# 事件→信号方向映射
# ---------------------------------------------------------------------------

# 复杂对冲事件标记 — actual 里分析师已显式写下双向推理 (一阶/二阶/对冲/双向/取中性),
# 或文本同时含利多+利空方向信号 → 关键词推断不可靠, 跳过 naive 冲突告警。
# 事故: 2026-08-26 Flash PMI(写neutral, 推断bullish因'加息概率回落')与
#        国债回购(写bullish, 推断bearish因二阶风险注'鹰派加息')两连假阳性待复核。
_HEDGED_MARKERS = ("对冲", "双向", "取中性", "一阶", "二阶", "双通道", "两因素")


def _is_hedged_reasoning(actual: str) -> bool:
    """actual 是否已显式包含双向对冲推理.

    关键词推断无法解析「一阶利多 + 二阶利空 → 取中性」这类复合推理,
    命中标记时跳过 naive 冲突告警, 避免复杂事件反复出现假阳性待复核。
    """
    low = (actual or "").lower()
    if not low:
        return False
    if any(m in low for m in _HEDGED_MARKERS):
        return True
    # 双向信号并存也视为对冲 (利多+利空 或 看多+看空 同时出现)
    has_bull = any(k in low for k in ("利多", "看多", "bullish", "利好"))
    has_bear = any(k in low for k in ("利空", "看空", "bearish", "利淡"))
    return has_bull and has_bear


def _infer_direction_from_event(
    name: str,
    actual: str,
    forecast: str | None,
    previous: str | None = None,
    gold_bias: str | None = None,
) -> tuple[SignalDirection, str | None]:
    """根据事件实际结果推断对金价的信号方向.

    优先级:
      1. 写入时同步判定的 gold_bias (信息最全处的显式判断, 覆盖组合语义/指标极性)
      2. 关键词匹配 fallback (快速推断, 复杂判断由 AI 分析补充)

    初请/续请类事件优先走 _infer_claims_direction 综合判定 (预期差+环比+分母幻觉),
    而非裸关键词 — 裸关键词会漏判环比方向 (事故 2026-08-11)。

    两者都存在且关键词给出非中性的相反方向时, 返回冲突说明 — 调用方应生成
    待复核告警信号, 把隐性误判变成显性告警 (事故见 .learnings/2026-08-10)。

    Returns:
        (direction, conflict_note): conflict_note 为 None 表示无冲突。
    """
    keyword_direction = _infer_claims_direction(name, actual, forecast, previous)
    if keyword_direction is None:
        keyword_direction = _infer_direction_by_keywords(actual)

    explicit: SignalDirection | None = None
    if gold_bias in ("bullish", "bearish", "neutral"):
        explicit = SignalDirection(gold_bias)

    if explicit is None:
        return keyword_direction, None

    conflict: str | None = None
    if (
        keyword_direction is not SignalDirection.NEUTRAL
        and keyword_direction is not explicit
        and not _is_hedged_reasoning(actual)
    ):
        conflict = (
            f"写入判定={explicit.value} 与关键词推断={keyword_direction.value} 冲突, "
            f"以写入判定为准, 但需人工复核 actual 表述是否误导"
        )
    return explicit, conflict


# ---------------------------------------------------------------------------
# 初请/续请失业金综合方向判定 (2026-08-11 新增)
# ---------------------------------------------------------------------------

_CLAIMS_MARKERS = ("初请", "续请", "失业金", "claims", "jobless")

_DENOMINATOR_MARKERS = ("参与率", "退出", "劳动力市场萎缩", "分母", "退出劳动")


def _is_claims_event(name: str) -> bool:
    """判断事件是否为初请/续请失业金类 (反向指标)."""
    return any(m in name.lower() for m in _CLAIMS_MARKERS)


def _extract_number(text: str | None) -> float | None:
    """从文本中提取首个数字 (支持 '19.9万'/'208K'/'19.9' 等)."""
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*[万Kk]?", text)
    if not m:
        return None
    return float(m.group(1))


def _infer_claims_direction(
    name: str,
    actual: str,
    forecast: str | None,
    previous: str | None,
) -> SignalDirection | None:
    """初请/续请失业金综合方向判定 — 反向指标的完整推导.

    初请是反向指标: 申请人数低 = 劳动力强 = 偏鹰 = 利空黄金 (bearish)。
    但「劳动力稳健」的判定不能只看与预期的差距, 必须综合三个维度:

      1. 预期差 (actual vs forecast):
         - 低于预期 → 就业强 → 偏鹰 → bearish
         - 高于预期 → 就业弱 → 偏鸽 → bullish
      2. 环比涨幅 (actual vs previous):
         - 环比上升 → 边际恶化 → 利多黄金 (抵消"低于预期"的鹰派解读)
         - 环比下降 → 边际改善 → 利空黄金
      3. 分母幻觉 (参与率下降):
         - 参与率下降导致初请低, 不代表雇佣强劲 → 削弱 bearish 权重

    返回 None 表示非初请事件或无法解析数字, 调用方回退通用关键词路径。

    Args:
        name: 事件名称
        actual: 实际结果文本
        forecast: 预期值 (可含单位, 如 "20.2万")
        previous: 前值 (可含单位, 如 "19.8万")
    """
    if not _is_claims_event(name):
        return None

    a = _extract_number(actual)
    f = _extract_number(forecast)
    p = _extract_number(previous)
    if a is None:
        return None

    # 反向指标基线: 预期差方向
    base_dir: SignalDirection | None = None
    if f is not None and f > 0:
        base_dir = (
            SignalDirection.BEARISH if a < f
            else SignalDirection.BULLISH if a > f
            else None
        )

    # 分母幻觉: 参与率下降/退出劳动力市场 → 削弱 bearish
    denominator_illusion = any(m in actual.lower() for m in _DENOMINATOR_MARKERS)

    # 综合判定
    if p is not None and p > 0:
        if a > p:
            # 环比上升 = 边际恶化 → 利多黄金, 与"低于预期"的鹰派解读对冲
            if denominator_illusion:
                return SignalDirection.BULLISH  # 环比升 + 分母幻觉 → 明确利多
            if base_dir is SignalDirection.BEARISH:
                return SignalDirection.NEUTRAL  # 低于预期但环比升 → 对冲
            return SignalDirection.BULLISH
        if a < p:
            # 环比下降 = 边际改善 → 利空黄金
            return SignalDirection.BEARISH if base_dir is not SignalDirection.BULLISH else SignalDirection.NEUTRAL

    # 无 previous (无法判环比) 时退化为预期差判定
    if denominator_illusion and base_dir is SignalDirection.BEARISH:
        return SignalDirection.NEUTRAL  # 分母幻觉削弱鹰派解读
    return base_dir


def _infer_direction_by_keywords(actual: str) -> SignalDirection:
    """关键词匹配推断 (fallback 路径, 仅在 gold_bias 缺失时使用)."""
    actual_lower = actual.lower()

    # 利率预期反转构式优先: "加息概率走低"=收紧预期↓→利多; "降息概率走低"=宽松预期↓→利空
    # 须先于裸'加息/降息'子串检查, 否则 '加息' 子串先命中错误方向 (事故 2026-08-10)
    reversal = infer_rate_expectation_direction(actual_lower)
    if reversal == "bullish":
        return SignalDirection.BULLISH
    if reversal == "bearish":
        return SignalDirection.BEARISH

    # 鹰派/加息信号 → 利空黄金
    hawkish_keywords = ["加息", "鹰派", "hike", "hawkish", "收紧", "tighten"]
    if any(kw in actual_lower for kw in hawkish_keywords):
        return SignalDirection.BEARISH

    # 鸽派/降息信号 → 利多黄金
    dovish_keywords = ["降息", "鸽派", "cut", "dovish", "宽松", "ease"]
    if any(kw in actual_lower for kw in dovish_keywords):
        return SignalDirection.BULLISH

    # 数据低于预期 → 经济弱 → 利多黄金（对非农/PMI/零售等）
    # 含"下修"(前值下修=经济比此前认知更弱)、"负增/萎缩/裁员"(就业数据负值场景)、
    # "爆冷"(数据远逊预期的媒体措辞)。注意: 失业率下降若由参与率下降驱动(分母幻觉),
    # 属疲弱而非强劲——关键词层无法识别该组合, 写入 actual 时应注明"参与率下降"。
    weak_keywords = [
        "低于", "不及", "miss", "below", "下滑", "放缓", "下降", "减少",
        "下修", "负增", "萎缩", "裁员", "爆冷",
    ]
    if any(kw in actual_lower for kw in weak_keywords):
        return SignalDirection.BULLISH

    # 数据高于预期 → 经济强 → 利空黄金
    strong_keywords = ["高于", "超预期", "beat", "above", "上升", "加速", "增长"]
    if any(kw in actual_lower for kw in strong_keywords):
        return SignalDirection.BEARISH

    # 中性 / 基本符合预期
    neutral_keywords = ["符合预期", "持平", "不变", "维持", "in line", "unchanged"]
    if any(kw in actual_lower for kw in neutral_keywords):
        return SignalDirection.NEUTRAL

    # 默认为 NEUTRAL（AI 分析时再覆盖）
    return SignalDirection.NEUTRAL


def _infer_strength_from_weight(weight: float) -> SignalStrength:
    """衰减权重 → 信号强度."""
    if weight >= 1.0:
        return SignalStrength.STRONG
    if weight >= 0.5:
        return SignalStrength.MODERATE
    return SignalStrength.WEAK


# ---------------------------------------------------------------------------
# 信号生成器
# ---------------------------------------------------------------------------


class RecentEventSignalGenerator:
    """近期事件结果时效性加权信号生成器.

    从 EventCalendar 获取最近已发布事件及其实际结果，
    按时效性衰减生成加权信号。
    """

    def __init__(
        self,
        calendar: EventCalendar | None = None,
        config: RecencyWeightConfig | None = None,
    ) -> None:
        self.calendar = calendar or EventCalendar()
        self.config = config or RecencyWeightConfig()

    def generate_signals(self) -> list[Signal]:
        """生成时效性加权信号.

        1. 从日历中读取最近 lookback_days 内有 actual 的事件，按时效性衰减加权。
        2. 同时检查已发布但 actual 为空的事件（24h 内），生成「待查结果」警告信号，
           避免关键数据发布后被静默忽略。
        """
        self._ensure_loaded()
        now = datetime.now(tz=UTC)

        events = self.calendar.get_recent_events_with_results(
            lookback_days=self.config.lookback_days,
        )

        signals: list[Signal] = []

        # ── 已发布但 actual 为空的事件（24h 内）──
        # 这些是"刚发布还没人填结果"的关键数据，必须提醒用户去查
        pending_events = self.calendar.get_recently_published_without_result(
            lookback_days=1,  # 只看 24h 内的，超过 24h 还没填的优先级降低
        )
        # 过滤掉 monitor 类型（monitor 本身不需要 actual）
        pending_data_events = [
            e for e in pending_events
            if e.event_type.value not in ("monitor",)
        ]
        if pending_data_events:
            event_names = "、".join(e.name for e in pending_data_events)
            signals.append(
                Signal(
                    name=f"⚠️ 待查结果: {event_names}",
                    dimension="event",
                    direction=SignalDirection.NEUTRAL,
                    strength=SignalStrength.STRONG,
                    score=0.0,
                    description=(
                        f"{len(pending_data_events)}个事件已发布但未同步实际结果，"
                        f"请立即搜索权威来源获取结果并更新日历"
                    ),
                    metadata={
                        "event_type": "pending_result_sync",
                        "pending_count": len(pending_data_events),
                        "pending_events": [
                            {
                                "name": e.name,
                                "scheduled_at": e.scheduled_at.isoformat(),
                                "forecast": e.forecast,
                                "source": e.source,
                            }
                            for e in pending_data_events
                        ],
                        "source_tier": "system",
                    },
                )
            )

        if not events:
            logger.debug("近期无已发布事件结果")
            return signals

        for event in events:
            hours_ago = (now - event.scheduled_at).total_seconds() / 3600
            weight = self.config.compute_weight(hours_ago)

            if weight <= 0:
                continue

            # 对 fast-evolving 事件应用过时惩罚
            staleness_risk = event.needs_reverify
            if staleness_risk:
                weight *= self.config.staleness_penalty
                if weight <= 0:
                    continue

            direction, conflict = _infer_direction_from_event(
                event.name,
                event.actual or "",
                event.forecast,
                previous=event.previous,
                gold_bias=getattr(event, "gold_bias", None),
            )
            if conflict:
                signals.append(
                    Signal(
                        name=f"⚠️ 方向冲突待复核: {event.name}",
                        dimension="event",
                        direction=SignalDirection.NEUTRAL,
                        strength=SignalStrength.MODERATE,
                        score=0.0,
                        description=conflict,
                        metadata={
                            "event_type": "gold_bias_conflict",
                            "event_name": event.name,
                            "gold_bias": getattr(event, "gold_bias", None),
                            "actual": event.actual,
                        },
                    )
                )
            strength = _infer_strength_from_weight(weight)

            # 得分 = 方向符号 × 权重
            dir_sign = {SignalDirection.BULLISH: 1.0, SignalDirection.BEARISH: -1.0, SignalDirection.NEUTRAL: 0.0}
            score = dir_sign.get(direction, 0.0) * weight

            hours_desc = f"{hours_ago:.0f}h前" if hours_ago < 72 else f"{hours_ago/24:.0f}天前"

            # 格式化事件发生时间: ET + 北京双列
            et_str = event.scheduled_at.strftime("%m/%d %H:%M ET")
            bj_dt = event.scheduled_at.astimezone(timezone(timedelta(hours=8)))
            bj_str = bj_dt.strftime("%m/%d %H:%M 北京")

            description_parts = [
                f"🕐 {et_str} ({bj_str}) | {hours_desc} | 权重{weight:.1f}",
            ]
            if event.actual:
                description_parts.append(f"实际: {event.actual}")
            if event.forecast:
                description_parts.append(f"预期: {event.forecast}")

            signals.append(
                Signal(
                    name=f"近期事件: {event.name}",
                    dimension="event",
                    direction=direction,
                    strength=strength,
                    score=score,
                    description=" | ".join(description_parts),
                    metadata={
                        "event_type": event.event_type.value,
                        "hours_ago": round(hours_ago, 1),
                        "recency_weight": weight,
                        "actual": event.actual,
                        "forecast": event.forecast,
                        "scheduled_at": event.scheduled_at.isoformat(),
                        "source": event.source,
                        "staleness_risk": staleness_risk,
                    },
                )
            )

        # 按时效性排序（最新的在前）
        signals.sort(key=lambda s: s.metadata.get("hours_ago", 999))

        logger.info(
            f"[RecentEvents] {len(events)}个事件 → {len(signals)}个信号 "
            f"(权重范围: {min(s.metadata.get('recency_weight', 0) for s in signals):.1f}-"
            f"{max(s.metadata.get('recency_weight', 0) for s in signals):.1f})"
        )
        return signals

    def _ensure_loaded(self) -> None:
        if not self.calendar.events:
            self.calendar.load_fixed_calendar()
