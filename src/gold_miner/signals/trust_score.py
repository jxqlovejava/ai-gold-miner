"""信息源可信度评分 — 基于历史预测准确率的动态权重."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from loguru import logger

from gold_miner.config import settings
from gold_miner.signals.base import SignalBundle


@dataclass
class SourceRecord:
    source_name: str
    total_predictions: int = 0
    correct_predictions: int = 0
    last_updated: datetime = field(default_factory=datetime.now)
    base_score: float = 0.7  # 初始信任度

    @property
    def accuracy(self) -> float:
        if self.total_predictions == 0:
            return self.base_score
        return self.correct_predictions / self.total_predictions


@dataclass
class TrustScore:
    source_name: str
    current_score: float
    accuracy_component: float
    recency_component: float
    decay_factor: float


class TrustStore:
    """信息源可信度存储.

    数据保存在 data/trust_store.json。
    """

    DECAY_DAYS = 30  # 超过此天数准确率权重递减
    MIN_SCORE = 0.2
    DEFAULT_SCORE = 0.7

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or settings.data_path
        self.file = self.data_dir / "trust_store.json"
        self._records: dict[str, SourceRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.file.exists():
            return
        try:
            data = json.loads(self.file.read_text())
            for name, rec in data.items():
                self._records[name] = SourceRecord(
                    source_name=name,
                    total_predictions=rec.get("total_predictions", 0),
                    correct_predictions=rec.get("correct_predictions", 0),
                    last_updated=(
                        datetime.fromisoformat(rec["last_updated"])
                        if rec.get("last_updated") else datetime.now()
                    ),
                    base_score=rec.get("base_score", self.DEFAULT_SCORE),
                )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"TrustStore加载失败: {e}")

    def _save(self) -> None:
        data = {}
        for name, rec in self._records.items():
            data[name] = {
                "total_predictions": rec.total_predictions,
                "correct_predictions": rec.correct_predictions,
                "last_updated": rec.last_updated.isoformat(),
                "base_score": rec.base_score,
            }
        self.file.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def record_outcome(self, source: str, was_correct: bool) -> None:
        """记录信号源的一次预测结果."""
        if source not in self._records:
            self._records[source] = SourceRecord(source_name=source)

        rec = self._records[source]
        rec.total_predictions += 1
        if was_correct:
            rec.correct_predictions += 1
        rec.last_updated = datetime.now()
        self._save()

    def score_for(self, source: str) -> TrustScore:
        """计算信息源当前可信度."""
        rec = self._records.get(source)
        if rec is None:
            return TrustScore(
                source_name=source,
                current_score=self.DEFAULT_SCORE,
                accuracy_component=self.DEFAULT_SCORE,
                recency_component=0.5,
                decay_factor=0.0,
            )

        accuracy = rec.accuracy

        # 时间衰减: 30天以上无更新，准确率权重逐步降低
        days_since = (datetime.now() - rec.last_updated).days
        recency = max(0.3, 1.0 - max(0, days_since - self.DECAY_DAYS) / 60)

        # 样本量惩罚: 预测少于5次，可信度打折
        sample_penalty = min(rec.total_predictions / 5, 1.0)

        current = max(self.MIN_SCORE, accuracy * 0.6 + recency * 0.3 + sample_penalty * 0.1)
        return TrustScore(
            source_name=source,
            current_score=round(current, 3),
            accuracy_component=round(accuracy, 3),
            recency_component=round(recency, 3),
            decay_factor=round(max(0, days_since - self.DECAY_DAYS) / 60, 3),
        )

    def apply_to_signals(self, bundle: SignalBundle) -> SignalBundle:
        """按信息源可信度缩放信号分数.

        仅对 news 维度的信号生效.
        """
        news_sigs = bundle.by_dimension("news")
        if not news_sigs:
            return bundle

        for signal in bundle.signals:
            if signal.dimension != "news":
                continue
            source = signal.metadata.get("source", "")
            trust = self.score_for(source)
            original = signal.score
            signal.score = round(original * (0.5 + 0.5 * trust.current_score), 2)
            if abs(original - signal.score) > 0.1:
                logger.debug(
                    f"可信度调整: [{source}] {original:+.2f} → {signal.score:+.2f} "
                    f"(可信度 {trust.current_score:.0%})"
                )

        return bundle

    def downgrade_source(self, source: str, penalty: float = 0.2) -> None:
        """人工降级信息源."""
        if source not in self._records:
            self._records[source] = SourceRecord(source_name=source)
        rec = self._records[source]
        rec.base_score = max(self.MIN_SCORE, rec.base_score - penalty)
        rec.last_updated = datetime.now()
        self._save()
        logger.info(f"信息源降级: {source} → {rec.base_score:.0%}")

    @property
    def all_sources(self) -> dict[str, TrustScore]:
        return {name: self.score_for(name) for name in self._records}
