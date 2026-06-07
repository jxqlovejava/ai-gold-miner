"""测试预测追踪器."""

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
import os

import pytest

from gold_miner.improvement.tracker import PredictionRecord, PredictionTracker


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
            assert loaded[0].direction == "buy"

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

    def test_resolve_prediction_incorrect_sell(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            r = _make_record(direction="sell", current_price=2000.0)
            tracker.record_prediction(r)

            resolved = tracker.resolve_prediction("abc123", 2100.0)
            assert resolved.was_correct is False

    def test_resolve_prediction_hold_correct(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            r = _make_record(direction="hold", current_price=2000.0)
            tracker.record_prediction(r)

            resolved = tracker.resolve_prediction("abc123", 2010.0)
            assert resolved.was_correct is True  # < 1% change

    def test_resolve_prediction_hold_incorrect(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = PredictionTracker(data_dir=Path(tmpdir))
            r = _make_record(direction="hold", current_price=2000.0)
            tracker.record_prediction(r)

            resolved = tracker.resolve_prediction("abc123", 2050.0)
            assert resolved.was_correct is False  # > 1% change

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
