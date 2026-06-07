"""测试效能分析器."""

from datetime import datetime

import pytest

from gold_miner.improvement.analyzer import PerformanceAnalyzer
from gold_miner.improvement.tracker import PredictionRecord


def _make_resolved(
    id: str,
    direction: str,
    current_price: float,
    actual_price: float,
    signals: list[dict],
    dimension_scores: dict[str, float] | None = None,
    confidence: float = 0.6,
) -> PredictionRecord:
    actual_return = (actual_price - current_price) / current_price
    if direction == "buy":
        was_correct = actual_return > 0
    elif direction == "sell":
        was_correct = actual_return < 0
    else:
        was_correct = abs(actual_return) < 0.01

    return PredictionRecord(
        id=id,
        timestamp=datetime.now(),
        current_price=current_price,
        signals=signals,
        composite_score=0.3,
        confidence=confidence,
        direction=direction,
        position_pct=0.3,
        dimension_scores=dimension_scores or {},
        actual_price=actual_price,
        actual_return=actual_return,
        was_correct=was_correct,
    )


class TestPerformanceAnalyzer:
    def test_empty_predictions(self):
        analyzer = PerformanceAnalyzer()
        result = analyzer.analyze([])
        assert result.total_predictions == 0
        assert result.resolved_predictions == 0

    def test_no_resolved_predictions(self):
        analyzer = PerformanceAnalyzer()
        predictions = [
            PredictionRecord(
                id="p1", timestamp=datetime.now(), current_price=2000.0,
                signals=[], composite_score=0.3, confidence=0.6,
                direction="buy", position_pct=0.3, dimension_scores={},
            )
        ]
        result = analyzer.analyze(predictions)
        assert result.total_predictions == 1
        assert result.resolved_predictions == 0
        assert result.overall_accuracy == 0.0

    def test_overall_accuracy(self):
        analyzer = PerformanceAnalyzer()
        predictions = [
            _make_resolved("p1", "buy", 2000, 2100,
                           [{"name": "RSI超卖", "dimension": "technical", "direction": "bullish", "score": 0.5}]),
            _make_resolved("p2", "sell", 2000, 1900,
                           [{"name": "MACD死叉", "dimension": "technical", "direction": "bearish", "score": -0.5}]),
            _make_resolved("p3", "buy", 2000, 1900,
                           [{"name": "RSI超卖", "dimension": "technical", "direction": "bullish", "score": 0.5}]),
        ]
        result = analyzer.analyze(predictions)
        assert result.resolved_predictions == 3
        assert result.overall_accuracy == pytest.approx(0.6667)

    def test_per_dimension_accuracy(self):
        analyzer = PerformanceAnalyzer()
        predictions = [
            _make_resolved("p1", "buy", 2000, 2100,
                           [{"name": "RSI超卖", "dimension": "technical", "direction": "bullish", "score": 0.5}],
                           dimension_scores={"technical": 0.5, "fundamental": 0.3}),
            _make_resolved("p2", "buy", 2000, 1900,
                           [{"name": "DXY背离", "dimension": "fundamental", "direction": "bullish", "score": 0.4}],
                           dimension_scores={"technical": 0.0, "fundamental": 0.4}),
        ]
        result = analyzer.analyze(predictions)
        assert len(result.per_dimension) >= 1

    def test_per_signal_accuracy(self):
        analyzer = PerformanceAnalyzer()
        predictions = [
            _make_resolved("p1", "buy", 2000, 2100,
                           [{"name": "RSI超卖", "dimension": "technical", "direction": "bullish", "score": 0.5}]),
            _make_resolved("p2", "buy", 2000, 2100,
                           [{"name": "RSI超卖", "dimension": "technical", "direction": "bullish", "score": 0.5}]),
            _make_resolved("p3", "buy", 2000, 1900,
                           [{"name": "MACD金叉", "dimension": "technical", "direction": "bullish", "score": 0.6}]),
        ]
        result = analyzer.analyze(predictions)

        rsi = next(s for s in result.per_signal if s.signal_name == "RSI超卖")
        assert rsi.total == 2
        assert rsi.correct == 2
        assert rsi.accuracy == 1.0

        macd = next(s for s in result.per_signal if s.signal_name == "MACD金叉")
        assert macd.total == 1
        assert macd.correct == 0

    def test_direction_accuracy(self):
        analyzer = PerformanceAnalyzer()
        predictions = [
            _make_resolved("p1", "buy", 2000, 2100, []),
            _make_resolved("p2", "buy", 2000, 1900, []),
            _make_resolved("p3", "sell", 2000, 1900, []),
        ]
        result = analyzer.analyze(predictions)

        assert result.direction_accuracy["buy"] == 0.5
        assert result.direction_accuracy["sell"] == 1.0

    def test_avg_return(self):
        analyzer = PerformanceAnalyzer()
        predictions = [
            _make_resolved("p1", "buy", 2000, 2100, []),  # +5%
            _make_resolved("p2", "buy", 2000, 1950, []),  # -2.5%
        ]
        result = analyzer.analyze(predictions)
        assert result.avg_return == pytest.approx(0.0125)

    def test_signal_correct_helper(self):
        # bullish signal + price up = correct
        assert PerformanceAnalyzer._signal_correct("bullish", 0.05) is True
        # bullish signal + price down = incorrect
        assert PerformanceAnalyzer._signal_correct("bullish", -0.05) is False
        # bearish signal + price down = correct
        assert PerformanceAnalyzer._signal_correct("bearish", -0.05) is True
        # bearish signal + price up = incorrect
        assert PerformanceAnalyzer._signal_correct("bearish", 0.05) is False
        # neutral = always "correct"
        assert PerformanceAnalyzer._signal_correct("neutral", 0.10) is True
        assert PerformanceAnalyzer._signal_correct("neutral", -0.10) is True
