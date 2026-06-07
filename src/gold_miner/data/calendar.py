"""事件日历 — 美联储决议、CPI、非农等重要事件."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any


class EventImpact(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EventType(str, Enum):
    FED_RATE = "fed_rate"
    CPI = "cpi"
    PCE = "pce"
    NFP = "nfp"
    FOMC_MINUTES = "fomc_minutes"
    ECB = "ecb"
    BOE = "boe"
    GEO_POLITICAL = "geo"
    GOLD_RESERVE = "gold_reserve"


@dataclass
class CalendarEvent:
    name: str
    event_type: EventType
    scheduled_at: datetime
    impact: EventImpact
    actual: str | None = None
    forecast: str | None = None
    previous: str | None = None
    source: str = ""
    description: str = ""


class EventCalendar:
    """事件日历管理器."""

    TEMPLATE_EVENTS: list[dict[str, Any]] = [
        {"name": "FOMC利率决议", "type": EventType.FED_RATE, "impact": EventImpact.HIGH},
        {"name": "美国CPI", "type": EventType.CPI, "impact": EventImpact.HIGH},
        {"name": "非农就业", "type": EventType.NFP, "impact": EventImpact.HIGH},
        {"name": "核心PCE", "type": EventType.PCE, "impact": EventImpact.HIGH},
    ]

    def __init__(self) -> None:
        self.events: list[CalendarEvent] = []

    def load_fixed_calendar(self, year: int | None = None) -> list[CalendarEvent]:
        """加载已知的年度关键事件日期."""
        from datetime import datetime

        target = year or datetime.now().year
        events: list[CalendarEvent] = []

        # 2025 FOMC 会议日期
        fomc_2025 = [
            (1, 29), (3, 19), (5, 7), (6, 18),
            (7, 30), (9, 17), (11, 5), (12, 17),
        ]
        fomc_2026 = [
            (1, 28), (3, 18), (5, 6), (6, 17),
            (7, 29), (9, 16), (11, 4), (12, 16),
        ]
        fomc_dates = fomc_2025 if target == 2025 else fomc_2026
        for month, day in fomc_dates:
            events.append(CalendarEvent(
                name="FOMC利率决议",
                event_type=EventType.FED_RATE,
                scheduled_at=datetime(target, month, day, 14, 0),
                impact=EventImpact.HIGH,
                source="Federal Reserve",
                description="美联储联邦公开市场委员会利率决议",
            ))

        # CPI 发布 (每月中旬)
        for month in range(1, 13):
            day = min(14 if month not in (1, 2) else 13, 28)
            events.append(CalendarEvent(
                name="美国CPI",
                event_type=EventType.CPI,
                scheduled_at=datetime(target, month, day, 8, 30),
                impact=EventImpact.HIGH,
                source="BLS",
                description="美国消费者物价指数",
            ))

        # PCE 发布 (每月底)
        for month in range(1, 13):
            day = min(28, 30)
            events.append(CalendarEvent(
                name="核心PCE物价指数",
                event_type=EventType.PCE,
                scheduled_at=datetime(target, month, day, 8, 30),
                impact=EventImpact.HIGH,
                source="BEA",
                description="核心个人消费支出物价指数",
            ))

        # NFP 发布 (每月第一个周五)
        for month in range(1, 13):
            first_day = datetime(target, month, 1)
            days_until_fri = (4 - first_day.weekday()) % 7
            nfp_day = 1 + days_until_fri
            events.append(CalendarEvent(
                name="非农就业",
                event_type=EventType.NFP,
                scheduled_at=datetime(target, month, nfp_day, 8, 30),
                impact=EventImpact.HIGH,
                source="BLS",
                description="美国非农就业数据",
            ))

        self.events.extend(events)
        self.events.sort(key=lambda e: e.scheduled_at)
        return events

    def check_event_outcome(
        self, event_name: str, actual: str, forecast: str,
    ) -> None:
        """更新事件的实际结果."""
        for e in self.events:
            if e.name == event_name and e.actual is None:
                e.actual = actual
                e.forecast = e.forecast or forecast
                return

    def get_upcoming(
        self,
        days: int = 7,
        min_impact: EventImpact = EventImpact.MEDIUM,
    ) -> list[CalendarEvent]:
        now = datetime.now()
        cutoff = now + timedelta(days=days)
        impact_order = {EventImpact.HIGH: 3, EventImpact.MEDIUM: 2, EventImpact.LOW: 1}
        min_level = impact_order.get(min_impact, 1)
        return [
            e for e in self.events
            if now <= e.scheduled_at <= cutoff
            and impact_order.get(e.impact, 0) >= min_level
        ]

    def get_today(self) -> list[CalendarEvent]:
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today + timedelta(days=1)
        return [e for e in self.events if today <= e.scheduled_at < tomorrow]

    def add_event(self, event: CalendarEvent) -> None:
        self.events.append(event)
        self.events.sort(key=lambda e: e.scheduled_at)
