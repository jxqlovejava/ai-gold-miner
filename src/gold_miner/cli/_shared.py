"""Shared helpers for CLI commands."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime

import pandas as pd
from loguru import logger

from gold_miner.events.models import EventType, EvidenceSnapshot
from gold_miner.events.store import EventStore
from gold_miner.improvement.tracker import PredictionRecord, PredictionTracker
from gold_miner.signals.base import SignalBundle


def _record_prediction_events(
    bundle: SignalBundle,
    decision: dict,
    current_price: float,
    dxy_df: pd.DataFrame | None = None,
    rate_df: pd.DataFrame | None = None,
    silver_df: pd.DataFrame | None = None,
    breakeven_df: pd.DataFrame | None = None,
    source: str = "scan",
    source_refs: list[dict] | None = None,
    horizon_days: int = 7,
) -> str:
    """向 EventStore 写入 prediction_made + evidence_attached."""
    prediction_id = uuid.uuid4().hex[:12]
    store = EventStore()

    direction = decision.get("direction", "neutral")
    store.append(
        EventType.PREDICTION_MADE,
        prediction_id,
        {
            "direction": direction,
            "composite_score": round(bundle.composite_score, 4),
            "confidence": round(bundle.confidence, 4),
            "position_pct": decision.get("position_pct", 0),
            "horizon_days": horizon_days,
            "source": source,
            "auto_resolve": horizon_days <= 7,
            "current_price": round(current_price, 2),
        },
    )

    dxy_val = float(dxy_df["value"].iloc[-1]) if dxy_df is not None and not dxy_df.empty else None
    silver_val = float(silver_df["value"].iloc[-1]) if silver_df is not None and not silver_df.empty else None
    rate_val = float(rate_df["value"].iloc[-1]) if rate_df is not None and not rate_df.empty else None
    breakeven_val = float(breakeven_df["value"].iloc[-1]) if breakeven_df is not None and not breakeven_df.empty else None

    gsr: float | None = None
    if current_price > 0 and silver_val and silver_val > 0:
        gsr = round(current_price / silver_val, 1)

    dim_scores: dict[str, float] = {}
    for dim in ["technical", "fundamental", "news", "sentiment", "smart_money"]:
        signals = bundle.by_dimension(dim)
        if signals:
            dim_scores[dim] = round(sum(s.score for s in signals) / len(signals), 2)
        else:
            dim_scores[dim] = 0.0

    serialized_signals: list[dict] = []
    for s in bundle.signals:
        sd = asdict(s)
        sd["timestamp"] = sd["timestamp"].isoformat()
        serialized_signals.append(sd)

    snapshot = EvidenceSnapshot.from_price_data(
        prediction_id=prediction_id,
        spot_gold=round(current_price, 2),
        dxy=round(dxy_val, 2) if dxy_val else None,
        silver=round(silver_val, 2) if silver_val else None,
        real_rate=round(rate_val, 2) if rate_val else None,
        breakeven=round(breakeven_val, 2) if breakeven_val else None,
        gold_silver_ratio=gsr,
        signals=serialized_signals,
        dimension_scores=dim_scores,
        composite_score=round(bundle.composite_score, 4),
        confidence=round(bundle.confidence, 4),
        source_type=source,
        source_refs=source_refs,
    )
    store.append(
        EventType.EVIDENCE_ATTACHED,
        prediction_id,
        {"snapshot": snapshot},
    )

    logger.debug(f"EventStore 已记录: {prediction_id[:8]}... ({source}, {direction})")
    return prediction_id


def _auto_track_prediction(
    bundle: SignalBundle,
    decision: dict,
    current_price: float,
) -> None:
    """自动记录预测到预测追踪器."""
    dim_scores: dict[str, float] = {}
    for dim in ["technical", "fundamental", "news", "sentiment", "smart_money"]:
        signals = bundle.by_dimension(dim)
        if signals:
            dim_scores[dim] = round(sum(s.score for s in signals) / len(signals), 2)
        else:
            dim_scores[dim] = 0.0

    serialized_signals: list[dict] = []
    for s in bundle.signals:
        sd = asdict(s)
        sd["timestamp"] = sd["timestamp"].isoformat()
        serialized_signals.append(sd)

    record = PredictionRecord(
        id=uuid.uuid4().hex[:12],
        timestamp=datetime.now(),
        current_price=current_price,
        signals=serialized_signals,
        composite_score=bundle.composite_score,
        confidence=bundle.confidence,
        direction=decision.get("direction", "neutral"),
        position_pct=decision.get("position_pct", 0),
        dimension_scores=dim_scores,
    )
    PredictionTracker().record_prediction(record)
