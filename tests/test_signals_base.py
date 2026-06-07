"""Tests for signals/base.py — Signal, SignalBundle, and enums."""

from datetime import datetime

from gold_miner.signals.base import Signal, SignalBundle, SignalDirection, SignalStrength


class TestSignalDirection:
    def test_enum_values(self) -> None:
        assert SignalDirection.BULLISH.value == "bullish"
        assert SignalDirection.BEARISH.value == "bearish"
        assert SignalDirection.NEUTRAL.value == "neutral"

    def test_enum_membership(self) -> None:
        assert SignalDirection("bullish") == SignalDirection.BULLISH
        assert SignalDirection("bearish") == SignalDirection.BEARISH
        assert SignalDirection("neutral") == SignalDirection.NEUTRAL


class TestSignalStrength:
    def test_enum_values(self) -> None:
        assert SignalStrength.STRONG.value == "strong"
        assert SignalStrength.MODERATE.value == "moderate"
        assert SignalStrength.WEAK.value == "weak"

    def test_enum_membership(self) -> None:
        assert SignalStrength("strong") == SignalStrength.STRONG
        assert SignalStrength("moderate") == SignalStrength.MODERATE
        assert SignalStrength("weak") == SignalStrength.WEAK


class TestSignal:
    def test_default_timestamp_is_set(self) -> None:
        """Signal should auto-assign a timestamp when not provided."""
        signal = Signal(name="test", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5)
        assert isinstance(signal.timestamp, datetime)

    def test_default_metadata_is_empty_dict(self) -> None:
        signal = Signal(name="test", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5)
        assert signal.metadata == {}

    def test_default_description_is_empty_string(self) -> None:
        signal = Signal(name="test", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5)
        assert signal.description == ""

    def test_all_fields(self) -> None:
        ts = datetime(2025, 1, 15, 10, 30, 0)
        signal = Signal(
            name="RSI超卖",
            dimension="technical",
            direction=SignalDirection.BULLISH,
            strength=SignalStrength.MODERATE,
            score=0.4,
            description="RSI < 30 超卖反弹",
            timestamp=ts,
            metadata={"rsi_value": 25.0},
        )
        assert signal.name == "RSI超卖"
        assert signal.dimension == "technical"
        assert signal.direction == SignalDirection.BULLISH
        assert signal.strength == SignalStrength.MODERATE
        assert signal.score == 0.4
        assert signal.description == "RSI < 30 超卖反弹"
        assert signal.timestamp == ts
        assert signal.metadata == {"rsi_value": 25.0}


class TestSignalBundle:
    def test_empty_bundle(self) -> None:
        bundle = SignalBundle()
        assert bundle.signals == []
        assert bundle.composite_score == 0.0
        assert bundle.confidence == 0.0

    def test_add_signal(self) -> None:
        bundle = SignalBundle()
        signal = Signal(name="test", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5)
        bundle.add(signal)
        assert len(bundle.signals) == 1
        assert bundle.signals[0] == signal

    def test_add_multiple(self) -> None:
        bundle = SignalBundle()
        s1 = Signal(name="a", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5)
        s2 = Signal(name="b", dimension="news", direction=SignalDirection.BEARISH, strength=SignalStrength.WEAK, score=-0.2)
        bundle.add(s1)
        bundle.add(s2)
        assert len(bundle.signals) == 2

    def test_by_dimension_returns_matching(self) -> None:
        bundle = SignalBundle()
        s1 = Signal(name="a", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5)
        s2 = Signal(name="b", dimension="news", direction=SignalDirection.BEARISH, strength=SignalStrength.WEAK, score=-0.2)
        s3 = Signal(name="c", dimension="technical", direction=SignalDirection.NEUTRAL, strength=SignalStrength.MODERATE, score=0.0)
        bundle.add(s1)
        bundle.add(s2)
        bundle.add(s3)

        tech_signals = bundle.by_dimension("technical")
        assert len(tech_signals) == 2
        assert s1 in tech_signals
        assert s3 in tech_signals

    def test_by_dimension_empty_when_no_match(self) -> None:
        bundle = SignalBundle()
        s = Signal(name="a", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5)
        bundle.add(s)
        assert bundle.by_dimension("fundamental") == []

    def test_bullish_count(self) -> None:
        bundle = SignalBundle()
        bundle.add(Signal(name="a", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5))
        bundle.add(Signal(name="b", dimension="news", direction=SignalDirection.BEARISH, strength=SignalStrength.WEAK, score=-0.2))
        bundle.add(Signal(name="c", dimension="sentiment", direction=SignalDirection.BULLISH, strength=SignalStrength.MODERATE, score=0.3))
        assert bundle.bullish_count() == 2

    def test_bullish_count_zero_when_none(self) -> None:
        bundle = SignalBundle()
        bundle.add(Signal(name="a", dimension="technical", direction=SignalDirection.BEARISH, strength=SignalStrength.STRONG, score=-0.5))
        assert bundle.bullish_count() == 0

    def test_bearish_count(self) -> None:
        bundle = SignalBundle()
        bundle.add(Signal(name="a", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5))
        bundle.add(Signal(name="b", dimension="news", direction=SignalDirection.BEARISH, strength=SignalStrength.WEAK, score=-0.2))
        assert bundle.bearish_count() == 1

    def test_bearish_count_zero_when_none(self) -> None:
        bundle = SignalBundle()
        bundle.add(Signal(name="a", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5))
        assert bundle.bearish_count() == 0

    def test_both_counts_with_neutral(self) -> None:
        """Neutral signals should not count as bullish or bearish."""
        bundle = SignalBundle()
        bundle.add(Signal(name="a", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5))
        bundle.add(Signal(name="b", dimension="news", direction=SignalDirection.NEUTRAL, strength=SignalStrength.MODERATE, score=0.0))
        assert bundle.bullish_count() == 1
        assert bundle.bearish_count() == 0
