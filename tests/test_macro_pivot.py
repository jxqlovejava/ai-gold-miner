"""宏观政策转向多线汇聚信号测试."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from gold_miner.signals.base import SignalDirection
from gold_miner.signals.macro_pivot import (
    MacroPivotConfig,
    MacroPivotSignalGenerator,
    _script_of,
    _thread_direction,
    _thread_of,
)


def _event(name: str, etype: str, actual: str, days_ago: int) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        event_type=SimpleNamespace(value=etype),
        scheduled_at=datetime.now(tz=UTC) - timedelta(days=days_ago),
        actual=actual,
        forecast="持平",
    )


class _FakeCalendar:
    def __init__(self, events: list) -> None:
        self.events: list = []
        self._events = events

    def get_recent_events_with_results(self, lookback_days: int = 7) -> list:
        return self._events

    def load_fixed_calendar(self) -> None:
        pass


class TestThreadClassification:
    def test_thread_of(self):
        assert _thread_of(_event("美国非农", "nfp", "x", 1)) == "employment"
        assert _thread_of(_event("美国CPI", "cpi", "x", 1)) == "inflation"
        assert _thread_of(_event("FOMC决议", "fed_rate", "x", 1)) == "policy"
        assert _thread_of(_event("美伊冲突", "geo", "x", 1)) == "fx_geo"
        assert _thread_of(_event("央行购金", "gold_reserve", "x", 1)) == "central_bank"
        assert _thread_of(_event("其他", "monitor", "x", 1)) is None

    def test_thread_direction_fx_geo(self):
        assert _thread_direction(_event("美日联合干预日元", "geo", "美日联手干预汇率，日元163", 1)) \
            == SignalDirection.BULLISH
        assert _thread_direction(_event("美伊冲突", "geo", "冲突升级，实施新制裁", 1)) \
            == SignalDirection.BULLISH
        assert _thread_direction(_event("美伊缓和", "geo", "达成停火协议", 1)) \
            == SignalDirection.BEARISH

    def test_thread_direction_central_bank(self):
        assert _thread_direction(_event("央行购金", "gold_reserve", "央行增持20吨黄金", 1)) \
            == SignalDirection.BULLISH
        assert _thread_direction(_event("央行售金", "gold_reserve", "央行抛售黄金储备", 1)) \
            == SignalDirection.BEARISH

    def test_script_of(self):
        assert _script_of("employment", SignalDirection.BULLISH) == "dovish"
        assert _script_of("policy", SignalDirection.BEARISH) == "hawkish"
        assert _script_of("fx_geo", SignalDirection.BULLISH) == "risk_off"
        assert _script_of("central_bank", SignalDirection.BULLISH) == "structural"
        assert _script_of("central_bank", SignalDirection.BEARISH) is None


class TestMacroPivotGenerator:
    def test_three_threads_converge_dovish(self):
        """就业+通胀+政策 三线汇聚降息 → 强利多信号."""
        cal = _FakeCalendar([
            _event("美国非农", "nfp", "就业减少2.3万人，低于预期", 3),
            _event("美国CPI", "cpi", "通胀低于预期继续回落", 5),
            _event("美联储会议纪要", "fomc_minutes", "鸽派信号，考虑降息", 2),
        ])
        gen = MacroPivotSignalGenerator(calendar=cal)
        signals = gen.generate_signals()
        assert len(signals) == 1
        s = signals[0]
        assert "降息" in s.name
        assert s.direction == SignalDirection.BULLISH
        assert s.strength.value == "strong"
        assert set(s.metadata["threads"]) == {"employment", "inflation", "policy"}

    def test_two_threads_converge_moderate(self):
        """仅 2 线汇聚 → 中强度."""
        cal = _FakeCalendar([
            _event("美国非农", "nfp", "就业减少，低于预期", 3),
            _event("美联储会议纪要", "fomc_minutes", "考虑降息", 2),
        ])
        gen = MacroPivotSignalGenerator(calendar=cal)
        signals = gen.generate_signals()
        assert len(signals) == 1
        assert signals[0].strength.value == "moderate"

    def test_hawkish_convergence(self):
        """就业强+通胀高 汇聚加息剧本 → 利空信号."""
        cal = _FakeCalendar([
            _event("美国非农", "nfp", "就业大幅增长，远超预期", 3),
            _event("美国CPI", "cpi", "通胀高于预期继续攀升", 5),
        ])
        gen = MacroPivotSignalGenerator(calendar=cal)
        signals = gen.generate_signals()
        assert len(signals) == 1
        assert "加息" in signals[0].name
        assert signals[0].direction == SignalDirection.BEARISH

    def test_single_thread_no_signal(self):
        """单一线索 → 不输出汇聚信号（避免单点过度外推）."""
        cal = _FakeCalendar([
            _event("美国非农", "nfp", "就业减少，低于预期", 3),
        ])
        gen = MacroPivotSignalGenerator(calendar=cal)
        assert gen.generate_signals() == []

    def test_empty_events_no_signal(self):
        cal = _FakeCalendar([])
        gen = MacroPivotSignalGenerator(calendar=cal)
        assert gen.generate_signals() == []

    def test_geo_alone_no_risk_off(self):
        """仅 1 条地缘线索 → 不构成避险汇聚."""
        cal = _FakeCalendar([
            _event("美日联合干预日元", "geo", "美日联手干预汇率，日元163", 3),
        ])
        gen = MacroPivotSignalGenerator(calendar=cal)
        assert gen.generate_signals() == []
