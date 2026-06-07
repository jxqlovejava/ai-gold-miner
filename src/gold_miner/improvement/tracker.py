"""预测追踪器 — 记录信号预测并结算实际结果.

模式: 每次 scan 后自动保存 PredictionRecord (JSONL)，
后续手动结算 (resolve) 实际价格后生成准确率数据。
"""

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from gold_miner.config import settings


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


class PredictionTracker:
    """预测追踪器 — JSONL 持久化，与 TradeJournal 模式一致."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or settings.data_path
        self.journal_file = self.data_dir / "prediction_journal.jsonl"
        self.records: list[PredictionRecord] = []
        self._load()

    def _load(self) -> None:
        if not self.journal_file.exists():
            return
        with open(self.journal_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    data["timestamp"] = datetime.fromisoformat(data["timestamp"])
                    if data.get("resolved_at"):
                        data["resolved_at"] = datetime.fromisoformat(data["resolved_at"])
                    self.records.append(PredictionRecord(**data))
                except (json.JSONDecodeError, KeyError, ValueError):
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
        with open(self.journal_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

    def resolve_prediction(
        self, prediction_id: str, actual_price: float
    ) -> PredictionRecord | None:
        """用实际价格结算预测，计算正确性和收益率."""
        for record in self.records:
            if record.id == prediction_id and record.actual_price is None:
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

    def _rewrite(self) -> None:
        with open(self.journal_file, "w", encoding="utf-8") as f:
            for record in self.records:
                data = asdict(record)
                data["timestamp"] = record.timestamp.isoformat()
                if record.resolved_at:
                    data["resolved_at"] = record.resolved_at.isoformat()
                f.write(json.dumps(data, ensure_ascii=False) + "\n")

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
