"""文章分析日志 — JSONL 持久化."""

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from gold_miner.config import settings


@dataclass
class ArticleRecord:
    """单篇文章分析记录."""

    id: str
    source_url: str
    title: str
    text_preview: str  # 前200字
    word_count: int

    # 规则分析
    sentiment_score: float
    sentiment_direction: str
    manipulation_score: int
    manipulation_flags: list[str]
    is_suspicious: bool
    claims: list[dict[str, str]]

    # LLM 增强 (可选)
    llm_analysis: dict[str, Any] | None = None

    # 交叉验证 (可选)
    cross_ref: dict[str, Any] | None = None

    # 价格预判 (可选)
    forecast_direction: str | None = None
    forecast_confidence: float | None = None
    forecast_horizon_days: int | None = None
    forecast_target_pct: float | None = None
    forecast_reasoning: str = ""

    # 元数据
    created_at: datetime = field(default_factory=datetime.now)
    status: str = "analyzed"  # analyzed / cross_referenced / forecasted


class ArticleJournal:
    """文章分析日志 — JSONL 持久化."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or settings.data_path
        self.journal_file = self.data_dir / "article_journal.jsonl"
        self.records: list[ArticleRecord] = []
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
                    data["created_at"] = datetime.fromisoformat(data["created_at"])
                    self.records.append(ArticleRecord(**data))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

    def save(self, record: ArticleRecord) -> None:
        self.records.append(record)
        self._append(record)
        logger.info(f"文章分析已保存 (id: {record.id}, 方向: {record.sentiment_direction})")

    def _append(self, record: ArticleRecord) -> None:
        data = asdict(record)
        data["created_at"] = record.created_at.isoformat()
        with open(self.journal_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

    def get(self, record_id: str) -> ArticleRecord | None:
        for r in self.records:
            if r.id == record_id:
                return r
        return None

    def update(self, record_id: str, **kwargs: Any) -> ArticleRecord | None:
        for i, r in enumerate(self.records):
            if r.id == record_id:
                for k, v in kwargs.items():
                    if hasattr(r, k):
                        setattr(r, k, v)
                self._rewrite()
                return r
        return None

    def _rewrite(self) -> None:
        with open(self.journal_file, "w", encoding="utf-8") as f:
            for record in self.records:
                data = asdict(record)
                data["created_at"] = record.created_at.isoformat()
                f.write(json.dumps(data, ensure_ascii=False) + "\n")

    def list_all(self) -> list[ArticleRecord]:
        return sorted(self.records, key=lambda r: r.created_at, reverse=True)

    def list_forecasted(self) -> list[ArticleRecord]:
        return [r for r in self.records if r.forecast_direction is not None]

    def list_unverified(self) -> list[ArticleRecord]:
        """返回有预判但未与 PredictionTracker 关联的记录."""
        return [
            r for r in self.records
            if r.forecast_direction is not None and r.status == "forecasted"
        ]
