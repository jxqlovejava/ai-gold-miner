"""预测追踪器 — 记录信号预测并结算实际结果.

模式: 每次 scan 后自动保存 PredictionRecord (JSONL)，
后续手动结算 (resolve) 实际价格后生成准确率数据。
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from gold_miner.storage import get_store


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
        self.records.append(record)
        self._append(record)
        logger.info(f"预测已记录 (id: {record.id}, 方向: {record.direction}, 仓位: {record.position_pct:.0%})")

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
                record.actual_price = actual_price
                record.resolved_at = datetime.now()
                record.actual_return = (
                    (actual_price - record.current_price) / record.current_price
                )

                # 方向正确性判定
                direction = record.direction
                ret = record.actual_return
                if direction == "buy":
                    record.was_correct = ret > 0
                elif direction == "sell":
                    record.was_correct = ret < 0
                else:  # hold / neutral
                    record.was_correct = abs(ret) < 0.01

                self._rewrite()
                return record
        return None

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
        return {
            "total": total,
            "resolved": len(resolved),
            "unresolved": len(unresolved),
            "correct": correct,
            "accuracy": correct / len(resolved) if resolved else 0.0,
        }

    def recent(self, n: int = 10) -> list[PredictionRecord]:
        return sorted(self.records, key=lambda r: r.timestamp, reverse=True)[:n]
