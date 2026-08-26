"""置信度校准模块测试 (2026-08-26)."""
from __future__ import annotations

from gold_miner.improvement.calibration import (
    MIN_CALIBRATION_SAMPLES,
    build_calibration,
    calibrate_confidence,
)


def _rec(direction: str, confidence: float, actual_return: float, actual_price=100.0) -> dict:
    return {
        "direction": direction,
        "confidence": confidence,
        "actual_price": actual_price,
        "actual_return": actual_return,
        "invalidated": False,
    }


class TestBuildCalibration:
    def test_basic_direction_hit_rates(self):
        recs = [
            _rec("long", 0.6, 0.02),      # long 正确 (ret>0)
            _rec("long", 0.7, -0.01),     # long 错误
            _rec("neutral", 0.6, 0.01),   # neutral 正确 (|ret|<1.5%)
            _rec("neutral", 0.6, 0.03),   # neutral 错误
        ]
        table = build_calibration(recs)
        assert table["long"]["n"] == 2
        assert table["long"]["hit_rate"] == 0.5
        assert table["neutral"]["n"] == 2
        assert table["neutral"]["hit_rate"] == 0.5

    def test_excludes_invalidated_and_unresolved(self):
        recs = [
            _rec("long", 0.6, 0.02),
            {**_rec("long", 0.6, 0.02), "invalidated": True},
            {**_rec("long", 0.6, 0.02), "actual_price": None},
        ]
        table = build_calibration(recs)
        assert table["long"]["n"] == 1

    def test_normalizes_direction_aliases(self):
        recs = [_rec("buy", 0.6, 0.02), _rec("bullish", 0.6, 0.02)]
        table = build_calibration(recs)
        assert table["long"]["n"] == 2


class TestCalibrateConfidence:
    def test_sufficient_samples_uses_hit_rate(self):
        table = {"long": {"n": 20, "hit_rate": 0.80, "mean_conf": 0.65}}
        assert calibrate_confidence("long", 0.65, table) == 0.80

    def test_insufficient_samples_keeps_original(self):
        table = {"long": {"n": 5, "hit_rate": 0.0, "mean_conf": 0.65}}
        assert calibrate_confidence("long", 0.65, table) == 0.65

    def test_threshold_boundary_uses_hit_rate(self):
        table = {"neutral": {"n": MIN_CALIBRATION_SAMPLES, "hit_rate": 0.83, "mean_conf": 0.64}}
        assert calibrate_confidence("neutral", 0.64, table) == 0.83

    def test_unknown_direction_falls_back(self):
        table = {"long": {"n": 20, "hit_rate": 0.8, "mean_conf": 0.65}}
        assert calibrate_confidence("sideways", 0.6, table) == 0.6

    def test_no_table_keeps_original(self):
        assert calibrate_confidence("long", 0.7, None) == 0.7
