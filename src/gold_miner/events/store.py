"""EventStore — JSONL 只追加事件存储 + 状态重放."""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from gold_miner.config import settings
from gold_miner.events.models import (
    Event,
    EventType,
    EvidenceSnapshot,
    PredictionState,
)


class EventStore:
    """JSONL 只追加事件存储.

    事件不可变，只能追加。通过重放事件计算预测状态。
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or settings.data_path
        self.file = self.data_dir / "event_store.jsonl"

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def append(
        self,
        event_type: EventType,
        prediction_id: str,
        payload: dict[str, Any],
    ) -> Event:
        event = Event.create(event_type, prediction_id, payload)
        self._write_event(event)
        logger.debug(
            f"事件已记录: {event_type.value} "
            f"(prediction: {prediction_id[:8]}..., event: {event.event_id[:8]}...)"
        )
        return event

    def _write_event(self, event: Event) -> None:
        data = {
            "event_id": event.event_id,
            "timestamp": event.timestamp.isoformat(),
            "event_type": event.event_type.value,
            "prediction_id": event.prediction_id,
            "payload": _serialize_payload(event.payload),
        }
        with open(self.file, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def events_for(self, prediction_id: str) -> list[Event]:
        return [e for e in self._iter_events() if e.prediction_id == prediction_id]

    def all_prediction_ids(self) -> list[str]:
        seen: set[str] = set()
        for e in self._iter_events():
            seen.add(e.prediction_id)
        return sorted(seen)

    def pending_settlement(self) -> list[str]:
        """返回已到期但未结算的预测 ID."""
        states = [
            self.replay(pid) for pid in self.all_prediction_ids()
        ]
        return [
            s.prediction_id for s in states
            if s.is_due and not s.settled and not s.invalidated and s.auto_resolve
        ]

    def pending_verification(self) -> list[str]:
        """返回待人工确认的预测 ID."""
        states = [
            self.replay(pid) for pid in self.all_prediction_ids()
        ]
        return [
            s.prediction_id for s in states
            if s.settled and not s.verified and not s.invalidated
        ]

    def all_states(self) -> list[PredictionState]:
        return [self.replay(pid) for pid in self.all_prediction_ids()]

    def get_state(self, prediction_id: str) -> PredictionState | None:
        events = self.events_for(prediction_id)
        if not events:
            return None
        return self._replay_events(prediction_id, events)

    # ------------------------------------------------------------------
    # 状态重放
    # ------------------------------------------------------------------

    def replay(self, prediction_id: str) -> PredictionState:
        events = self.events_for(prediction_id)
        return self._replay_events(prediction_id, events)

    @staticmethod
    def _replay_events(
        prediction_id: str,
        events: list[Event],
    ) -> PredictionState:
        state = PredictionState(prediction_id=prediction_id)
        for e in sorted(events, key=lambda x: x.timestamp):
            _apply(state, e)
        return state

    # ------------------------------------------------------------------
    # 批量
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        states = self.all_states()
        total = len(states)
        settled = [s for s in states if s.settled]
        correct = sum(1 for s in settled if s.was_correct)
        pending = [s for s in states if s.status == "pending"]
        return {
            "total_predictions": total,
            "settled": len(settled),
            "pending": len(pending),
            "correct": correct,
            "accuracy": correct / len(settled) if settled else 0.0,
            "auto_resolved": sum(1 for s in settled if s.settled_by == "auto"),
            "human_verified": sum(1 for s in states if s.verified),
        }

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _iter_events(self) -> list[Event]:
        if not self.file.exists():
            return []
        result: list[Event] = []
        with open(self.file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    result.append(Event(
                        event_id=data["event_id"],
                        timestamp=datetime.fromisoformat(data["timestamp"]),
                        event_type=EventType(data["event_type"]),
                        prediction_id=data["prediction_id"],
                        payload=data["payload"],
                    ))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
        return result


# ------------------------------------------------------------------
# 事件应用器 (replay)
# ------------------------------------------------------------------


def _apply(state: PredictionState, event: Event) -> None:
    """将单个事件应用到状态上."""
    p = event.payload
    t = event.event_type

    if t == EventType.PREDICTION_MADE:
        state.created_at = event.timestamp
        state.direction = p.get("direction", "neutral")
        state.composite_score = float(p.get("composite_score", 0))
        state.confidence = float(p.get("confidence", 0))
        state.position_pct = float(p.get("position_pct", 0))
        state.horizon_days = int(p.get("horizon_days", 7))
        state.source = p.get("source", "scan")
        state.auto_resolve = bool(p.get("auto_resolve", True))
        state.current_price = float(p.get("current_price", 0))

    elif t == EventType.EVIDENCE_ATTACHED:
        snap_data = p.get("snapshot", p)
        snap = _deserialize_snapshot(snap_data)
        state.evidence_snapshots.append(snap)

    elif t == EventType.PRICE_OBSERVED:
        state.observed_price = float(p.get("observed_price", 0))
        state.observed_at = event.timestamp

    elif t == EventType.PREDICTION_SETTLED:
        state.settled = True
        state.settled_at = event.timestamp
        state.settled_by = p.get("settled_by", "auto")
        state.was_correct = p.get("was_correct")
        state.actual_return = p.get("actual_return")

    elif t == EventType.HUMAN_VERIFIED:
        state.verified = True
        state.verifier_notes = p.get("verifier_notes", "")

    elif t == EventType.PREDICTION_INVALIDATED:
        state.invalidated = True
        state.invalidation_reason = p.get("reason", "")

    elif t == EventType.EVENT_PREDICTION:
        state.metadata["event_prediction"] = {
            "event_name": p.get("event_name", ""),
            "direction": p.get("direction", ""),
            "score": p.get("score", 0),
        }

    elif t == EventType.EVENT_OBSERVED:
        state.metadata["event_observed"] = {
            "event_name": p.get("event_name", ""),
            "actual": p.get("actual", ""),
            "was_correct": p.get("was_correct"),
        }

    elif t == EventType.ANOMALY_DETECTED:
        anomalies = state.metadata.setdefault("anomalies", [])
        anomalies.append({
            "type": p.get("anomaly_type", ""),
            "severity": p.get("severity", ""),
            "description": p.get("description", ""),
            "detected_at": event.timestamp.isoformat(),
        })

    elif t == EventType.HUMAN_REVIEW_SUBMITTED:
        state.metadata["human_review"] = {
            "verdict": p.get("verdict", ""),
            "notes": p.get("notes", ""),
        }


def _serialize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """序列化 payload 中不可 JSON 的类型."""
    result: dict[str, Any] = {}
    for k, v in payload.items():
        if isinstance(v, datetime):
            result[k] = v.isoformat()
        elif isinstance(v, EvidenceSnapshot):
            result[k] = _serialize_snapshot(v)
        elif isinstance(v, tuple):
            result[k] = list(v)
        elif isinstance(v, list):
            result[k] = [
                _serialize_snapshot(item) if isinstance(item, EvidenceSnapshot)
                else item.isoformat() if isinstance(item, datetime)
                else item
                for item in v
            ]
        else:
            result[k] = v
    return result


def _serialize_snapshot(snap: EvidenceSnapshot) -> dict[str, Any]:
    return {
        "snapshot_id": snap.snapshot_id,
        "prediction_id": snap.prediction_id,
        "timestamp": snap.timestamp.isoformat(),
        "spot_gold": snap.spot_gold,
        "dxy": snap.dxy,
        "silver": snap.silver,
        "real_rate": snap.real_rate,
        "breakeven": snap.breakeven,
        "gold_silver_ratio": snap.gold_silver_ratio,
        "composite_score": snap.composite_score,
        "confidence": snap.confidence,
        "source_type": snap.source_type,
        "signals_summary": [
            {
                "name": s.name,
                "dimension": s.dimension,
                "direction": s.direction,
                "score": s.score,
                "description": s.description,
            }
            for s in snap.signals_summary
        ],
        "dimension_scores": snap.dimension_scores,
        "source_refs": [
            {
                "ref_type": r.ref_type,
                "ref_id": r.ref_id,
                "url": r.url,
                "title": r.title,
                "description": r.description,
            }
            for r in snap.source_refs
        ],
    }


def _deserialize_snapshot(data: dict[str, Any]) -> EvidenceSnapshot:
    from gold_miner.events.models import SignalSnapshot, SourceRef

    return EvidenceSnapshot(
        snapshot_id=data.get("snapshot_id", ""),
        prediction_id=data.get("prediction_id", ""),
        timestamp=datetime.fromisoformat(data["timestamp"])
        if data.get("timestamp") else datetime.now(),
        spot_gold=float(data.get("spot_gold", 0)),
        dxy=data.get("dxy"),
        silver=data.get("silver"),
        real_rate=data.get("real_rate"),
        breakeven=data.get("breakeven"),
        gold_silver_ratio=data.get("gold_silver_ratio"),
        composite_score=float(data.get("composite_score", 0)),
        confidence=float(data.get("confidence", 0)),
        source_type=data.get("source_type", "scan"),
        signals_summary=tuple(
            SignalSnapshot.from_dict(s)
            for s in data.get("signals_summary", [])
        ),
        dimension_scores=data.get("dimension_scores", {}),
        source_refs=tuple(
            SourceRef.from_dict(r)
            for r in data.get("source_refs", [])
        ),
    )
