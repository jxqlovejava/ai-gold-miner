"""效能分析器 — 按维度和信号统计预测准确率."""

from collections import defaultdict
from dataclasses import dataclass, field

from gold_miner.improvement.tracker import PredictionRecord
from gold_miner.signals.base import SignalDirection


@dataclass
class PerDimensionAccuracy:
    dimension: str
    total: int
    correct: int
    accuracy: float
    avg_score: float = 0.0


@dataclass
class PerSignalAccuracy:
    signal_name: str
    dimension: str
    total: int
    correct: int
    accuracy: float


@dataclass
class AnalysisResult:
    total_predictions: int
    resolved_predictions: int
    overall_accuracy: float
    per_dimension: list[PerDimensionAccuracy] = field(default_factory=list)
    per_signal: list[PerSignalAccuracy] = field(default_factory=list)
    direction_accuracy: dict[str, float] = field(default_factory=dict)
    avg_return: float = 0.0
    prediction_samples: list[PredictionRecord] = field(default_factory=list)


class PerformanceAnalyzer:
    """信号预测效能分析器."""

    @staticmethod
    def _signal_correct(direction: str, actual_return: float) -> bool:
        """判断单条信号方向与实际走势是否一致."""
        if direction == SignalDirection.BULLISH:
            return actual_return > 0
        if direction == SignalDirection.BEARISH:
            return actual_return < 0
        return True  # neutral signals can't be "wrong"

    def analyze(self, predictions: list[PredictionRecord]) -> AnalysisResult:
        resolved = [p for p in predictions if p.actual_price is not None]
        if not resolved:
            return AnalysisResult(
                total_predictions=len(predictions),
                resolved_predictions=0,
                overall_accuracy=0.0,
            )

        correct_predictions = sum(1 for p in resolved if p.was_correct)
        overall = correct_predictions / len(resolved) if resolved else 0.0

        # 按维度统计
        dim_stats = self._analyze_dimensions(resolved)
        # 按单信号统计
        signal_stats = self._analyze_signals(resolved)
        # 方向准确率
        direction_acc = self._analyze_direction(resolved)
        # 平均收益
        avg_ret = sum(p.actual_return for p in resolved if p.actual_return is not None) / len(resolved)

        return AnalysisResult(
            total_predictions=len(predictions),
            resolved_predictions=len(resolved),
            overall_accuracy=round(overall, 4),
            per_dimension=dim_stats,
            per_signal=signal_stats,
            direction_accuracy=direction_acc,
            avg_return=round(avg_ret, 4),
            prediction_samples=resolved[-20:],
        )

    def _analyze_dimensions(
        self, resolved: list[PredictionRecord]
    ) -> list[PerDimensionAccuracy]:
        dim_scores: dict[str, list[float]] = defaultdict(list)
        dim_correct: dict[str, list[bool]] = defaultdict(list)

        for p in resolved:
            for dim, score in p.dimension_scores.items():
                dim_scores[dim].append(score)
                is_correct = score > 0 and p.actual_return and p.actual_return > 0 or \
                             score < 0 and p.actual_return and p.actual_return < 0
                dim_correct[dim].append(is_correct)

        result: list[PerDimensionAccuracy] = []
        for dim in sorted(dim_scores):
            scores = dim_scores[dim]
            corrects = dim_correct[dim]
            total = len(scores)
            correct = sum(corrects)
            result.append(PerDimensionAccuracy(
                dimension=dim,
                total=total,
                correct=correct,
                accuracy=round(correct / total, 4) if total > 0 else 0.0,
                avg_score=round(sum(scores) / total, 4) if total > 0 else 0.0,
            ))

        return result

    def _analyze_signals(
        self, resolved: list[PredictionRecord]
    ) -> list[PerSignalAccuracy]:
        sig_correct: dict[tuple[str, str], list[bool]] = defaultdict(list)

        for p in resolved:
            if p.actual_return is None:
                continue
            for s in p.signals:
                name = s.get("name", "")
                dim = s.get("dimension", "")
                direction = s.get("direction", "")
                correct = self._signal_correct(direction, p.actual_return)
                sig_correct[(name, dim)].append(correct)

        result: list[PerSignalAccuracy] = []
        for (name, dim), corrects in sorted(sig_correct.items(), key=lambda x: -len(x[1])):
            total = len(corrects)
            correct = sum(corrects)
            result.append(PerSignalAccuracy(
                signal_name=name,
                dimension=dim,
                total=total,
                correct=correct,
                accuracy=round(correct / total, 4) if total > 0 else 0.0,
            ))

        return result

    def _analyze_direction(
        self, resolved: list[PredictionRecord]
    ) -> dict[str, float]:
        dir_stats: dict[str, list[bool]] = defaultdict(list)
        for p in resolved:
            dir_stats[p.direction].append(bool(p.was_correct))

        return {
            d: round(sum(corrects) / len(corrects), 4) if corrects else 0.0
            for d, corrects in sorted(dir_stats.items())
        }
