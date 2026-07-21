"""经济日历信号 — 未来高影响事件提醒.

不输出交易方向，只作为风险提醒注入 scan 报告，
用于触发 r004「数据前不重仓」等军规。

所有事件的 scheduled_at 存储为美东时间（US Eastern）。
输出时自动转换为北京时间（UTC+8）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

from loguru import logger

from gold_miner.data.calendar import EventCalendar, EventImpact
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength

# 北京时间 = UTC+8
_BEIJING_TZ = timezone(timedelta(hours=8))


def _is_us_dst(dt: datetime) -> bool:
    """美东夏令时 (EDT, UTC-4): 3月第二个周日 – 11月第一个周日."""
    # 若传入 aware datetime，先转为 naive 再比较（月/日边界用本地时间即可）
    naive_dt = dt.replace(tzinfo=None) if dt.tzinfo is not None else dt
    # 3月第二个周日
    mar_first = datetime(naive_dt.year, 3, 1)
    mar_second_sun = mar_first + timedelta(days=(6 - mar_first.weekday() + 7) % 7 + 7)
    # 11月第一个周日
    nov_first = datetime(naive_dt.year, 11, 1)
    nov_first_sun = nov_first + timedelta(days=(6 - nov_first.weekday() + 7) % 7)
    return mar_second_sun <= naive_dt < nov_first_sun


@dataclass
class EconomicCalendarConfig:
    """经济日历信号配置."""

    days_ahead: int = 14
    min_impact: EventImpact = EventImpact.HIGH
    warn_within_hours: int = 48  # 48h 内触发 r004 提醒


class EconomicCalendarSignalGenerator:
    """未来高影响经济事件提醒生成器.

    存储时间 = 美东时间 (UTC-5)，展示时间 = 北京时间 (UTC+8)。
    """

    def __init__(self, config: EconomicCalendarConfig | None = None) -> None:
        self.config = config or EconomicCalendarConfig()
        self.calendar = EventCalendar()

    @staticmethod
    def _to_beijing(et_dt: datetime) -> datetime:
        """美东时间 → 北京时间.

        自动检测夏令时：EDT(UTC-4) 或 EST(UTC-5)。
        """
        offset_hours = -4 if _is_us_dst(et_dt) else -5
        et_tz = timezone(timedelta(hours=offset_hours))
        return et_dt.replace(tzinfo=et_tz).astimezone(_BEIJING_TZ)

    @staticmethod
    def _fmt_beijing(et_dt: datetime) -> str:
        """美东时间 → 北京时间字符串，如 '07-09 02:00 (周四)'."""
        bj = EconomicCalendarSignalGenerator._to_beijing(et_dt)
        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        return f"{bj.strftime('%m-%d %H:%M')} ({weekdays[bj.weekday()]})"

    def generate_signals(self) -> list[Signal]:
        """生成未来事件提醒信号."""
        signals: list[Signal] = []
        now = datetime.now(tz=UTC)
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
            # 双列钟点: 禁止只展示北京 (2026-07-15 听证误判事故)
            from gold_miner.data.calendar_time_rules import dual_clock_str

            clock = dual_clock_str(event.scheduled_at)

            signals.append(
                Signal(
                    name=f"未来事件: {event.name}",
                    dimension="event_calendar",
                    direction=SignalDirection.NEUTRAL,
                    strength=strength,
                    score=0.0,
                    description=(
                        f"{when_desc} {clock} "
                        f"| 来源: {event.source}"
                    ),
                    metadata={
                        "event_type": event.event_type.value,
                        "impact": event.impact.value,
                        "scheduled_at": event.scheduled_at.isoformat(),
                        "scheduled_at_beijing": self._to_beijing(event.scheduled_at).isoformat(),
                        "dual_clock": clock,
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
