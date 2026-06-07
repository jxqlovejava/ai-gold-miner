"""测试改进建议生成器."""

from datetime import datetime

from gold_miner.improvement.analyzer import (
    AnalysisResult,
    PerDimensionAccuracy,
    PerSignalAccuracy,
)
from gold_miner.improvement.findings import Finding, FindingGenerator
from gold_miner.improvement.tracker import PredictionRecord


def _make_result(
    per_signal: list[PerSignalAccuracy] | None = None,
    per_dimension: list[PerDimensionAccuracy] | None = None,
    resolved_count: int = 10,
) -> AnalysisResult:
    return AnalysisResult(
        total_predictions=resolved_count + 5,
        resolved_predictions=resolved_count,
        overall_accuracy=0.6,
        per_signal=per_signal or [],
        per_dimension=per_dimension or [],
        direction_accuracy={"buy": 0.65, "sell": 0.50},
        avg_return=0.008,
    )


class TestFindingGenerator:
    def test_empty_analysis_no_findings(self):
        gen = FindingGenerator()
        analysis = _make_result()
        findings = gen.generate(analysis)
        assert findings == []

    def test_underperforming_signal_high_severity(self):
        gen = FindingGenerator()
        sig = PerSignalAccuracy(
            signal_name="新闻情绪",
            dimension="news",
            total=8,
            correct=2,
            accuracy=0.25,
        )
        analysis = _make_result(per_signal=[sig])
        findings = gen.generate(analysis)

        assert len(findings) == 1
        assert findings[0].finding_type == "underperforming_signal"
        assert findings[0].severity == "high"
        assert findings[0].signal_name == "新闻情绪"

    def test_underperforming_signal_medium_severity(self):
        gen = FindingGenerator()
        sig = PerSignalAccuracy(
            signal_name="COT持仓",
            dimension="sentiment",
            total=5,
            correct=2,
            accuracy=0.40,
        )
        analysis = _make_result(per_signal=[sig])
        findings = gen.generate(analysis)

        assert findings[0].severity == "medium"

    def test_underperforming_signal_low_severity(self):
        gen = FindingGenerator()
        sig = PerSignalAccuracy(
            signal_name="布林带上轨",
            dimension="technical",
            total=5,
            correct=2,
            accuracy=0.48,
        )
        analysis = _make_result(per_signal=[sig])
        findings = gen.generate(analysis)

        assert findings[0].severity == "low"

    def test_high_performing_signal_no_finding(self):
        gen = FindingGenerator()
        sig = PerSignalAccuracy(
            signal_name="RSI超卖",
            dimension="technical",
            total=10,
            correct=7,
            accuracy=0.70,
        )
        analysis = _make_result(per_signal=[sig])
        findings = gen.generate(analysis)
        assert findings == []

    def test_insufficient_samples_no_finding(self):
        gen = FindingGenerator()
        sig = PerSignalAccuracy(
            signal_name="稀有信号",
            dimension="technical",
            total=2,
            correct=0,
            accuracy=0.0,
        )
        analysis = _make_result(per_signal=[sig])
        findings = gen.generate(analysis)
        assert findings == []

    def test_weight_misalignment_high(self):
        gen = FindingGenerator()
        dim = PerDimensionAccuracy(
            dimension="news",
            total=10,
            correct=2,
            accuracy=0.20,
            avg_score=0.1,
        )
        analysis = _make_result(per_dimension=[dim])
        findings = gen.generate(analysis)

        news_findings = [f for f in findings if f.finding_type == "weight_misalignment"]
        assert len(news_findings) == 1
        assert news_findings[0].severity == "high"

    def test_weight_misalignment_medium(self):
        gen = FindingGenerator()
        dim = PerDimensionAccuracy(
            dimension="sentiment",
            total=10,
            correct=3,
            accuracy=0.35,
            avg_score=0.1,
        )
        analysis = _make_result(per_dimension=[dim])
        findings = gen.generate(analysis)

        sentiment = [f for f in findings if f.finding_type == "weight_misalignment"]
        assert len(sentiment) == 1
        assert sentiment[0].severity == "medium"

    def test_suggested_value_reasonable(self):
        gen = FindingGenerator()
        dim = PerDimensionAccuracy(
            dimension="sentiment",
            total=10,
            correct=4,
            accuracy=0.40,
            avg_score=0.1,
        )
        analysis = _make_result(per_dimension=[dim])
        findings = gen.generate(analysis)

        sentiment = [f for f in findings if f.finding_type == "weight_misalignment"]
        if sentiment:
            # suggested = max(0.05, 0.20 * (0.40 / 0.50)) = max(0.05, 0.16) = 0.16
            assert sentiment[0].suggested_value is not None
            assert 0.05 <= sentiment[0].suggested_value < 0.20

    def test_severity_sorting(self):
        gen = FindingGenerator()
        sig_high = PerSignalAccuracy(signal_name="S1", dimension="tech", total=5, correct=1, accuracy=0.20)
        sig_low = PerSignalAccuracy(signal_name="S2", dimension="fund", total=5, correct=2, accuracy=0.48)
        analysis = _make_result(per_signal=[sig_high, sig_low])
        findings = gen.generate(analysis)

        assert findings[0].severity == "high"

    def test_no_findings_when_all_good(self):
        gen = FindingGenerator()
        sig = PerSignalAccuracy(signal_name="RSI超卖", dimension="technical", total=10, correct=7, accuracy=0.70)
        dim = PerDimensionAccuracy(dimension="technical", total=10, correct=7, accuracy=0.70, avg_score=0.3)
        analysis = _make_result(per_signal=[sig], per_dimension=[dim])
        findings = gen.generate(analysis)
        assert findings == []
