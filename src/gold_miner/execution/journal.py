"""交易日记 — 记录决策与回测迭代."""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from gold_miner.config import settings


@dataclass
class TradeRecord:
    id: str
    timestamp: datetime
    signal: str
    instrument: str
    position_pct: float
    entry_price: float
    exit_price: float | None = None
    pnl: float | None = None
    pnl_pct: float | None = None
    status: str = "open"
    close_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class TradeJournal:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or settings.data_path
        self.journal_file = self.data_dir / "trade_journal.jsonl"
        self.records: list[TradeRecord] = []
        self._load()

    def _load(self) -> None:
        if not self.journal_file.exists():
            return
        with open(self.journal_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    data["timestamp"] = datetime.fromisoformat(data["timestamp"])
                    if data.get("exit_price") is not None:
                        data["exit_price"] = float(data["exit_price"])
                    self.records.append(TradeRecord(**data))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

    def record(self, trade: TradeRecord) -> None:
        self.records.append(trade)
        self._append(trade)

    def _append(self, trade: TradeRecord) -> None:
        data = asdict(trade)
        data["timestamp"] = trade.timestamp.isoformat()
        with open(self.journal_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

    def close_trade(self, trade_id: str, exit_price: float, reason: str = "manual") -> TradeRecord | None:
        for record in self.records:
            if record.id == trade_id and record.status == "open":
                record.exit_price = exit_price
                record.status = "closed"
                record.close_reason = reason
                if record.entry_price != 0:
                    record.pnl = exit_price - record.entry_price
                    record.pnl_pct = record.pnl / record.entry_price
                self._rewrite()
                return record
        return None

    def _rewrite(self) -> None:
        with open(self.journal_file, "w", encoding="utf-8") as f:
            for record in self.records:
                data = asdict(record)
                data["timestamp"] = record.timestamp.isoformat()
                f.write(json.dumps(data, ensure_ascii=False) + "\n")

    def stats(self) -> dict[str, Any]:
        closed = [r for r in self.records if r.status == "closed" and r.pnl is not None]
        if not closed:
            return {"total_trades": 0, "win_rate": 0.0}
        wins = sum(1 for r in closed if r.pnl and r.pnl > 0)
        total = len(closed)
        win_rate = wins / total if total > 0 else 0.0
        total_pnl = sum(r.pnl for r in closed if r.pnl is not None)
        avg_pnl = total_pnl / total if total > 0 else 0.0
        pnls = [r.pnl for r in closed if r.pnl is not None]
        return {
            "total_trades": total,
            "win_rate": round(win_rate, 2),
            "avg_pnl": round(avg_pnl, 4),
            "total_pnl": round(total_pnl, 4),
            "max_profit": round(max(pnls) if pnls else 0, 4),
            "max_loss": round(min(pnls) if pnls else 0, 4),
        }

    def recent(self, n: int = 10) -> list[TradeRecord]:
        return sorted(self.records, key=lambda r: r.timestamp, reverse=True)[:n]
