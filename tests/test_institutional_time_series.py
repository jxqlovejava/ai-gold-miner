"""时间序列机构信号分析测试."""
from __future__ import annotations

from datetime import datetime, timedelta

from gold_miner.signals.institutional_time_series import (
    InstitutionalTimeSeriesAnalyzer,
)


def _target_record(
    bank: str = "Goldman Sachs",
    target_price: float = 3700.0,
    current_spot: float = 3300.0,
    upside_pct: float = 12.1,
    days_ago: int = 0,
) -> dict:
    ts = (datetime.now() - timedelta(days=days_ago)).isoformat()
    return {
        "timestamp": ts,
        "bank": bank,
        "target_price": target_price,
        "current_spot": current_spot,
        "upside_pct": upside_pct,
    }


class TestDetectTargetFlipFlops:
    """投行目标价反转检测测试."""

    def test_bullish_to_bearish_flip_flop(self):
        history = [
            _target_record(upside_pct=12.1, days_ago=5),
            _target_record(target_price=3100, upside_pct=-6.1, days_ago=1),
        ]
        analyzer = InstitutionalTimeSeriesAnalyzer(history)
        results = analyzer.detect_target_flip_flops(window_days=30)

        assert len(results) == 1
        assert results[0].bank == "Goldman Sachs"
        assert results[0].previous_direction == "bullish"
        assert results[0].current_direction == "bearish"

    def test_bearish_to_bullish_flip_flop(self):
        history = [
            _target_record(target_price=3000, upside_pct=-9.1, days_ago=5),
            _target_record(target_price=3700, upside_pct=12.1, days_ago=1),
        ]
        analyzer = InstitutionalTimeSeriesAnalyzer(history)
        results = analyzer.detect_target_flip_flops(window_days=30)

        assert len(results) == 1
        assert results[0].previous_direction == "bearish"
        assert results[0].current_direction == "bullish"

    def test_no_flip_flop_when_same_direction(self):
        history = [
            _target_record(upside_pct=12.1, days_ago=5),
            _target_record(target_price=3800, upside_pct=15.2, days_ago=1),
        ]
        analyzer = InstitutionalTimeSeriesAnalyzer(history)
        results = analyzer.detect_target_flip_flops(window_days=30)

        assert len(results) == 0

    def test_no_flip_flop_when_neutral(self):
        history = [
            _target_record(target_price=3300, upside_pct=0.0, days_ago=5),
            _target_record(target_price=3400, upside_pct=3.0, days_ago=1),
        ]
        analyzer = InstitutionalTimeSeriesAnalyzer(history)
        results = analyzer.detect_target_flip_flops(window_days=30)

        assert len(results) == 0

    def test_flip_flop_outside_window_ignored(self):
        history = [
            _target_record(upside_pct=12.1, days_ago=60),
            _target_record(target_price=3100, upside_pct=-6.1, days_ago=1),
        ]
        analyzer = InstitutionalTimeSeriesAnalyzer(history)
        results = analyzer.detect_target_flip_flops(window_days=30)

        assert len(results) == 0

    def test_multi_bank_flip_flops(self):
        history = [
            _target_record(bank="Goldman Sachs", upside_pct=12.1, days_ago=5),
            _target_record(bank="Goldman Sachs", target_price=3100, upside_pct=-6.1, days_ago=1),
            _target_record(bank="JPMorgan", upside_pct=-8.0, days_ago=5),
            _target_record(bank="JPMorgan", target_price=3600, upside_pct=9.1, days_ago=1),
        ]
        analyzer = InstitutionalTimeSeriesAnalyzer(history)
        results = analyzer.detect_target_flip_flops(window_days=30)

        assert len(results) == 2


class TestDetectWalkTalkMismatches:
    """言行不一检测测试."""

    def test_bullish_bank_with_selling_institution(self):
        bank_targets = [{"bank": "Goldman Sachs", "direction": "bullish"}]
        holdings = [
            {
                "institution": "Goldman Sachs Asset Management",
                "ticker": "GLD",
                "position_change_pct": -0.25,
                "quarter": "Q2 2026",
            },
        ]
        analyzer = InstitutionalTimeSeriesAnalyzer([])
        mismatches = analyzer.detect_walk_talk_mismatches(bank_targets, holdings)

        assert len(mismatches) == 1
        assert mismatches[0].bank == "Goldman Sachs"
        assert mismatches[0].institution == "Goldman Sachs Asset Management"

    def test_no_mismatch_when_buying(self):
        bank_targets = [{"bank": "Goldman Sachs", "direction": "bullish"}]
        holdings = [
            {
                "institution": "Goldman Sachs Asset Management",
                "ticker": "GLD",
                "position_change_pct": 0.15,
                "quarter": "Q2 2026",
            },
        ]
        analyzer = InstitutionalTimeSeriesAnalyzer([])
        mismatches = analyzer.detect_walk_talk_mismatches(bank_targets, holdings)

        assert len(mismatches) == 0

    def test_no_mismatch_when_bank_not_bullish(self):
        bank_targets = [{"bank": "Goldman Sachs", "direction": "bearish"}]
        holdings = [
            {
                "institution": "Goldman Sachs Asset Management",
                "ticker": "GLD",
                "position_change_pct": -0.25,
                "quarter": "Q2 2026",
            },
        ]
        analyzer = InstitutionalTimeSeriesAnalyzer([])
        mismatches = analyzer.detect_walk_talk_mismatches(bank_targets, holdings)

        assert len(mismatches) == 0

    def test_no_mismatch_when_change_small(self):
        bank_targets = [{"bank": "Goldman Sachs", "direction": "bullish"}]
        holdings = [
            {
                "institution": "Goldman Sachs Asset Management",
                "ticker": "GLD",
                "position_change_pct": -0.05,
                "quarter": "Q2 2026",
            },
        ]
        analyzer = InstitutionalTimeSeriesAnalyzer([])
        mismatches = analyzer.detect_walk_talk_mismatches(bank_targets, holdings)

        assert len(mismatches) == 0


class TestNameOverlap:
    """名称重叠测试."""

    def test_overlap_with_subword(self):
        assert InstitutionalTimeSeriesAnalyzer._name_overlap("Goldman Sachs", "Goldman Sachs Asset")

    def test_no_overlap(self):
        assert not InstitutionalTimeSeriesAnalyzer._name_overlap("Goldman Sachs", "Morgan Stanley")

    def test_case_insensitive(self):
        assert InstitutionalTimeSeriesAnalyzer._name_overlap("GOLDMAN SACHS", "goldman sachs asset")
