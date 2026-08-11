"""Tests for TechnicalAnalyzer — existing + new indicators + regulator behavior."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from gold_miner.signals.base import Signal, SignalDirection, SignalStrength
from gold_miner.signals.technical import TechnicalAnalyzer


def _make_ohlcv_df(
    n: int = 100,
    base_price: float = 680.0,
    trend: float = 5.0,
    noise_std: float = 3.0,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate reproducible OHLCV DataFrame for testing."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-06-01", periods=n, freq="B")
    drift = np.linspace(0, trend, n)
    noise = rng.normal(0, noise_std, n)
    close = base_price + drift + noise
    high = close + np.abs(rng.normal(0, 2, n))
    low = close - np.abs(rng.normal(0, 2, n))
    open_p = close - rng.normal(0, 1, n)
    volume = rng.integers(1000, 5000, n)

    return pd.DataFrame({
        "timestamp": dates,
        "open": open_p,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


def _make_uptrend_df(n: int = 100) -> pd.DataFrame:
    """Strong uptrend with low noise — trend scales with bars to ensure clear ADX."""
    return _make_ohlcv_df(
        n=n, base_price=650.0, trend=float(n) * 1.0, noise_std=0.5, seed=7,
    )


def _make_downtrend_df(n: int = 100) -> pd.DataFrame:
    """Strong downtrend with low noise — trend scales with bars."""
    return _make_ohlcv_df(
        n=n, base_price=700.0, trend=float(n) * -1.0, noise_std=0.5, seed=13,
    )


def _make_ranging_df(n: int = 100) -> pd.DataFrame:
    """Sideways market with low noise."""
    return _make_ohlcv_df(n=n, base_price=680.0, trend=0.0, noise_std=2.0, seed=99)


# ------------------------------------------------------------------
# Existing indicators — regression tests
# ------------------------------------------------------------------


class TestExistingIndicators:
    def test_rsi_normal_range(self) -> None:
        df = _make_ohlcv_df()
        ta = TechnicalAnalyzer(df)
        val = ta.rsi()
        assert 0.0 <= val <= 100.0

    def test_rsi_insufficient_data(self) -> None:
        df = _make_ohlcv_df(n=10)
        ta = TechnicalAnalyzer(df)
        val = ta.rsi()
        assert val == 50.0

    def test_macd_returns_expected_keys(self) -> None:
        df = _make_ohlcv_df()
        ta = TechnicalAnalyzer(df)
        result = ta.macd()
        assert set(result.keys()) == {"macd", "signal", "histogram", "crossover"}
        assert result["crossover"] in ("bullish", "bearish", "none")

    def test_macd_insufficient_data(self) -> None:
        df = _make_ohlcv_df(n=20)
        ta = TechnicalAnalyzer(df)
        result = ta.macd()
        assert result == {"macd": 0.0, "signal": 0.0, "histogram": 0.0, "crossover": "none"}

    def test_bollinger_returns_expected_keys(self) -> None:
        df = _make_ohlcv_df()
        ta = TechnicalAnalyzer(df)
        result = ta.bollinger()
        assert set(result.keys()) == {"upper", "middle", "lower", "width_pct", "position"}
        assert result["upper"] >= result["lower"]
        assert 0.0 <= result["position"] <= 1.0

    def test_support_resistance_returns_expected_keys(self) -> None:
        df = _make_ohlcv_df()
        ta = TechnicalAnalyzer(df)
        result = ta.support_resistance()
        assert set(result.keys()) == {
            "support", "resistance", "latest",
            "distance_to_support", "distance_to_resistance",
        }
        assert result["resistance"] >= result["support"]

    def test_generate_signals_returns_list(self) -> None:
        df = _make_ohlcv_df()
        ta = TechnicalAnalyzer(df)
        signals = ta.generate_signals()
        assert isinstance(signals, list)
        for sig in signals:
            assert isinstance(sig, Signal)
            assert sig.dimension == "technical"


# ------------------------------------------------------------------
# New indicators — ATR / MA crossover / ADX
# ------------------------------------------------------------------


class TestATR:
    def test_returns_expected_keys(self) -> None:
        df = _make_ohlcv_df()
        ta = TechnicalAnalyzer(df)
        result = ta.atr()
        assert set(result.keys()) == {"atr", "atr_pct", "volatility_regime"}
        assert result["volatility_regime"] in ("low", "normal", "high")

    def test_atr_positive(self) -> None:
        df = _make_ohlcv_df()
        ta = TechnicalAnalyzer(df)
        result = ta.atr()
        assert result["atr"] > 0

    def test_low_volatility_regime(self) -> None:
        df = _make_ohlcv_df(n=100, noise_std=0.5, base_price=680.0, trend=5.0, seed=1)
        ta = TechnicalAnalyzer(df)
        result = ta.atr()
        assert result["atr_pct"] < 1.0
        assert result["volatility_regime"] == "low"

    def test_high_volatility_regime(self) -> None:
        df = _make_ohlcv_df(n=100, noise_std=15.0, base_price=680.0, trend=0.0, seed=1)
        ta = TechnicalAnalyzer(df)
        result = ta.atr()
        assert result["atr_pct"] > 2.0, f"got atr_pct={result['atr_pct']}"
        assert result["volatility_regime"] == "high"

    def test_insufficient_data_fallback(self) -> None:
        df = _make_ohlcv_df(n=10)
        ta = TechnicalAnalyzer(df)
        result = ta.atr()
        assert result == {"atr": 0.0, "atr_pct": 0.0, "volatility_regime": "normal"}


class TestMACrossover:
    def test_no_crossover_in_normal_data(self) -> None:
        df = _make_ohlcv_df()
        ta = TechnicalAnalyzer(df)
        result = ta.ma_crossover()
        assert set(result.keys()) == {"crossover", "fast_ma", "slow_ma", "gap_pct"}

    def test_fast_above_slow_in_uptrend(self) -> None:
        df = _make_uptrend_df(n=60)
        ta = TechnicalAnalyzer(df)
        result = ta.ma_crossover()
        assert result["fast_ma"] > result["slow_ma"]

    def test_fast_below_slow_in_downtrend(self) -> None:
        df = _make_downtrend_df(n=60)
        ta = TechnicalAnalyzer(df)
        result = ta.ma_crossover()
        assert result["fast_ma"] < result["slow_ma"]

    def test_insufficient_data_fallback(self) -> None:
        df = _make_ohlcv_df(n=15)
        ta = TechnicalAnalyzer(df)
        result = ta.ma_crossover()
        assert result == {"crossover": "none", "fast_ma": 0.0, "slow_ma": 0.0, "gap_pct": 0.0}


class TestADX:
    def test_returns_expected_keys(self) -> None:
        df = _make_ohlcv_df()
        ta = TechnicalAnalyzer(df)
        result = ta.adx()
        assert set(result.keys()) == {"adx", "plus_di", "minus_di", "trend_regime"}
        assert result["trend_regime"] in ("trending", "ranging")
        assert result["adx"] >= 0

    def test_trending_market(self) -> None:
        df = _make_uptrend_df(n=100)
        ta = TechnicalAnalyzer(df)
        result = ta.adx()
        assert result["adx"] > 25, f"got adx={result['adx']}"
        assert result["trend_regime"] == "trending"

    def test_ranging_market(self) -> None:
        df = _make_ranging_df(n=100)
        ta = TechnicalAnalyzer(df)
        result = ta.adx()
        assert result["trend_regime"] == "ranging" or result["adx"] < 25

    def test_insufficient_data_fallback(self) -> None:
        df = _make_ohlcv_df(n=15)
        ta = TechnicalAnalyzer(df)
        result = ta.adx()
        assert result == {"adx": 20.0, "plus_di": 0.0, "minus_di": 0.0, "trend_regime": "ranging"}


# ------------------------------------------------------------------
# Regulator behavior
# ------------------------------------------------------------------


class TestRegulator:
    """ATR/ADX _adjust() 调节器行为验证."""

    def test_low_vol_downgrades_strong_signal(self) -> None:
        df = _make_uptrend_df(n=60)
        ta = TechnicalAnalyzer(df)
        signals = ta.generate_signals()
        macd_signals = [s for s in signals if "MACD" in s.name]
        if macd_signals:
            sig = macd_signals[0]
            assert sig.strength != SignalStrength.STRONG, (
                f"低波市场不应保持 STRONG, got {sig.strength.value}"
            )
            assert abs(sig.score) < 0.6, (
                f"低波市场 score 应被折扣, got {sig.score}"
            )

    def test_ranging_weakens_all_signals(self) -> None:
        df = _make_ranging_df(n=60)
        ta = TechnicalAnalyzer(df)
        signals = ta.generate_signals()
        for sig in signals:
            assert sig.strength == SignalStrength.WEAK, (
                f"震荡低波市场所有信号应为 WEAK, {sig.name} got {sig.strength.value}"
            )


class TestSignalCount:
    """信号数量合理性验证."""

    def test_signal_count_within_reasonable_range(self) -> None:
        df = _make_ohlcv_df(n=100)
        ta = TechnicalAnalyzer(df)
        signals = ta.generate_signals()
        assert len(signals) <= 6, f"信号过多: {len(signals)}"

    def test_all_signals_have_metadata(self) -> None:
        df = _make_ohlcv_df(n=100)
        ta = TechnicalAnalyzer(df)
        signals = ta.generate_signals()
        for sig in signals:
            assert sig.metadata is not None
            assert "source_tier" in sig.metadata
            assert sig.dimension == "technical"


class TestRSIThreshold:
    """RSI 阈值 20/80 — 更极端才触发超卖/超买信号."""

    def test_oversold_fires_below_20(self) -> None:
        df = _make_downtrend_df(n=60)
        ta = TechnicalAnalyzer(df)
        rsi = ta.rsi()
        signals = ta.generate_signals()
        assert rsi < 20, f"预期 RSI<20，实际 {rsi:.1f}"
        assert any(s.name == "RSI超卖" for s in signals)

    def test_overbought_fires_above_80(self) -> None:
        df = _make_uptrend_df(n=60)
        ta = TechnicalAnalyzer(df)
        rsi = ta.rsi()
        signals = ta.generate_signals()
        assert rsi > 80, f"预期 RSI>80，实际 {rsi:.1f}"
        assert any(s.name == "RSI超买" for s in signals)

    def test_mid_range_no_extreme_signal(self) -> None:
        df = _make_ranging_df(n=100)
        ta = TechnicalAnalyzer(df)
        rsi = ta.rsi()
        signals = ta.generate_signals()
        assert 20 <= rsi <= 80
        assert not any(s.name in ("RSI超卖", "RSI超买") for s in signals)

    def test_score_scales_with_threshold(self) -> None:
        """RSI 越极端分数越大（0→1 线性缩放），超卖为正在、超买为负向."""
        df = _make_downtrend_df(n=60)
        ta = TechnicalAnalyzer(df)
        oversold = next(s for s in ta.generate_signals() if s.name == "RSI超卖")
        assert 0.0 < oversold.score <= 1.0
        assert oversold.direction == SignalDirection.BULLISH

        df_up = _make_uptrend_df(n=60)
        ta_up = TechnicalAnalyzer(df_up)
        overbought = next(s for s in ta_up.generate_signals() if s.name == "RSI超买")
        assert -1.0 <= overbought.score < 0.0
        assert overbought.direction == SignalDirection.BEARISH


# ------------------------------------------------------------------
# 突破前兆信号 (Req1A 2026-08-11)
# ------------------------------------------------------------------


def _make_squeeze_df(n: int = 60) -> pd.DataFrame:
    """尾部 20 根 close 近乎恒定 → 20日布林带宽收敛到极低 (真实窄幅盘整)."""
    df = _make_ohlcv_df(n=n, base_price=900.0, trend=0.0, noise_std=3.0, seed=3)
    const_val = 947.0
    n_const = 20
    df.loc[df.index[-n_const:], "close"] = const_val
    df.loc[df.index[-n_const:], "high"] = const_val + 0.5
    df.loc[df.index[-n_const:], "low"] = const_val - 0.5
    return df


def _make_round_df(latest: float, n: int = 60) -> pd.DataFrame:
    """close 末值 = latest, 用于整数关口逼近测试."""
    df = _make_ohlcv_df(n=n, base_price=latest - 20.0, trend=1.0, noise_std=1.0, seed=5)
    df.loc[df.index[-1], "close"] = latest
    df.loc[df.index[-1], "high"] = latest + 1.0
    df.loc[df.index[-1], "low"] = latest - 1.0
    return df


class TestSqueezeDetection:
    def test_squeeze_fires_on_tight_band(self) -> None:
        df = _make_squeeze_df()
        ta = TechnicalAnalyzer(df)
        result = ta.squeeze_detection()
        assert result["squeeze"] is True
        assert result["width_pct"] < 0.03

    def test_no_squeeze_on_ranging(self) -> None:
        df = _make_ranging_df(n=60)
        ta = TechnicalAnalyzer(df)
        result = ta.squeeze_detection()
        assert result["squeeze"] is False

    def test_insufficient_data_fallback(self) -> None:
        df = _make_ohlcv_df(n=15)
        ta = TechnicalAnalyzer(df)
        result = ta.squeeze_detection()
        assert result["squeeze"] is False


class TestRoundLevelProximity:
    def test_near_below(self) -> None:
        df = _make_round_df(latest=947.0)
        ta = TechnicalAnalyzer(df)
        result = ta.round_level_proximity()
        assert result["near_round_level"] is True
        assert result["level"] == 950.0
        assert result["above"] is False

    def test_near_above(self) -> None:
        df = _make_round_df(latest=1003.0)
        ta = TechnicalAnalyzer(df)
        result = ta.round_level_proximity()
        assert result["near_round_level"] is True
        assert result["level"] == 1000.0
        assert result["above"] is True

    def test_far(self) -> None:
        df = _make_round_df(latest=920.0)
        ta = TechnicalAnalyzer(df)
        result = ta.round_level_proximity()
        assert result["near_round_level"] is False


class TestAdxConvergence:
    def test_returns_expected_keys(self) -> None:
        df = _make_ohlcv_df()
        ta = TechnicalAnalyzer(df)
        result = ta.adx_convergence()
        assert set(result.keys()) == {"adx_converging", "adx", "adx_prev", "drop_pct"}
        assert result["adx"] >= 0

    def test_no_convergence_in_strong_uptrend(self) -> None:
        df = _make_uptrend_df(n=100)
        ta = TechnicalAnalyzer(df)
        result = ta.adx_convergence()
        assert result["adx_converging"] is False

    def test_adx_still_returns_keys_after_refactor(self) -> None:
        """回归: _adx_series 重构后 adx() 键不变."""
        df = _make_ohlcv_df()
        ta = TechnicalAnalyzer(df)
        result = ta.adx()
        assert set(result.keys()) == {"adx", "plus_di", "minus_di", "trend_regime"}


class TestBreakoutPrecursorSignals:
    def test_generate_signals_contains_round_level_near(self) -> None:
        df = _make_round_df(latest=947.0)
        ta = TechnicalAnalyzer(df)
        signals = ta.generate_signals()
        assert any("逼近整数关口" in s.name for s in signals)
        for sig in signals:
            assert sig.dimension == "technical"

    def test_generate_signals_contains_squeeze(self) -> None:
        df = _make_squeeze_df()
        ta = TechnicalAnalyzer(df)
        signals = ta.generate_signals()
        assert any(s.name == "布林带收窄·蓄势待变" for s in signals)
