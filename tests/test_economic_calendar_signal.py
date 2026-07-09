"""经济日历信号生成器测试."""
from __future__ import annotations

from datetime import datetime as RealDatetime
from datetime import timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from gold_miner.data.calendar import CalendarEvent, EventCalendar, EventImpact, EventType
from gold_miner.signals.base import SignalDirection
from gold_miner.signals.economic_calendar import EconomicCalendarSignalGenerator

_UTC = timezone.utc


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _setup_mock_dt(mock_dt: MagicMock, frozen_now: RealDatetime) -> None:
    """配置 mock datetime：仅拦截 now()，构造器委托给真实 datetime."""
    mock_dt.now.return_value = frozen_now
    mock_dt.side_effect = lambda *a, **kw: RealDatetime(*a, **kw)
    mock_dt.strptime = RealDatetime.strptime
    mock_dt.fromisoformat = RealDatetime.fromisoformat
    mock_dt.timedelta = timedelta
    mock_dt.timezone = timezone


_FROZEN = RealDatetime(2026, 6, 23, 12, 0, 0, tzinfo=_UTC)

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def generator() -> EconomicCalendarSignalGenerator:
    return EconomicCalendarSignalGenerator()


@pytest.fixture
def fixed_calendar() -> EventCalendar:
    """返回包含已知未来事件的日历（使用 aware UTC 时间以匹配 production 行为）."""
    cal = EventCalendar()
    base = _FROZEN
    cal.events = [
        CalendarEvent(
            name="核心PCE物价指数",
            event_type=EventType.PCE,
            scheduled_at=base + timedelta(days=2),
            impact=EventImpact.HIGH,
            source="BEA",
        ),
        CalendarEvent(
            name="非农就业",
            event_type=EventType.NFP,
            scheduled_at=base + timedelta(days=10),
            impact=EventImpact.HIGH,
            source="BLS",
        ),
        CalendarEvent(
            name="ISM制造业PMI",
            event_type=EventType.PMI,
            scheduled_at=base + timedelta(days=8),
            impact=EventImpact.HIGH,
            source="ISM",
        ),
    ]
    return cal


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


@patch("gold_miner.signals.economic_calendar.datetime")
def test_generate_signals_returns_upcoming_events(
    mock_dt: MagicMock,
    generator: EconomicCalendarSignalGenerator,
    fixed_calendar: EventCalendar,
) -> None:
    _setup_mock_dt(mock_dt, _FROZEN)

    # 绕过真实日历加载
    generator.calendar = fixed_calendar
    signals = generator.generate_signals()

    event_signals = [s for s in signals if s.metadata.get("event_type")]
    assert len(event_signals) == 3
    assert all(s.direction == SignalDirection.NEUTRAL for s in event_signals)
    assert all(s.score == 0.0 for s in event_signals)
    assert event_signals[0].name == "未来事件: 核心PCE物价指数"


@patch("gold_miner.signals.economic_calendar.datetime")
def test_warn_within_48_hours(
    mock_dt: MagicMock,
    generator: EconomicCalendarSignalGenerator,
    fixed_calendar: EventCalendar,
) -> None:
    _setup_mock_dt(mock_dt, _FROZEN)

    generator.calendar = fixed_calendar
    signals = generator.generate_signals()

    warnings = [s for s in signals if s.metadata.get("rule_id") == "r004"]
    assert len(warnings) == 1
    assert warnings[0].name == "r004 数据前不重仓提醒"
    assert "核心PCE物价指数" in warnings[0].description


@patch("gold_miner.signals.economic_calendar.datetime")
def test_no_warning_for_events_outside_window(
    mock_dt: MagicMock,
    generator: EconomicCalendarSignalGenerator,
    fixed_calendar: EventCalendar,
) -> None:
    _setup_mock_dt(mock_dt, _FROZEN)

    # 把 48h 窗口改小，只有 PCE 在窗口内，非农/PMI 不在
    generator.config.warn_within_hours = 1
    generator.calendar = fixed_calendar
    signals = generator.generate_signals()

    warnings = [s for s in signals if s.metadata.get("rule_id") == "r004"]
    assert len(warnings) == 0


@patch("gold_miner.signals.economic_calendar.datetime")
def test_no_events_outside_ahead_window(
    mock_dt: MagicMock,
    generator: EconomicCalendarSignalGenerator,
    fixed_calendar: EventCalendar,
) -> None:
    _setup_mock_dt(mock_dt, _FROZEN)

    generator.config.days_ahead = 7
    generator.calendar = fixed_calendar
    signals = generator.generate_signals()

    events = [s for s in signals if s.metadata.get("event_type")]
    # 只有 PCE(2天后) 在 7 天窗口内
    assert len(events) == 1
    assert events[0].metadata["days_until"] == 2


@patch("gold_miner.signals.economic_calendar.datetime")
def test_event_same_day_triggers_r004_by_hours(
    mock_dt: MagicMock,
    generator: EconomicCalendarSignalGenerator,
) -> None:
    """当天但 <48h 的事件仍应触发 r004 提醒."""
    frozen = RealDatetime(2026, 6, 23, 14, 0, 0, tzinfo=_UTC)
    _setup_mock_dt(mock_dt, frozen)

    cal = EventCalendar()
    cal.events = [
        CalendarEvent(
            name="FOMC利率决议",
            event_type=EventType.FED_RATE,
            scheduled_at=RealDatetime(2026, 6, 23, 20, 0, 0, tzinfo=_UTC),
            impact=EventImpact.HIGH,
            source="Federal Reserve",
        ),
    ]
    generator.calendar = cal
    signals = generator.generate_signals()

    warnings = [s for s in signals if s.metadata.get("rule_id") == "r004"]
    assert len(warnings) == 1
    assert "6 小时内" in warnings[0].description


@patch("gold_miner.signals.economic_calendar.datetime")
def test_event_metadata(
    mock_dt: MagicMock,
    generator: EconomicCalendarSignalGenerator,
    fixed_calendar: EventCalendar,
) -> None:
    _setup_mock_dt(mock_dt, _FROZEN)

    generator.config.days_ahead = 7
    generator.calendar = fixed_calendar
    signals = generator.generate_signals()

    event = next(s for s in signals if s.metadata.get("event_type"))
    assert event.metadata["event_type"] == EventType.PCE.value
    assert event.metadata["impact"] == EventImpact.HIGH.value
    assert event.metadata["source"] == "BEA"
    assert event.metadata["source_tier"] == "T0"
    assert "scheduled_at" in event.metadata


def test_generator_gracefully_handles_calendar_failure() -> None:
    gen = EconomicCalendarSignalGenerator()
    # 让 load_fixed_calendar 抛异常
    gen.calendar = MagicMock()
    gen.calendar.events = []
    gen.calendar.load_fixed_calendar.side_effect = RuntimeError("boom")

    signals = gen.generate_signals()
    assert signals == []
