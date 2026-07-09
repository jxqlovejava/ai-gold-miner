"""近期事件结果时效性加权信号.

从事件日历中读取最近已发布且有实际结果的事件，
按发布时间衰减加权，生成时效性信号注入分析管线。

时效性衰减规则:
  <24h   → weight=1.0  市场正在定价中
  24-48h → weight=0.7  已大部分消化
  48-72h → weight=0.5  影响递减中
  3-7d   → weight=0.3  已基本定价，仅作背景参考
  >7d    → 不纳入
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from loguru import logger

from gold_miner.data.calendar import EventCalendar, EventType
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength


@dataclass
class RecencyWeightConfig:
    """时效性衰减配置."""

    lookback_days: int = 7
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


def _infer_direction_from_event(name: str, actual: str, forecast: str | None) -> SignalDirection:
    """根据事件实际结果推断对金价的信号方向.

    基于关键词匹配做快速推断，复杂判断由 AI 分析补充。
    """
    actual_lower = actual.lower()
    forecast_lower = (forecast or "").lower()

    # 鹰派/加息信号 → 利空黄金
    hawkish_keywords = ["加息", "鹰派", "hike", "hawkish", "收紧", "tighten"]
    if any(kw in actual_lower for kw in hawkish_keywords):
        return SignalDirection.BEARISH

    # 鸽派/降息信号 → 利多黄金
    dovish_keywords = ["降息", "鸽派", "cut", "dovish", "宽松", "ease"]
    if any(kw in actual_lower for kw in dovish_keywords):
        return SignalDirection.BULLISH

    # 数据低于预期 → 经济弱 → 利多黄金（对非农/PMI/零售等）
    weak_keywords = ["低于", "不及", "miss", "below", "下滑", "放缓", "下降", "减少"]
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

        从日历中读取最近 lookback_days 内有 actual 的事件，
        按发布时间衰减产生权重，注入第一步信号采集。
        """
        self._ensure_loaded()
        events = self.calendar.get_recent_events_with_results(
            lookback_days=self.config.lookback_days,
        )

        if not events:
            logger.debug("近期无已发布事件结果")
            return []

        signals: list[Signal] = []
        now = datetime.now()

        for event in events:
            hours_ago = (now - event.scheduled_at).total_seconds() / 3600
            weight = self.config.compute_weight(hours_ago)

            if weight <= 0:
                continue

            direction = _infer_direction_from_event(
                event.name,
                event.actual or "",
                event.forecast,
            )
            strength = _infer_strength_from_weight(weight)

            # 得分 = 方向符号 × 权重
            dir_sign = {SignalDirection.BULLISH: 1.0, SignalDirection.BEARISH: -1.0, SignalDirection.NEUTRAL: 0.0}
            score = dir_sign.get(direction, 0.0) * weight

            hours_desc = f"{hours_ago:.0f}h前" if hours_ago < 72 else f"{hours_ago/24:.0f}天前"

            description_parts = [
                f"{hours_desc} | 权重{weight:.1f}",
            ]
            if event.actual:
                description_parts.append(f"实际: {event.actual}")
            if event.forecast:
                description_parts.append(f"预期: {event.forecast}")

            signals.append(
                Signal(
                    name=f"近期事件: {event.name}",
                    dimension="recent_events",
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
