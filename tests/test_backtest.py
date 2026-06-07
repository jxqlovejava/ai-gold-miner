"""Tests for backtest/engine.py — BacktestEngine and BacktestResult."""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from gold_miner.backtest.engine import BacktestEngine, BacktestResult
from gold_miner.signals.base import Signal, SignalBundle, SignalDirection, SignalStrength


def _make_ohlcv(n: int = 100, start_price: float = 100.0) -> pd.DataFrame:
    """Generate realistic OHLCV mock data with trending price action."""
    np.random.seed(42)
    start = datetime(2025, 1, 1)
    timestamps = [start + timedelta(hours=i) for i in range(n)]

    # Random walk with slight upward drift
    returns = np.random.normal(0.0005, 0.01, n)
    prices = [start_price]
    for r in returns[1:]:
        prices.append(prices[-1] * (1 + r))

    return pd.DataFrame({
        "timestamp": timestamps,
        "open": prices,
        "high": [p * 1.015 for p in prices],
        "low": [p * 0.985 for p in prices],
        "close": prices,
        "volume": np.random.randint(500, 1500, n).tolist(),
    })


def _make_uptrend_ohlcv(n: int = 120) -> pd.DataFrame:
    """Generate OHLCV data with a clear uptrend (for buy signals)."""
    start = datetime(2025, 1, 1)
    timestamps = [start + timedelta(hours=i) for i in range(n)]
    prices = np.linspace(100, 130, n).tolist()
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": prices,
        "high": [p * 1.01 for p in prices],
        "low": [p * 0.99 for p in prices],
        "close": prices,
        "volume": [1000] * n,
    })


def _always_bullish(df: pd.DataFrame) -> SignalBundle:
    """Signal function that always returns a bullish signal."""
    bundle = SignalBundle()
    bundle.add(Signal(
        name="Always Bullish", dimension="technical",
        direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG,
        score=0.6,
    ))
    bundle.composite_score = 0.6
    bundle.confidence = 0.8
    return bundle


def _always_bearish(df: pd.DataFrame) -> SignalBundle:
    """Signal function that always returns a bearish signal."""
    bundle = SignalBundle()
    bundle.add(Signal(
        name="Always Bearish", dimension="technical",
        direction=SignalDirection.BEARISH, strength=SignalStrength.STRONG,
        score=-0.6,
    ))
    bundle.composite_score = -0.6
    bundle.confidence = 0.8
    return bundle


def _neutral_after_50(df: pd.DataFrame) -> SignalBundle:
    """Signal function that is bullish for first 50 rows, neutral after."""
    bundle = SignalBundle()
    if len(df) < 70:
        bundle.add(Signal(
            name="Early Bullish", dimension="technical",
            direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG,
            score=0.8,
        ))
        bundle.composite_score = 0.8
    else:
        bundle.composite_score = 0.0
    bundle.confidence = 0.5
    return bundle


class TestBacktestResult:
    def test_dataclass_defaults(self) -> None:
        result = BacktestResult()
        assert result.total_return == 0.0
        assert result.sharpe_ratio == 0.0
        assert result.max_drawdown == 0.0
        assert result.total_trades == 0
        assert result.win_rate == 0.0
        assert result.profit_factor == 0.0
        assert result.equity_curve == []
        assert result.trades == []

    def test_dataclass_fields(self) -> None:
        result = BacktestResult(
            total_return=0.15,
            sharpe_ratio=1.2,
            max_drawdown=0.05,
            total_trades=5,
            win_rate=0.6,
            profit_factor=2.0,
        )
        assert result.total_return == 0.15
        assert result.sharpe_ratio == 1.2
        assert result.max_drawdown == 0.05
        assert result.total_trades == 5
        assert result.win_rate == 0.6
        assert result.profit_factor == 2.0


class TestBacktestEngine:
    def test_run_with_bullish_signals_positive_return(self) -> None:
        df = _make_uptrend_ohlcv(n=120)
        engine = BacktestEngine()
        result = engine.run(df, _always_bullish)
        # On an uptrend with always-bullish signals, total return should be positive
        assert result.total_return > 0

    def test_run_with_bearish_signals_no_buys(self) -> None:
        """Bearish signals should not trigger buy."""
        df = _make_uptrend_ohlcv(n=120)
        engine = BacktestEngine()
        result = engine.run(df, _always_bearish)
        # No buy actions means no closed trades
        assert result.total_trades == 0

    def test_run_with_neutral_after_50_has_buy_trade(self) -> None:
        """Should buy during bullish phase; check trades list for buy entries."""
        df = _make_uptrend_ohlcv(n=120)
        engine = BacktestEngine()
        result = engine.run(df, _neutral_after_50)
        buy_trades = [t for t in result.trades if t.get("action") == "buy"]
        assert len(buy_trades) >= 1

    def test_metrics_after_run(self) -> None:
        df = _make_uptrend_ohlcv(n=120)
        engine = BacktestEngine()
        result = engine.run(df, _always_bullish)
        assert isinstance(result.total_return, float)
        assert isinstance(result.max_drawdown, float)
        assert isinstance(result.sharpe_ratio, float)
        assert result.equity_curve is not None
        assert len(result.equity_curve) > 0

    def test_equity_curve_length(self) -> None:
        df = _make_uptrend_ohlcv(n=100)
        engine = BacktestEngine()
        result = engine.run(df, _always_bullish)
        # Equity curve should have one entry per bar (starting at bar 50)
        expected_length = len(df) - 50 + 1  # +1 for initial entry
        assert len(result.equity_curve) == expected_length

    def test_initial_capital_affects_results(self) -> None:
        """Different initial capital should not change return percentages."""
        df = _make_uptrend_ohlcv(n=120)
        engine1 = BacktestEngine(initial_capital=100_000)
        engine2 = BacktestEngine(initial_capital=50_000)
        r1 = engine1.run(df, _always_bullish)
        r2 = engine2.run(df, _always_bullish)
        assert abs(r1.total_return - r2.total_return) < 0.01

    def test_sharpe_ratio_with_no_volatility(self) -> None:
        """Very low volatility data should still produce a valid Sharpe."""
        df = _make_uptrend_ohlcv(n=120)
        engine = BacktestEngine()
        result = engine.run(df, _always_bullish)
        assert result.sharpe_ratio >= 0 or result.sharpe_ratio == 0.0

    def test_max_drawdown_non_negative(self) -> None:
        """Max drawdown should always be >= 0."""
        df = _make_uptrend_ohlcv(n=120)
        engine = BacktestEngine()
        result = engine.run(df, _always_bullish)
        assert result.max_drawdown >= 0
        assert result.max_drawdown <= 1.0

    def test_run_with_random_walk_data(self) -> None:
        """Run on random walk data - should not crash and produce consistent result type."""
        df = _make_ohlcv(n=100)
        engine = BacktestEngine()
        result = engine.run(df, _always_bullish)
        assert isinstance(result, BacktestResult)
        assert -1.5 <= result.total_return <= 1.5  # reasonable bounds
