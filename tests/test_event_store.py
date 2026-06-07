"""EventStore + 事件模型 + 状态重放 测试."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from gold_miner.config import settings
from gold_miner.events.models import (
    Event,
    EventType,
    EvidenceSnapshot,
    PredictionState,
    SignalSnapshot,
    SourceRef,
)
from gold_miner.events.store import EventStore


class TestEventModels:
    def test_signal_snapshot_immutable(self):
        s = SignalSnapshot(name="RSI超卖", dimension="technical", direction="bullish", score=0.6)
        assert s.name == "RSI超卖"
        with pytest.raises(Exception):
            s.name = "changed"  # type: ignore[misc]

    def test_source_ref_from_dict(self):
        d = {"ref_type": "article", "ref_id": "abc", "url": "https://x.com", "title": "Test"}
        ref = SourceRef.from_dict(d)
        assert ref.ref_type == "article"
        assert ref.ref_id == "abc"

    def test_evidence_snapshot_from_price_data(self):
        snap = EvidenceSnapshot.from_price_data(
            prediction_id="test_001",
            spot_gold=2650.0,
            dxy=104.5,
            real_rate=2.0,
            signals=[
                {"name": "RSI超卖", "dimension": "technical", "direction": "bullish", "score": 0.6},
                {"name": "美元走弱", "dimension": "fundamental", "direction": "bullish", "score": 0.4},
            ],
            dimension_scores={"technical": 0.5, "fundamental": 0.4},
            composite_score=0.35,
            confidence=0.65,
            source_type="scan",
            source_refs=[{"ref_type": "data_source", "title": "Yahoo Finance"}],
        )
        assert snap.prediction_id == "test_001"
        assert snap.spot_gold == 2650.0
        assert snap.dxy == 104.5
        assert len(snap.signals_summary) == 2
        assert snap.signals_summary[0].name == "RSI超卖"
        assert snap.dimension_scores["technical"] == 0.5
        assert snap.composite_score == 0.35

    def test_event_create(self):
        e = Event.create(EventType.PREDICTION_MADE, "pid_001", {"direction": "long"})
        assert e.event_type == EventType.PREDICTION_MADE
        assert e.prediction_id == "pid_001"
        assert e.payload["direction"] == "long"


class TestEventStore:
    def test_append_and_replay(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            store = EventStore(data_dir=data_dir)

            pid = "test_prediction_001"
            store.append(EventType.PREDICTION_MADE, pid, {
                "direction": "long",
                "composite_score": 0.45,
                "confidence": 0.7,
                "position_pct": 0.4,
                "horizon_days": 5,
                "source": "scan",
                "auto_resolve": True,
                "current_price": 2650.0,
            })

            state = store.replay(pid)
            assert state.direction == "long"
            assert state.composite_score == 0.45
            assert state.horizon_days == 5
            assert state.auto_resolve is True
            assert state.status == "pending"

    def test_full_prediction_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            store = EventStore(data_dir=data_dir)

            pid = "test_lifecycle_001"

            # 1. prediction_made
            store.append(EventType.PREDICTION_MADE, pid, {
                "direction": "long",
                "composite_score": 0.5,
                "confidence": 0.8,
                "position_pct": 0.4,
                "horizon_days": 3,
                "source": "scan",
                "auto_resolve": True,
                "current_price": 2650.0,
            })

            # 2. evidence_attached
            snap = EvidenceSnapshot.from_price_data(
                prediction_id=pid,
                spot_gold=2650.0,
                dxy=104.5,
                signals=[
                    {"name": "RSI超卖", "dimension": "technical", "direction": "bullish", "score": 0.6},
                ],
                dimension_scores={"technical": 0.6},
                composite_score=0.5,
                confidence=0.8,
                source_type="scan",
            )
            store.append(EventType.EVIDENCE_ATTACHED, pid, {"snapshot": snap})

            state = store.replay(pid)
            assert len(state.evidence_snapshots) == 1
            assert state.evidence_snapshots[0].spot_gold == 2650.0

            # 3. price_observed
            store.append(EventType.PRICE_OBSERVED, pid, {"observed_price": 2680.0})

            # 4. prediction_settled
            store.append(EventType.PREDICTION_SETTLED, pid, {
                "was_correct": True,
                "actual_return": 0.01132,
                "settled_by": "auto",
            })

            # 5. human_verified
            store.append(EventType.HUMAN_VERIFIED, pid, {"verifier_notes": "确认无误"})

            state = store.replay(pid)
            assert state.status == "verified"
            assert state.settled is True
            assert state.was_correct is True
            assert state.verified is True
            assert state.settled_by == "auto"
            assert state.observed_price == 2680.0

    def test_invalidation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            store = EventStore(data_dir=data_dir)

            pid = "test_invalid_001"
            store.append(EventType.PREDICTION_MADE, pid, {
                "direction": "long",
                "composite_score": 0.3,
                "confidence": 0.5,
                "position_pct": 0.2,
                "horizon_days": 7,
                "source": "scan",
                "auto_resolve": True,
                "current_price": 2650.0,
            })
            store.append(EventType.PREDICTION_INVALIDATED, pid, {"reason": "数据源异常"})

            state = store.replay(pid)
            assert state.status == "invalidated"
            assert state.invalidated is True
            assert state.invalidation_reason == "数据源异常"

    def test_pending_settlement(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            store = EventStore(data_dir=data_dir)

            # 创建已到期预测: horizon=0 (立即到期)
            pid = "test_pending_001"
            store.append(EventType.PREDICTION_MADE, pid, {
                "direction": "long",
                "composite_score": 0.4,
                "confidence": 0.6,
                "position_pct": 0.3,
                "horizon_days": 0,
                "source": "scan",
                "auto_resolve": True,
                "current_price": 2650.0,
            })

            pending = store.pending_settlement()
            assert pid in pending

    def test_pending_settlement_not_due(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            store = EventStore(data_dir=data_dir)

            # 创建未到期预测: horizon=30天
            pid = "test_not_due_001"
            store.append(EventType.PREDICTION_MADE, pid, {
                "direction": "long",
                "composite_score": 0.4,
                "confidence": 0.6,
                "position_pct": 0.3,
                "horizon_days": 30,
                "source": "scan",
                "auto_resolve": True,
                "current_price": 2650.0,
            })

            pending = store.pending_settlement()
            assert pid not in pending

    def test_all_prediction_ids(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            store = EventStore(data_dir=data_dir)

            for i in range(3):
                store.append(EventType.PREDICTION_MADE, f"pid_{i}", {
                    "direction": "long",
                    "composite_score": 0.3,
                    "confidence": 0.5,
                    "position_pct": 0.2,
                    "horizon_days": 7,
                    "source": "scan",
                    "auto_resolve": True,
                    "current_price": 2650.0,
                })

            ids = store.all_prediction_ids()
            assert len(ids) == 3
            assert "pid_0" in ids

    def test_stats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            store = EventStore(data_dir=data_dir)

            pid = "test_stats_001"
            store.append(EventType.PREDICTION_MADE, pid, {
                "direction": "long",
                "composite_score": 0.4,
                "confidence": 0.7,
                "position_pct": 0.3,
                "horizon_days": 5,
                "source": "scan",
                "auto_resolve": True,
                "current_price": 2650.0,
            })
            store.append(EventType.PREDICTION_SETTLED, pid, {
                "was_correct": True,
                "actual_return": 0.02,
                "settled_by": "auto",
            })

            st = store.stats()
            assert st["total_predictions"] == 1
            assert st["settled"] == 1
            assert st["accuracy"] == 1.0

    def test_prediction_state_short_term_auto_resolve(self):
        state = PredictionState(prediction_id="test")
        state.horizon_days = 3
        state.auto_resolve = True
        assert state.is_due is False  # 刚创建

    def test_get_state_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EventStore(data_dir=Path(tmpdir))
            assert store.get_state("nonexistent") is None


class TestAutoResolver:
    def test_determine_correctness(self):
        from gold_miner.events.resolver import _determine_correctness

        assert _determine_correctness("long", 0.02) is True
        assert _determine_correctness("long", -0.02) is False
        assert _determine_correctness("short", -0.02) is True
        assert _determine_correctness("short", 0.02) is False
        assert _determine_correctness("neutral", 0.005) is True
        assert _determine_correctness("neutral", 0.02) is False
