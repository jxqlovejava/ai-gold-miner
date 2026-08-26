"""测试预测追踪器."""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from gold_miner.improvement.tracker import (
    PredictionRecord,
    PredictionTracker,
    determine_correctness,
    normalize_direction,
    predict_direction,
)


def _make_record(
    id: str = "abc123",
    direction: str = "buy",
    current_price: float = 2000.0,
    composite_score: float = 0.35,
    confidence: float = 0.65,
    position_pct: float = 0.30,
    timestamp: datetime | None = None,
) -> PredictionRecord:
    return PredictionRecord(
        id=id,
        timestamp=timestamp or datetime.now(),
        current_price=current_price,
        signals=[
            {"name": "RSI超卖", "dimension": "technical", "direction": "bullish", "score": 0.5},
            {"name": "MACD金叉", "dimension": "technical", "direction": "bullish", "score": 0.6},
        ],
        composite_score=composite_score,
        confidence=confidence,
        direction=direction,
        position_pct=position_pct,
        dimension_scores={"technical": 0.55, "fundamental": 0.0},
    )


class TestNormalizeDirection:
    def test_bullish_aliases(self):
        for d in ("long", "buy", "BUY", "bullish", "做多", "看多", "买入"):
            assert normalize_direction(d) == "long"

    def test_bearish_aliases(self):
        for d in ("short", "sell", "SELL", "bearish", "做空", "看空", "卖出"):
            assert normalize_direction(d) == "short"

    def test_neutral_aliases(self):
        for d in ("neutral", "hold", "HOLD", "观望", "中性", "持有"):
            assert normalize_direction(d) == "neutral"

    def test_unknown_defaults_neutral(self):
        assert normalize_direction("whatever") == "neutral"
        assert normalize_direction("") == "neutral"


class TestDetermineCorrectness:
    def test_long_correct_on_up(self):
        assert determine_correctness("long", 0.01) is True
        assert determine_correctness("buy", 0.05) is True

    def test_long_incorrect_on_down(self):
        assert determine_correctness("long", -0.01) is False

    def test_short_correct_on_down(self):
        assert determine_correctness("short", -0.02) is True
        assert determine_correctness("sell", -0.05) is True

    def test_short_incorrect_on_up(self):
        assert determine_correctness("short", 0.01) is False

    def test_neutral_within_band(self):
        # 1.4% still within 1.5% band
        assert determine_correctness("neutral", 0.014) is True
        assert determine_correctness("hold", -0.01) is True

    def test_neutral_outside_band(self):
        assert determine_correctness("neutral", 0.02) is False
        assert determine_correctness("观望", -0.02) is False


class TestPredictDirection:
    """方向预测阈值 (2026-08-26): |score|>=0.15 且 conf>=0.5 才押方向, 否则观望."""

    def test_strong_bullish_goes_long(self):
        assert predict_direction(0.20, 0.6) == "long"

    def test_strong_bearish_goes_short(self):
        assert predict_direction(-0.20, 0.6) == "short"

    def test_threshold_boundary(self):
        assert predict_direction(0.15, 0.5) == "long"
        assert predict_direction(-0.15, 0.5) == "short"

    def test_below_threshold_neutral(self):
        assert predict_direction(0.14, 0.6) == "neutral"
        assert predict_direction(-0.14, 0.6) == "neutral"

    def test_low_confidence_neutral(self):
        assert predict_direction(0.30, 0.4) == "neutral"

    def test_none_defaults_neutral(self):
        assert predict_direction(None, None) == "neutral"
        assert predict_direction(0.0, 0.0) == "neutral"


class TestPredictionRecord:
    def test_create_record(self):
        r = _make_record()
        assert r.id == "abc123"
        assert r.direction == "buy"
        assert r.actual_price is None
        assert r.was_correct is None


class TestPredictionTracker:
    def test_record_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            r = _make_record()
            tracker.record_prediction(r)

            loaded = tracker.load_all()
            assert len(loaded) == 1
            assert loaded[0].id == "abc123"
            # buy is normalized to long on record
            assert loaded[0].direction == "long"

    def test_record_normalizes_direction(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            tracker.record_prediction(_make_record(id="a", direction="做多"))
            tracker.record_prediction(_make_record(id="b", direction="sell"))
            tracker.record_prediction(_make_record(id="c", direction="hold"))
            by_id = {r.id: r.direction for r in tracker.load_all()}
            assert by_id["a"] == "long"
            assert by_id["b"] == "short"
            assert by_id["c"] == "neutral"

    def test_resolve_prediction_correct_buy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            r = _make_record(direction="buy", current_price=2000.0)
            tracker.record_prediction(r)

            resolved = tracker.resolve_prediction("abc123", 2100.0)
            assert resolved is not None
            assert resolved.was_correct is True
            assert resolved.actual_return == pytest.approx(0.05)
            assert resolved.actual_price == 2100.0
            assert resolved.direction == "long"

    def test_resolve_prediction_incorrect_buy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            r = _make_record(direction="buy", current_price=2000.0)
            tracker.record_prediction(r)

            resolved = tracker.resolve_prediction("abc123", 1900.0)
            assert resolved.was_correct is False
            assert resolved.actual_return == pytest.approx(-0.05)

    def test_resolve_prediction_correct_sell(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            r = _make_record(direction="sell", current_price=2000.0)
            tracker.record_prediction(r)

            resolved = tracker.resolve_prediction("abc123", 1900.0)
            assert resolved.was_correct is True
            assert resolved.direction == "short"

    def test_resolve_prediction_incorrect_sell(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            r = _make_record(direction="sell", current_price=2000.0)
            tracker.record_prediction(r)

            resolved = tracker.resolve_prediction("abc123", 2100.0)
            assert resolved.was_correct is False

    def test_resolve_prediction_long_correct(self):
        """Regression: long must NOT fall into hold/neutral branch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            tracker.record_prediction(
                _make_record(direction="long", current_price=900.0)
            )
            # +2% — would fail under old |ret|<1% hold logic if long misclassified
            resolved = tracker.resolve_prediction("abc123", 918.0)
            assert resolved is not None
            assert resolved.was_correct is True
            assert resolved.actual_return == pytest.approx(0.02)

    def test_resolve_prediction_long_incorrect(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            tracker.record_prediction(
                _make_record(direction="long", current_price=900.0)
            )
            resolved = tracker.resolve_prediction("abc123", 882.0)
            assert resolved.was_correct is False
            assert resolved.actual_return == pytest.approx(-0.02)

    def test_resolve_prediction_short_correct(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            tracker.record_prediction(
                _make_record(direction="short", current_price=900.0)
            )
            resolved = tracker.resolve_prediction("abc123", 882.0)
            assert resolved.was_correct is True

    def test_resolve_prediction_short_incorrect(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            tracker.record_prediction(
                _make_record(direction="short", current_price=900.0)
            )
            resolved = tracker.resolve_prediction("abc123", 918.0)
            assert resolved.was_correct is False

    def test_resolve_prediction_hold_correct(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            r = _make_record(direction="hold", current_price=2000.0)
            tracker.record_prediction(r)

            # 0.5% < 1.5% neutral band
            resolved = tracker.resolve_prediction("abc123", 2010.0)
            assert resolved.was_correct is True
            assert resolved.direction == "neutral"

    def test_resolve_prediction_hold_incorrect(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            r = _make_record(direction="hold", current_price=2000.0)
            tracker.record_prediction(r)

            # 2.5% > 1.5% neutral band
            resolved = tracker.resolve_prediction("abc123", 2050.0)
            assert resolved.was_correct is False

    def test_resolve_prediction_neutral_band_edge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            tracker.record_prediction(
                _make_record(id="n1", direction="neutral", current_price=1000.0)
            )
            # exactly 1.4% — within band
            r1 = tracker.resolve_prediction("n1", 1014.0)
            assert r1.was_correct is True

            tracker.record_prediction(
                _make_record(id="n2", direction="neutral", current_price=1000.0)
            )
            # 1.6% — outside band
            r2 = tracker.resolve_prediction("n2", 1016.0)
            assert r2.was_correct is False

    def test_list_unresolved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            tracker.record_prediction(_make_record(id="r1"))
            tracker.record_prediction(_make_record(id="r2"))
            tracker.resolve_prediction("r1", 2100.0)

            unresolved = tracker.list_unresolved()
            assert len(unresolved) == 1
            assert unresolved[0].id == "r2"

    def test_list_resolved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            tracker.record_prediction(_make_record(id="r1"))
            tracker.record_prediction(_make_record(id="r2"))
            tracker.resolve_prediction("r1", 2100.0)

            resolved = tracker.list_resolved()
            assert len(resolved) == 1
            assert resolved[0].id == "r1"

    def test_stats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            tracker.record_prediction(_make_record(id="r1", direction="buy", current_price=2000))
            tracker.record_prediction(_make_record(id="r2", direction="sell", current_price=2000))
            tracker.resolve_prediction("r1", 2100.0)  # correct
            tracker.resolve_prediction("r2", 2100.0)  # incorrect

            stats = tracker.stats()
            assert stats["total"] == 2
            assert stats["resolved"] == 2
            assert stats["unresolved"] == 0
            assert stats["correct"] == 1
            assert stats["accuracy"] == 0.5
            # fixture prices 2000 excluded from accuracy_ex_test
            assert stats["accuracy_ex_test"] == 0.0
            assert stats["resolved_ex_test"] == 0

    def test_resolve_nonexistent_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            result = tracker.resolve_prediction("nonexistent", 100.0)
            assert result is None

    def test_already_resolved_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            tracker.record_prediction(_make_record(id="r1"))
            tracker.resolve_prediction("r1", 2100.0)
            result = tracker.resolve_prediction("r1", 2200.0)
            assert result is None

    def test_recent_returns_sorted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            t1 = datetime.now() - timedelta(days=2)
            t2 = datetime.now() - timedelta(days=1)
            t3 = datetime.now()
            tracker.record_prediction(_make_record(id="old", timestamp=t1))
            tracker.record_prediction(_make_record(id="mid", timestamp=t2))
            tracker.record_prediction(_make_record(id="new", timestamp=t3))

            recent = tracker.recent(2)
            assert len(recent) == 2
            assert recent[0].id == "new"

    def test_auto_resolve_stale(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            old = _make_record(
                id="stale1",
                direction="long",
                current_price=900.0,
                timestamp=datetime.now() - timedelta(hours=48),
            )
            fresh = _make_record(
                id="fresh1",
                direction="long",
                current_price=900.0,
                timestamp=datetime.now() - timedelta(hours=2),
            )
            tracker.record_prediction(old)
            tracker.record_prediction(fresh)

            newly = tracker.auto_resolve_stale(current_price=945.0, min_age_hours=24)
            assert len(newly) == 1
            assert newly[0].id == "stale1"
            assert newly[0].actual_price == 945.0
            assert newly[0].was_correct is True  # +5%
            assert newly[0].actual_return == pytest.approx(0.05)

            # fresh still unresolved
            assert tracker.list_unresolved()[0].id == "fresh1"
            assert len(tracker.list_resolved()) == 1

    def test_auto_resolve_skips_invalidated_and_already_resolved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            tracker.record_prediction(
                _make_record(
                    id="done",
                    direction="long",
                    current_price=900.0,
                    timestamp=datetime.now() - timedelta(hours=72),
                )
            )
            tracker.record_prediction(
                _make_record(
                    id="bad",
                    direction="long",
                    current_price=900.0,
                    timestamp=datetime.now() - timedelta(hours=72),
                )
            )
            tracker.resolve_prediction("done", 910.0)
            tracker.invalidate_prediction("bad", reason="test")

            newly = tracker.auto_resolve_stale(current_price=950.0, min_age_hours=24)
            assert newly == []

    def test_auto_resolve_with_horizons_hours(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            tracker.record_prediction(
                _make_record(
                    id="h1",
                    direction="short",
                    current_price=1000.0,
                    timestamp=datetime.now() - timedelta(hours=30),
                )
            )
            newly = tracker.auto_resolve_stale(
                current_price=980.0,
                min_age_hours=24,
                horizons_hours=[24, 120],
            )
            assert len(newly) == 1
            assert newly[0].was_correct is True  # short + down 2%

    def test_corrupted_jsonl_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_file = Path(tmpdir) / "prediction_journal.jsonl"
            journal_file.write_text('{"broken": true\n', encoding="utf-8")
            # valid record
            r = _make_record(id="valid")
            data = {
                "id": "valid",
                "timestamp": r.timestamp.isoformat(),
                "current_price": r.current_price,
                "signals": r.signals,
                "composite_score": r.composite_score,
                "confidence": r.confidence,
                "direction": r.direction,
                "position_pct": r.position_pct,
                "dimension_scores": r.dimension_scores,
                "actual_price": None,
                "resolved_at": None,
                "actual_return": None,
                "was_correct": None,
            }
            with open(journal_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")

            tracker = PredictionTracker(data_dir=Path(tmpdir))
            loaded = tracker.load_all()
            assert len(loaded) == 1
            assert loaded[0].id == "valid"

    def test_empty_tracker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            assert tracker.load_all() == []
            assert tracker.list_unresolved() == []
            assert tracker.list_resolved() == []
            stats = tracker.stats()
            assert stats["total"] == 0
