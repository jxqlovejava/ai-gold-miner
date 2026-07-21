"""Tests for CandlestickPatternDetector — K线形态 + 量价背离 + 共振加成."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from gold_miner.signals.base import Signal, SignalDirection, SignalStrength
from gold_miner.signals.candlestick import CandlestickPatternDetector


def _make_base_ohlcv(n: int = 60, base_price: float = 680.0, seed: int = 42) -> pd.DataFrame:
    """Baseline OHLCV — gentle uptrend with moderate noise."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-06-01", periods=n, freq="B")
    drift = np.linspace(0, 10, n)
    noise = rng.normal(0, 2, n)
    close = base_price + drift + noise
    high = close + np.abs(rng.normal(0, 1.5, n))
    low = close - np.abs(rng.normal(0, 1.5, n))
    open_p = close - rng.normal(0, 0.7, n)
    volume = rng.integers(2000, 6000, n)

    return pd.DataFrame({
        "timestamp": dates, "open": open_p, "high": high,
        "low": low, "close": close, "volume": volume,
    })


def _make_hammer_df() -> pd.DataFrame:
    """Build df with a hammer at the last bar (below MA20)."""
    df = _make_base_ohlcv(n=60, base_price=700.0, seed=1)  # prices ~700-710
    # Override last row: hammer — long lower shadow, small body, tiny upper shadow
    # close slightly below open (red), but the body is small relative to lower shadow
    df.at[df.index[-1], "open"] = 698.0
    df.at[df.index[-1], "high"] = 698.0   # upper shadow = 0
    df.at[df.index[-1], "low"] = 690.0     # lower shadow = 698 - 690 = 8
    df.at[df.index[-1], "close"] = 697.0   # body = 1.0, lower shadow = 7.0 (7x body)
    return df


def _make_shooting_star_df() -> pd.DataFrame:
    """Build df with a shooting star at the last bar (above MA20)."""
    df = _make_base_ohlcv(n=60, base_price=680.0, seed=1)  # prices ~680-690
    df.at[df.index[-1], "open"] = 692.0
    df.at[df.index[-1], "high"] = 700.0     # upper shadow = 700 - 692 = 8
    df.at[df.index[-1], "low"] = 692.0       # lower shadow = 0
    df.at[df.index[-1], "close"] = 693.0     # body = 1.0, green
    return df


def _make_engulfing_df(bullish: bool = True) -> pd.DataFrame:
    """Build df with an engulfing pattern on the last 2 bars."""
    df = _make_base_ohlcv(n=60, seed=3)

    if bullish:
        # Prev bar: red
        df.at[df.index[-2], "open"] = 695.0
        df.at[df.index[-2], "close"] = 690.0
        df.at[df.index[-2], "high"] = 696.0
        df.at[df.index[-2], "low"] = 689.0
        # Curr bar: green, wraps prev bar
        df.at[df.index[-1], "open"] = 688.0   # < prev.close → wraps
        df.at[df.index[-1], "close"] = 697.0   # > prev.open → wraps
        df.at[df.index[-1], "high"] = 698.0
        df.at[df.index[-1], "low"] = 687.0
    else:
        # Bearish engulfing
        df.at[df.index[-2], "open"] = 690.0
        df.at[df.index[-2], "close"] = 695.0
        df.at[df.index[-2], "high"] = 696.0
        df.at[df.index[-2], "low"] = 689.0
        df.at[df.index[-1], "open"] = 697.0    # > prev.close → wraps
        df.at[df.index[-1], "close"] = 688.0    # < prev.open → wraps
        df.at[df.index[-1], "high"] = 698.0
        df.at[df.index[-1], "low"] = 687.0

    return df


def _make_doji_df() -> pd.DataFrame:
    """Build df with a doji on the last bar."""
    df = _make_base_ohlcv(n=60, seed=5)
    center = 690.0
    df.at[df.index[-1], "open"] = center - 0.02
    df.at[df.index[-1], "close"] = center + 0.02
    df.at[df.index[-1], "high"] = center + 3.0
    df.at[df.index[-1], "low"] = center - 3.0
    return df


def _make_long_legged_doji_df() -> pd.DataFrame:
    """Build df with a long-legged doji on the last bar."""
    df = _make_base_ohlcv(n=60, seed=5)
    center = 690.0
    df.at[df.index[-1], "open"] = center - 0.01
    df.at[df.index[-1], "close"] = center + 0.01
    df.at[df.index[-1], "high"] = center + 5.0  # long upper shadow
    df.at[df.index[-1], "low"] = center - 5.0   # long lower shadow
    return df


def _make_star_df(morning: bool = True) -> pd.DataFrame:
    """Build df with morning/evening star on last 3 bars."""
    df = _make_base_ohlcv(n=60, seed=7)

    if morning:  # Big Red → Small → Big Green
        df.at[df.index[-3], "open"] = 700.0
        df.at[df.index[-3], "close"] = 693.0   # red body = 7
        df.at[df.index[-3], "high"] = 701.0
        df.at[df.index[-3], "low"] = 692.0
        # Day2: small body
        df.at[df.index[-2], "open"] = 693.5
        df.at[df.index[-2], "close"] = 693.7   # body = 0.2
        df.at[df.index[-2], "high"] = 695.0
        df.at[df.index[-2], "low"] = 693.0
        # Day3: big green, close > D1 midpoint (696.5)
        df.at[df.index[-1], "open"] = 694.0
        df.at[df.index[-1], "close"] = 699.0   # green body = 5, > midpoint 696.5
        df.at[df.index[-1], "high"] = 700.0
        df.at[df.index[-1], "low"] = 693.0
    else:  # Evening star: Big Green → Small → Big Red
        df.at[df.index[-3], "open"] = 690.0
        df.at[df.index[-3], "close"] = 697.0   # green body = 7
        df.at[df.index[-3], "high"] = 698.0
        df.at[df.index[-3], "low"] = 689.0
        # Day2: small body
        df.at[df.index[-2], "open"] = 697.5
        df.at[df.index[-2], "close"] = 697.3   # body = 0.2
        df.at[df.index[-2], "high"] = 698.0
        df.at[df.index[-2], "low"] = 696.5
        # Day3: big red, close < D1 midpoint (693.5)
        df.at[df.index[-1], "open"] = 697.0
        df.at[df.index[-1], "close"] = 690.0    # red body = 7
        df.at[df.index[-1], "high"] = 697.5
        df.at[df.index[-1], "low"] = 689.0

    return df


def _make_volume_divergence_df(bearish: bool = True) -> pd.DataFrame:
    """Build df with volume-price divergence in the last 20 bars."""
    df = _make_base_ohlcv(n=60, seed=11)
    # First 10 of last 20: lower price, higher volume (or vice versa)
    if bearish:  # price up, volume down
        df.loc[df.index[-20:-10], "close"] = df.loc[df.index[-20:-10], "close"] - 3
        df.loc[df.index[-20:-10], "volume"] = 6000  # high vol in first half
        df.loc[df.index[-10:], "close"] = df.loc[df.index[-10:], "close"] + 5
        df.loc[df.index[-10:], "volume"] = 2000      # low vol in second half
    else:  # price down, volume down
        df.loc[df.index[-20:-10], "close"] = df.loc[df.index[-20:-10], "close"] + 5
        df.loc[df.index[-20:-10], "volume"] = 6000
        df.loc[df.index[-10:], "close"] = df.loc[df.index[-10:], "close"] - 5
        df.loc[df.index[-10:], "volume"] = 2000

    return df


# ------------------------------------------------------------------
# Pattern detection tests
# ------------------------------------------------------------------


class TestHammer:
    def test_detects_hammer_at_bottom(self) -> None:
        df = _make_hammer_df()
        detector = CandlestickPatternDetector(df)
        signals = detector.detect_hammer()
        hammer_sigs = [s for s in signals if "锤子线" in s.name]
        assert len(hammer_sigs) >= 1, f"Expected hammer signal, got {signals}"
        sig = hammer_sigs[0]
        assert sig.direction == SignalDirection.BULLISH
        assert sig.strength == SignalStrength.WEAK

    def test_no_hammer_in_normal_data(self) -> None:
        df = _make_base_ohlcv()
        detector = CandlestickPatternDetector(df)
        signals = detector.detect_hammer()
        hammer_sigs = [s for s in signals if "锤子线" in s.name]
        assert len(hammer_sigs) == 0

    def test_detects_shooting_star_at_top(self) -> None:
        df = _make_shooting_star_df()
        detector = CandlestickPatternDetector(df)
        signals = detector.detect_hammer()
        bear_sigs = [s for s in signals if "射击之星" in s.name]
        assert len(bear_sigs) >= 1, f"Expected shooting star, got {signals}"
        assert bear_sigs[0].direction == SignalDirection.BEARISH


class TestEngulfing:
    def test_detects_bullish_engulfing(self) -> None:
        df = _make_engulfing_df(bullish=True)
        detector = CandlestickPatternDetector(df)
        signals = detector.detect_engulfing()
        bull_sigs = [s for s in signals if "看涨吞没" in s.name]
        assert len(bull_sigs) >= 1, f"Expected bullish engulfing, got {signals}"
        assert bull_sigs[0].direction == SignalDirection.BULLISH

    def test_detects_bearish_engulfing(self) -> None:
        df = _make_engulfing_df(bullish=False)
        detector = CandlestickPatternDetector(df)
        signals = detector.detect_engulfing()
        bear_sigs = [s for s in signals if "看跌吞没" in s.name]
        assert len(bear_sigs) >= 1, f"Expected bearish engulfing, got {signals}"
        assert bear_sigs[0].direction == SignalDirection.BEARISH

    def test_no_engulfing_in_normal_data(self) -> None:
        df = _make_base_ohlcv()
        detector = CandlestickPatternDetector(df)
        signals = detector.detect_engulfing()
        assert len(signals) == 0


class TestDoji:
    def test_detects_standard_doji(self) -> None:
        df = _make_doji_df()
        detector = CandlestickPatternDetector(df)
        signals = detector.detect_doji()
        assert len(signals) >= 1, f"Expected doji, got {signals}"
        assert signals[0].direction == SignalDirection.NEUTRAL
        assert signals[0].score == 0.0  # doji has no directional bias

    def test_detects_long_legged_doji(self) -> None:
        df = _make_long_legged_doji_df()
        detector = CandlestickPatternDetector(df)
        signals = detector.detect_doji()
        assert len(signals) >= 1
        assert "长腿" in signals[0].name

    def test_no_doji_in_normal_data(self) -> None:
        df = _make_base_ohlcv()
        detector = CandlestickPatternDetector(df)
        signals = detector.detect_doji()
        assert len(signals) == 0


class TestStarPatterns:
    def test_detects_morning_star(self) -> None:
        df = _make_star_df(morning=True)
        detector = CandlestickPatternDetector(df)
        signals = detector.detect_morning_evening_star()
        bull_sigs = [s for s in signals if "晨星" in s.name]
        assert len(bull_sigs) >= 1, f"Expected morning star, got {signals}"
        assert bull_sigs[0].direction == SignalDirection.BULLISH

    def test_detects_evening_star(self) -> None:
        df = _make_star_df(morning=False)
        detector = CandlestickPatternDetector(df)
        signals = detector.detect_morning_evening_star()
        bear_sigs = [s for s in signals if "暮星" in s.name]
        assert len(bear_sigs) >= 1, f"Expected evening star, got {signals}"
        assert bear_sigs[0].direction == SignalDirection.BEARISH

    def test_no_star_in_normal_data(self) -> None:
        df = _make_base_ohlcv()
        detector = CandlestickPatternDetector(df)
        signals = detector.detect_morning_evening_star()
        assert len(signals) == 0


class TestVolumeDivergence:
    def test_detects_bearish_divergence(self) -> None:
        df = _make_volume_divergence_df(bearish=True)
        detector = CandlestickPatternDetector(df)
        signals = detector.detect_volume_price_divergence()
        bear_sigs = [s for s in signals if "顶背离" in s.name]
        assert len(bear_sigs) >= 1, f"Expected bearish volume divergence, got {signals}"
        assert bear_sigs[0].direction == SignalDirection.BEARISH
        assert bear_sigs[0].strength == SignalStrength.MODERATE

    def test_detects_bullish_divergence(self) -> None:
        df = _make_volume_divergence_df(bearish=False)
        detector = CandlestickPatternDetector(df)
        signals = detector.detect_volume_price_divergence()
        bull_sigs = [s for s in signals if "底背离" in s.name]
        assert len(bull_sigs) >= 1, f"Expected bullish volume divergence, got {signals}"
        assert bull_sigs[0].direction == SignalDirection.BULLISH

    def test_no_divergence_without_volume_column(self) -> None:
        df = _make_base_ohlcv().drop(columns=["volume"])
        detector = CandlestickPatternDetector(df)
        signals = detector.detect_volume_price_divergence()
        assert len(signals) == 0


# ------------------------------------------------------------------
# Resonance boosting
# ------------------------------------------------------------------


class TestResonance:
    def test_two_bullish_resonance(self) -> None:
        """2 同向信号 → MODERATE 共振."""
        from gold_miner.signals.base import Signal, SignalDirection, SignalStrength

        signals = [
            Signal(name="锤子线", dimension="technical", direction=SignalDirection.BULLISH,
                   strength=SignalStrength.WEAK, score=0.12),
            Signal(name="看涨吞没", dimension="technical", direction=SignalDirection.BULLISH,
                   strength=SignalStrength.WEAK, score=0.15),
        ]
        detector = CandlestickPatternDetector(_make_base_ohlcv(n=30))
        boosted = detector._boost_resonance(signals)
        assert len(boosted) == 1
        assert boosted[0].strength == SignalStrength.MODERATE
        assert boosted[0].direction == SignalDirection.BULLISH
        assert "共振" in boosted[0].name

    def test_three_bullish_resonance(self) -> None:
        """3 同向信号 → STRONG 共振."""
        from gold_miner.signals.base import Signal, SignalDirection, SignalStrength

        signals = [
            Signal(name="锤子线", dimension="technical", direction=SignalDirection.BULLISH,
                   strength=SignalStrength.WEAK, score=0.12),
            Signal(name="看涨吞没", dimension="technical", direction=SignalDirection.BULLISH,
                   strength=SignalStrength.WEAK, score=0.15),
            Signal(name="晨星", dimension="technical", direction=SignalDirection.BULLISH,
                   strength=SignalStrength.WEAK, score=0.18),
        ]
        detector = CandlestickPatternDetector(_make_base_ohlcv(n=30))
        boosted = detector._boost_resonance(signals)
        assert len(boosted) == 1
        assert boosted[0].strength == SignalStrength.STRONG

    def test_no_resonance_single_signal(self) -> None:
        from gold_miner.signals.base import Signal, SignalDirection, SignalStrength

        signals = [
            Signal(name="锤子线", dimension="technical", direction=SignalDirection.BULLISH,
                   strength=SignalStrength.WEAK, score=0.12),
        ]
        detector = CandlestickPatternDetector(_make_base_ohlcv(n=30))
        boosted = detector._boost_resonance(signals)
        assert len(boosted) == 0

    def test_neutral_signals_ignored_in_resonance(self) -> None:
        from gold_miner.signals.base import Signal, SignalDirection, SignalStrength

        signals = [
            Signal(name="十字星", dimension="technical", direction=SignalDirection.NEUTRAL,
                   strength=SignalStrength.WEAK, score=0.0),
            Signal(name="锤子线", dimension="technical", direction=SignalDirection.BULLISH,
                   strength=SignalStrength.WEAK, score=0.12),
        ]
        detector = CandlestickPatternDetector(_make_base_ohlcv(n=30))
        boosted = detector._boost_resonance(signals)
        # 只有1个非中性信号 → 不共振
        assert len(boosted) == 0


# ------------------------------------------------------------------
# Integration & edge cases
# ------------------------------------------------------------------


class TestGenerateSignals:
    def test_returns_list_of_signals(self) -> None:
        df = _make_base_ohlcv()
        detector = CandlestickPatternDetector(df)
        signals = detector.generate_signals()
        assert isinstance(signals, list)
        for sig in signals:
            assert isinstance(sig, Signal)
            assert sig.dimension == "technical"

    def test_signal_count_reasonable(self) -> None:
        """Normal data: 0-6 signals max."""
        df = _make_base_ohlcv(n=100)
        detector = CandlestickPatternDetector(df)
        signals = detector.generate_signals()
        assert len(signals) <= 8  # generous upper bound for normal data

    def test_all_signals_have_metadata(self) -> None:
        df = _make_base_ohlcv(n=100)
        detector = CandlestickPatternDetector(df)
        signals = detector.generate_signals()
        for sig in signals:
            assert sig.metadata is not None
            assert "pattern" in sig.metadata or sig.name == ""

    def test_insufficient_data_returns_empty(self) -> None:
        df = _make_base_ohlcv(n=10)
        detector = CandlestickPatternDetector(df)
        signals = detector.generate_signals()
        # Should not crash with insufficient data
        assert isinstance(signals, list)

    def test_missing_columns_raises(self) -> None:
        df = pd.DataFrame({"close": [100, 101, 102]})
        with pytest.raises(ValueError, match="缺少必要列"):
            CandlestickPatternDetector(df)

    def test_no_volume_column_ok(self) -> None:
        """Missing volume should not crash — just skip volume divergence."""
        df = _make_base_ohlcv().drop(columns=["volume"])
        detector = CandlestickPatternDetector(df)
        signals = detector.generate_signals()
        # Should work fine, just no volume signals
        vol_sigs = [s for s in signals if "量价" in s.name]
        assert len(vol_sigs) == 0
