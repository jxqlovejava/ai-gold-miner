"""经济日历信号 — 未来高影响事件提醒.

不输出交易方向，只作为风险提醒注入 scan 报告，
用于触发 r004「数据前不重仓」等军规。
"""

from dataclasses import dataclass
from datetime import datetime

from loguru import logger

from gold_miner.data.calendar import EventCalendar, EventImpact
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength


@dataclass
class EconomicCalendarConfig:
    """经济日历信号配置."""

    days_ahead: int = 14
    min_impact: EventImpact = EventImpact.HIGH
    warn_within_hours: int = 48  # 48h 内触发 r004 提醒


class EconomicCalendarSignalGenerator:
    """未来高影响经济事件提醒生成器."""

    def __init__(self, config: EconomicCalendarConfig | None = None) -> None:
        self.config = config or EconomicCalendarConfig()
        self.calendar = EventCalendar()

    def generate_signals(self) -> list[Signal]:
        """生成未来事件提醒信号."""
        signals: list[Signal] = []
        now = datetime.now()
        try:
            if not self.calendar.events:
                self.calendar.load_fixed_calendar()
            upcoming = self.calendar.get_upcoming(
                days=self.config.days_ahead,
                min_impact=self.config.min_impact,
                reference_time=now,
            )
        except Exception as e:
            logger.debug(f"经济日历加载失败: {e}")
            return signals

        for event in upcoming:
            delta = event.scheduled_at - now
            days_until = max(0, delta.days)
            hours_until = max(0, delta.total_seconds() / 3600)
            strength = self._impact_to_strength(event.impact)

            when_desc = (
                "今天" if days_until == 0
                else "明天" if days_until == 1
                else f"{days_until}天后"
            )

            signals.append(
                Signal(
                    name=f"未来事件: {event.name}",
                    dimension="event_calendar",
                    direction=SignalDirection.NEUTRAL,
                    strength=strength,
                    score=0.0,
                    description=(
                        f"{when_desc} {event.scheduled_at.strftime('%m-%d %H:%M')} "
                        f"| 来源: {event.source}"
                    ),
                    metadata={
                        "event_type": event.event_type.value,
                        "impact": event.impact.value,
                        "scheduled_at": event.scheduled_at.isoformat(),
                        "days_until": days_until,
                        "hours_until": round(hours_until, 1),
                        "source": event.source,
                        "source_tier": "T0",
                    },
                )
            )

            if hours_until <= self.config.warn_within_hours:
                signals.append(
                    Signal(
                        name="r004 数据前不重仓提醒",
                        dimension="event_calendar",
                        direction=SignalDirection.NEUTRAL,
                        strength=SignalStrength.STRONG,
                        score=0.0,
                        description=(
                            f"{event.name} 将在 {int(hours_until)} 小时内公布，"
                            f"数据前避免新建 >10% 仓位"
                        ),
                        metadata={
                            "rule_id": "r004",
                            "event_name": event.name,
                            "hours_until": round(hours_until, 1),
                            "impact": event.impact.value,
                            "source_tier": "doctrine",
                        },
                    )
                )

        return signals

    @staticmethod
    def _impact_to_strength(impact: EventImpact) -> SignalStrength:
        if impact == EventImpact.HIGH:
            return SignalStrength.STRONG
        if impact == EventImpact.MEDIUM:
            return SignalStrength.MODERATE
        return SignalStrength.WEAK
