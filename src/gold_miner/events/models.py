"""事件溯源模型 — 不可变事件类型、证据快照、预测状态."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    PREDICTION_MADE = "prediction_made"
    EVIDENCE_ATTACHED = "evidence_attached"
    PRICE_OBSERVED = "price_observed"
    PREDICTION_SETTLED = "prediction_settled"
    HUMAN_VERIFIED = "human_verified"
    PREDICTION_INVALIDATED = "prediction_invalidated"
    REPORT_GENERATED = "report_generated"
    EVENT_PREDICTION = "event_prediction"
    EVENT_OBSERVED = "event_observed"
    ANOMALY_DETECTED = "anomaly_detected"
    HUMAN_REVIEW_SUBMITTED = "human_review_submitted"


# ---------------------------------------------------------------------------
# 不可变值对象
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalSnapshot:
    name: str
    dimension: str
    direction: str
    score: float
    description: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SignalSnapshot":
        return cls(
            name=d.get("name", ""),
            dimension=d.get("dimension", ""),
            direction=d.get("direction", ""),
            score=float(d.get("score", 0)),
            description=d.get("description", ""),
        )


@dataclass(frozen=True)
class SourceRef:
    ref_type: str  # article | data_source | url | text
    ref_id: str = ""
    url: str = ""
    title: str = ""
    description: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SourceRef":
        return cls(
            ref_type=d.get("ref_type", ""),
            ref_id=d.get("ref_id", ""),
            url=d.get("url", ""),
            title=d.get("title", ""),
            description=d.get("description", ""),
        )


@dataclass(frozen=True)
class EvidenceSnapshot:
    """不可变证据快照 — 记录预测时的完整上下文."""
    snapshot_id: str
    prediction_id: str
    timestamp: datetime = field(default_factory=datetime.now)

    # 原始价格数据
    spot_gold: float = 0.0
    dxy: float | None = None
    silver: float | None = None
    real_rate: float | None = None
    breakeven: float | None = None
    gold_silver_ratio: float | None = None

    # 信号摘要
    signals_summary: tuple[SignalSnapshot, ...] = ()
    dimension_scores: dict[str, float] = field(default_factory=dict)

    # 复合评分
    composite_score: float = 0.0
    confidence: float = 0.0

    # 来源引用
    source_type: str = "scan"  # scan | article | manual
    source_refs: tuple[SourceRef, ...] = ()

    @classmethod
    def from_price_data(
        cls,
        prediction_id: str,
        spot_gold: float,
        *,
        dxy: float | None = None,
        silver: float | None = None,
        real_rate: float | None = None,
        breakeven: float | None = None,
        gold_silver_ratio: float | None = None,
        signals: list[dict[str, Any]] | None = None,
        dimension_scores: dict[str, float] | None = None,
        composite_score: float = 0.0,
        confidence: float = 0.0,
        source_type: str = "scan",
        source_refs: list[dict[str, Any]] | None = None,
    ) -> "EvidenceSnapshot":
        sig_snapshots = tuple(
            SignalSnapshot.from_dict(s) for s in (signals or [])
        )
        refs = tuple(
            SourceRef.from_dict(r) for r in (source_refs or [])
        )
        return cls(
            snapshot_id=uuid.uuid4().hex[:12],
            prediction_id=prediction_id,
            spot_gold=spot_gold,
            dxy=dxy,
            silver=silver,
            real_rate=real_rate,
            breakeven=breakeven,
            gold_silver_ratio=gold_silver_ratio,
            signals_summary=sig_snapshots,
            dimension_scores=dimension_scores or {},
            composite_score=composite_score,
            confidence=confidence,
            source_type=source_type,
            source_refs=refs,
        )


# ---------------------------------------------------------------------------
# 事件
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    """不可变事件."""
    event_id: str
    timestamp: datetime
    event_type: EventType
    prediction_id: str
    payload: dict[str, Any]

    @classmethod
    def create(
        cls,
        event_type: EventType,
        prediction_id: str,
        payload: dict[str, Any],
    ) -> "Event":
        return cls(
            event_id=uuid.uuid4().hex[:12],
            timestamp=datetime.now(),
            event_type=event_type,
            prediction_id=prediction_id,
            payload=payload,
        )


# ---------------------------------------------------------------------------
# 预测状态 (通过重放事件计算)
# ---------------------------------------------------------------------------


@dataclass
class PredictionState:
    """重放事件计算得出的预测完整状态."""
    prediction_id: str
    created_at: datetime | None = None

    # prediction_made
    direction: str = "neutral"
    composite_score: float = 0.0
    confidence: float = 0.0
    position_pct: float = 0.0
    horizon_days: int = 7
    source: str = "scan"  # scan | article | manual
    auto_resolve: bool = True
    current_price: float = 0.0

    # evidence
    evidence_snapshots: list[EvidenceSnapshot] = field(default_factory=list)

    # settlement
    observed_price: float | None = None
    observed_at: datetime | None = None
    settled: bool = False
    settled_at: datetime | None = None
    settled_by: str = ""  # auto | human
    was_correct: bool | None = None
    actual_return: float | None = None

    # human verification
    verified: bool = False
    verifier_notes: str = ""

    # invalidation
    invalidated: bool = False
    invalidation_reason: str = ""

    # metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        if self.invalidated:
            return "invalidated"
        if self.verified:
            return "verified"
        if self.settled:
            return "settled"
        if self.observed_price is not None:
            return "price_observed"
        return "pending"

    @property
    def is_due(self) -> bool:
        """预测是否已到期."""
        if self.created_at is None:
            return False
        from datetime import timedelta
        return datetime.now() > self.created_at + timedelta(days=self.horizon_days)
