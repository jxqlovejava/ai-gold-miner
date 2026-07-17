"""预测追踪器 — 记录信号预测并结算实际结果.

模式: 每次 scan 后自动保存 PredictionRecord (JSONL)，
后续手动结算 (resolve) 实际价格后生成准确率数据。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from gold_miner.storage import get_store

# Directional correctness: any positive / negative move counts (matches EventStore resolver).
_DIRECTIONAL_THRESHOLD = 0.0
# Neutral / hold: treat as correct when price stays within this band.
_NEUTRAL_BAND = 0.015

_BULLISH_ALIASES = frozenset({
    "long", "buy", "bullish", "做多", "看多", "买入", "多",
})
_BEARISH_ALIASES = frozenset({
    "short", "sell", "bearish", "做空", "看空", "卖出", "空",
})
_NEUTRAL_ALIASES = frozenset({
    "neutral", "hold", "flat", "观望", "中性", "持有", "空仓",
})

# Fixture prices often used in unit tests — excluded from accuracy_ex_test.
_TEST_PRICES = frozenset({1000.0, 2000.0})


def normalize_direction(direction: str) -> str:
    """Normalize direction labels to long | short | neutral."""
    key = (direction or "").strip().lower()
    if key in _BULLISH_ALIASES:
        return "long"
    if key in _BEARISH_ALIASES:
        return "short"
    if key in _NEUTRAL_ALIASES:
        return "neutral"
    # Unknown → neutral (safe default for accuracy stats).
    return "neutral"


def determine_correctness(direction: str, actual_return: float) -> bool:
    """Judge whether a prediction direction was correct given realized return."""
    side = normalize_direction(direction)
    if side == "long":
        return actual_return > _DIRECTIONAL_THRESHOLD
    if side == "short":
        return actual_return < -_DIRECTIONAL_THRESHOLD
    return abs(actual_return) < _NEUTRAL_BAND


@dataclass
class PredictionRecord:
    """单条预测记录."""

    id: str
    timestamp: datetime
    current_price: float
    signals: list[dict[str, Any]]
    composite_score: float
    confidence: float
    direction: str
    position_pct: float
    dimension_scores: dict[str, float] = field(default_factory=dict)
    actual_price: float | None = None
    resolved_at: datetime | None = None
    actual_return: float | None = None
    was_correct: bool | None = None
    invalidated: bool = False
    invalidation_reason: str = ""


class PredictionTracker:
    """预测追踪器 — JSONL 持久化，与 TradeJournal 模式一致."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir  # 保留参数用于兼容，但实际使用存储层
        self._store = get_store(private_data_dir=data_dir)
        self.records: list[PredictionRecord] = []
        self._load()

    def _load(self) -> None:
        raw_records = self._store.load_predictions()
        for data in raw_records:
            try:
                data["timestamp"] = datetime.fromisoformat(data["timestamp"])
                if data.get("resolved_at"):
                    data["resolved_at"] = datetime.fromisoformat(data["resolved_at"])
                self.records.append(PredictionRecord(**data))
            except (KeyError, ValueError):
                continue

    def record_prediction(self, record: PredictionRecord) -> None:
        record.direction = normalize_direction(record.direction)
        self.records.append(record)
        self._append(record)
        logger.info(
            f"预测已记录 (id: {record.id}, 方向: {record.direction}, "
            f"仓位: {record.position_pct:.0%})"
        )

    def _append(self, record: PredictionRecord) -> None:
        data = asdict(record)
        data["timestamp"] = record.timestamp.isoformat()
        if record.resolved_at:
            data["resolved_at"] = record.resolved_at.isoformat()
        self._store.append_prediction(data)

    def resolve_prediction(
        self, prediction_id: str, actual_price: float
    ) -> PredictionRecord | None:
        """用实际价格结算预测，计算正确性和收益率."""
        for record in self.records:
            if record.id == prediction_id and record.actual_price is None and not record.invalidated:
                return self._settle(record, actual_price, rewrite=True)
        return None

    def _settle(
        self,
        record: PredictionRecord,
        actual_price: float,
        *,
        rewrite: bool = True,
    ) -> PredictionRecord:
        """Settle a single unresolved record in place."""
        record.actual_price = actual_price
        record.resolved_at = datetime.now()
        if record.current_price:
            record.actual_return = (
                (actual_price - record.current_price) / record.current_price
            )
        else:
            record.actual_return = 0.0
        record.was_correct = determine_correctness(
            record.direction, record.actual_return
        )
        if rewrite:
            self._rewrite()
        return record

    def auto_resolve_stale(
        self,
        current_price: float,
        min_age_hours: float = 24,
        horizons_hours: list | None = None,
    ) -> list[PredictionRecord]:
        """Auto-resolve unresolved, non-invalidated predictions older than min_age_hours.

        If ``horizons_hours`` is provided, a record is resolved when its age reaches
        the first horizon that it has exceeded (same effect as min age for the
        smallest horizon). Default behaviour: resolve once when age >= min_age_hours.
        """
        now = datetime.now()
        # If horizons provided, due when age exceeds any listed horizon; else use min_age_hours.
        age_thresholds = (
            [timedelta(hours=float(h)) for h in horizons_hours]
            if horizons_hours
            else [timedelta(hours=min_age_hours)]
        )

        newly_resolved: list[PredictionRecord] = []
        for record in self.records:
            if record.actual_price is not None or record.invalidated:
                continue
            age = now - record.timestamp
            if not any(age >= t for t in age_thresholds):
                continue
            self._settle(record, current_price, rewrite=False)
            newly_resolved.append(record)

        if newly_resolved:
            self._rewrite()
            logger.info(
                f"自动结算 {len(newly_resolved)} 条过期预测 "
                f"(price={current_price}, min_age_hours={min_age_hours})"
            )
        return newly_resolved

    def invalidate_prediction(
        self, prediction_id: str, reason: str = ""
    ) -> PredictionRecord | None:
        """将预测标记为无效，不再参与准确率统计."""
        for record in self.records:
            if record.id == prediction_id and not record.invalidated:
                record.invalidated = True
                record.invalidation_reason = reason
                self._rewrite()
                return record
        return None

    def _rewrite(self) -> None:
        records_data = []
        for record in self.records:
            data = asdict(record)
            data["timestamp"] = record.timestamp.isoformat()
            if record.resolved_at:
                data["resolved_at"] = record.resolved_at.isoformat()
            records_data.append(data)
        self._store.save_predictions(records_data)

    def load_all(self) -> list[PredictionRecord]:
        return list(self.records)

    def list_unresolved(self) -> list[PredictionRecord]:
        return [r for r in self.records if r.actual_price is None]

    def list_resolved(self) -> list[PredictionRecord]:
        return [r for r in self.records if r.actual_price is not None]

    def stats(self) -> dict[str, Any]:
        total = len(self.records)
        resolved = self.list_resolved()
        unresolved = self.list_unresolved()
        correct = sum(1 for r in resolved if r.was_correct)

        # Optional: accuracy excluding common unit-test fixtures.
        ex_test = [
            r for r in resolved
            if not r.invalidated
            and r.current_price not in _TEST_PRICES
            and not str(r.id).startswith("r")
        ]
        correct_ex = sum(1 for r in ex_test if r.was_correct)
        accuracy_ex_test = correct_ex / len(ex_test) if ex_test else 0.0

        return {
            "total": total,
            "resolved": len(resolved),
            "unresolved": len(unresolved),
            "correct": correct,
            "accuracy": correct / len(resolved) if resolved else 0.0,
            "accuracy_ex_test": accuracy_ex_test,
            "resolved_ex_test": len(ex_test),
        }

    def recent(self, n: int = 10) -> list[PredictionRecord]:
        return sorted(self.records, key=lambda r: r.timestamp, reverse=True)[:n]
