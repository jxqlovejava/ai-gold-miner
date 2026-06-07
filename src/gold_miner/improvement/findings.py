"""改进建议生成器 — 基于效能分析生成分级、可操作的发现."""

from dataclasses import dataclass, field

from gold_miner.improvement.analyzer import AnalysisResult, PerDimensionAccuracy, PerSignalAccuracy
from gold_miner.improvement.tracker import PredictionRecord
from gold_miner.signals.engine import DimensionWeights


@dataclass
class Finding:
    finding_type: str
    severity: str  # high / medium / low
    title: str
    description: str
    dimension: str = ""
    signal_name: str | None = None
    current_value: float = 0.0
    suggested_value: float | None = None
    recommendation: str = ""


class FindingGenerator:
    """基于效能分析生成改进建议.

    三种发现类型:
    1. underperforming_signal — 某信号准确率 < 50%
    2. weight_misalignment — 某维度权重与准确率偏差大
    3. low_confidence — 低置信度预测却正确 (系统低估自身)
    """

    SIGNAL_MIN_ACCURACY = 0.50
    SIGNAL_MIN_SAMPLES = 3
    WEIGHT_MAX_GAP = 0.30  # 权重与准确率差距 > 30% → 高严重度

    def __init__(self, weights: DimensionWeights | None = None) -> None:
        self.weights = weights or DimensionWeights()

    def generate(
        self,
        analysis: AnalysisResult,
        predictions: list[PredictionRecord] | None = None,
        min_accuracy: float = 0.50,
    ) -> list[Finding]:
        findings: list[Finding] = []

        findings.extend(self._find_underperforming_signals(analysis, min_accuracy))
        findings.extend(self._find_weight_misalignment(analysis))
        if predictions:
            findings.extend(self._find_low_confidence_calibration(predictions))

        findings.sort(key=lambda f: (0 if f.severity == "high" else 1 if f.severity == "medium" else 2))
        return findings

    def _find_underperforming_signals(
        self, analysis: AnalysisResult, min_accuracy: float
    ) -> list[Finding]:
        findings: list[Finding] = []
        for sig in analysis.per_signal:
            if sig.total < self.SIGNAL_MIN_SAMPLES:
                continue
            if sig.accuracy >= min_accuracy:
                continue

            if sig.accuracy < 0.35:
                severity = "high"
            elif sig.accuracy < 0.45:
                severity = "medium"
            else:
                severity = "low"

            findings.append(Finding(
                finding_type="underperforming_signal",
                severity=severity,
                title=f"信号「{sig.signal_name}」表现不佳",
                description=(
                    f"准确率: {sig.accuracy:.1%} ({sig.correct}/{sig.total})，"
                    f"低于 {min_accuracy:.0%} 阈值。"
                ),
                dimension=sig.dimension,
                signal_name=sig.signal_name,
                current_value=sig.accuracy,
                recommendation=(
                    f"建议检查「{sig.signal_name}」信号生成逻辑，"
                    f"考虑调整阈值参数或评分规则。"
                ),
            ))

        return findings

    def _find_weight_misalignment(
        self, analysis: AnalysisResult
    ) -> list[Finding]:
        findings: list[Finding] = []
        weight_map = {
            "technical": self.weights.technical,
            "fundamental": self.weights.fundamental,
            "news": self.weights.news,
            "sentiment": self.weights.sentiment,
        }

        for dim_acc in analysis.per_dimension:
            weight = weight_map.get(dim_acc.dimension, 0.0)
            if weight <= 0.10:
                continue
            if dim_acc.accuracy >= 0.50:
                continue

            shortfall = 0.50 - dim_acc.accuracy
            if shortfall <= 0.05:
                continue

            if shortfall > 0.25:
                severity = "high"
            elif shortfall > 0.10:
                severity = "medium"
            else:
                severity = "low"

            suggested = max(0.05, round(weight * (dim_acc.accuracy / 0.50), 2))

            findings.append(Finding(
                finding_type="weight_misalignment",
                severity=severity,
                title=f"维度「{dim_acc.dimension}」权重与准确率不匹配",
                description=(
                    f"当前权重: {weight:.0%} | 准确率: {dim_acc.accuracy:.1%} "
                    f"({dim_acc.correct}/{dim_acc.total}) | 偏差: {shortfall:.0%}"
                ),
                dimension=dim_acc.dimension,
                current_value=weight,
                suggested_value=suggested,
                recommendation=(
                    f"建议将「{dim_acc.dimension}」权重从 {weight:.0%} "
                    f"降至 {suggested:.0%}（与 {dim_acc.accuracy:.1%} 准确率匹配）。"
                ),
            ))

        return findings

    def _find_low_confidence_calibration(
        self, predictions: list[PredictionRecord]
    ) -> list[Finding]:
        resolved = [p for p in predictions if p.actual_price is not None]
        low_conf_correct = [
            p for p in resolved if p.confidence < 0.5 and p.was_correct
        ]
        if len(low_conf_correct) < 2:
            return []

        return [Finding(
            finding_type="low_confidence",
            severity="low",
            title="系统低估自身预测能力",
            description=(
                f"{len(low_conf_correct)} 个低置信度预测 (< 50%) 实际正确。"
                f"置信度校准可能需要调整。"
            ),
            recommendation="建议适当降低置信度计算中的信号数量权重，或提高对齐度的权重。",
        )]
