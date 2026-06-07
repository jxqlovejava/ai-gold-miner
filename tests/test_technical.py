"""Tests for signals/technical.py — TechnicalAnalyzer."""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from gold_miner.signals.base import SignalDirection
from gold_miner.signals.technical import TechnicalAnalyzer


def _make_df(prices: list[float], start: datetime | None = None) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from close prices."""
    if start is None:
        start = datetime(2025, 1, 1)
    n = len(prices)
    timestamps = [start + timedelta(hours=i) for i in range(n)]
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": prices,
        "high": [p * 1.01 for p in prices],
        "low": [p * 0.99 for p in prices],
        "close": prices,
        "volume": [1000] * n,
    })


def _make_uptrend(length: int = 40, start_price: float = 50.0, end_price: float = 100.0) -> pd.DataFrame:
    """Create a sustained uptrend (pushes RSI > 70)."""
    prices = np.linspace(start_price, end_price, length).tolist()
    return _make_df(prices)


def _make_downtrend(length: int = 40, start_price: float = 100.0, end_price: float = 50.0) -> pd.DataFrame:
    """Create a sustained downtrend (pushes RSI < 30)."""
    prices = np.linspace(start_price, end_price, length).tolist()
    return _make_df(prices)


def _make_macd_bullish_crossover() -> pd.DataFrame:
    """Create price data that ends with a bullish MACD crossover.

    Long decline followed by recovery — the recovery generates
    a fast EMA that crosses above the slow EMA at the last bar.
    """
    n = 120
    prices = [100.0]
    for i in range(1, n):
        if i < 30:
            prices.append(prices[-1] * (1 - 0.008))       # decline
        elif i < 50:
            prices.append(prices[-1] * (1 - 0.020))       # sharp decline
        elif i < 70:
            prices.append(prices[-1] * (1 + 0.015))       # recovery
        elif i < 90:
            prices.append(prices[-1] * (1 + 0.025))       # strong recovery
        else:
            prices.append(prices[-1] * (1 + 0.005))       # slow down
    # Find the last bullish crossover and truncate data to end there
    close = pd.Series(prices)
    ema_fast = close.ewm(span=12).mean()
    ema_slow = close.ewm(span=26).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=9).mean()
    histogram = macd_line - signal_line
    crossover_idx = None
    for i in range(len(histogram) - 1, 0, -1):
        if histogram.iloc[i] > 0 and histogram.iloc[i - 1] <= 0:
            crossover_idx = i
            break
    if crossover_idx is not None:
        prices = prices[: crossover_idx + 1]
    return _make_df(prices)


def _make_macd_bearish_crossover() -> pd.DataFrame:
    """Create price data that ends with a bearish MACD crossover."""
    n = 120
    prices = [100.0]
    for i in range(1, n):
        if i < 25:
            prices.append(prices[-1] * (1 + 0.008))       # gradual rise
        elif i < 40:
            prices.append(prices[-1] * (1 + 0.020))       # strong rise
        elif i < 60:
            prices.append(prices[-1] * (1 - 0.010))       # decline
        elif i < 75:
            prices.append(prices[-1] * (1 - 0.020))       # sharp decline
        elif i < 85:
            prices.append(prices[-1] * (1 - 0.010))       # continued decline
        else:
            prices.append(prices[-1] * (1 - 0.005))       # stabilization
    close = pd.Series(prices)
    ema_fast = close.ewm(span=12).mean()
    ema_slow = close.ewm(span=26).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=9).mean()
    histogram = macd_line - signal_line
    crossover_idx = None
    for i in range(len(histogram) - 1, 0, -1):
        if histogram.iloc[i] < 0 and histogram.iloc[i - 1] >= 0:
            crossover_idx = i
            break
    if crossover_idx is not None:
        prices = prices[: crossover_idx + 1]
    return _make_df(prices)


class TestTechnicalAnalyzerRSI:
    def test_rsi_oversold_below_30(self) -> None:
        """Sustained downtrend should push RSI below 30."""
        df = _make_downtrend(length=30)
        analyzer = TechnicalAnalyzer(df)
        rsi_val = analyzer.rsi(period=14)
        assert rsi_val < 30

    def test_rsi_overbought_above_70(self) -> None:
        """Sustained uptrend should push RSI above 70."""
        df = _make_uptrend(length=30)
        analyzer = TechnicalAnalyzer(df)
        rsi_val = analyzer.rsi(period=14)
        assert rsi_val > 70

    def test_rsi_centerish_for_flat_prices(self) -> None:
        """Flat prices should produce RSI near 50."""
        prices = [100.0] * 20
        df = _make_df(prices)
        analyzer = TechnicalAnalyzer(df)
        rsi_val = analyzer.rsi(period=14)
        assert rsi_val == 50.0

    def test_rsi_returns_50_for_insufficient_data(self) -> None:
        """With fewer rows than period+1, RSI returns 50."""
        df = _make_df([100, 101, 102])
        analyzer = TechnicalAnalyzer(df)
        assert analyzer.rsi(period=14) == 50.0


class TestTechnicalAnalyzerMACD:
    def test_bullish_crossover_detected(self) -> None:
        df = _make_macd_bullish_crossover()
        analyzer = TechnicalAnalyzer(df)
        macd_data = analyzer.macd()
        assert macd_data["crossover"] == "bullish"

    def test_bearish_crossover_detected(self) -> None:
        df = _make_macd_bearish_crossover()
        analyzer = TechnicalAnalyzer(df)
        macd_data = analyzer.macd()
        assert macd_data["crossover"] == "bearish"

    def test_macd_returns_none_for_insufficient_data(self) -> None:
        df = _make_df([100, 101, 102])
        analyzer = TechnicalAnalyzer(df)
        macd_data = analyzer.macd()
        assert macd_data["crossover"] == "none"
        assert macd_data["macd"] == 0.0
        assert macd_data["histogram"] == 0.0

    def test_macd_has_all_keys(self) -> None:
        df = _make_uptrend(length=50)
        analyzer = TechnicalAnalyzer(df)
        macd_data = analyzer.macd()
        assert set(macd_data.keys()) == {"macd", "signal", "histogram", "crossover"}


class TestTechnicalAnalyzerBollinger:
    def test_position_at_middle_for_flat_prices(self) -> None:
        """With flat prices, position should be 0.5."""
        prices = [100.0] * 25
        df = _make_df(prices)
        analyzer = TechnicalAnalyzer(df)
        bb = analyzer.bollinger()
        assert bb["position"] == 0.5

    def test_returns_defaults_for_insufficient_data(self) -> None:
        df = _make_df([100, 101, 102])
        analyzer = TechnicalAnalyzer(df)
        bb = analyzer.bollinger()
        assert bb["position"] == 0.5
        assert bb["upper"] == 0.0

    def test_has_all_keys(self) -> None:
        df = _make_uptrend(length=30)
        analyzer = TechnicalAnalyzer(df)
        bb = analyzer.bollinger()
        assert set(bb.keys()) == {"upper", "middle", "lower", "width_pct", "position"}


class TestTechnicalAnalyzerSupportResistance:
    def test_returns_float_values(self) -> None:
        df = _make_uptrend(length=30)
        analyzer = TechnicalAnalyzer(df)
        sr = analyzer.support_resistance(lookback=20)
        assert isinstance(sr["support"], float)
        assert isinstance(sr["resistance"], float)
        assert sr["support"] <= sr["resistance"]

    def test_support_is_min_of_low(self) -> None:
        from datetime import datetime
        timestamps = [datetime(2025, 1, 1) + timedelta(hours=i) for i in range(5)]
        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": [100.0, 102.0, 98.0, 105.0, 101.0],
            "high": [101.0, 103.0, 99.0, 106.0, 102.0],
            "low": [99.0, 101.0, 97.0, 104.0, 100.0],
            "close": [100.0, 102.0, 98.0, 105.0, 101.0],
            "volume": [1000, 1000, 1000, 1000, 1000],
        })
        analyzer = TechnicalAnalyzer(df)
        sr = analyzer.support_resistance(lookback=5)
        assert sr["support"] == 97.0
        assert sr["resistance"] == 106.0

    def test_distance_to_support_is_positive(self) -> None:
        """Distance to support should be >= 0 since close >= low min."""
        df = _make_uptrend(length=30)
        analyzer = TechnicalAnalyzer(df)
        sr = analyzer.support_resistance()
        assert sr["distance_to_support"] >= 0

    def test_distance_to_resistance_is_positive(self) -> None:
        df = _make_uptrend(length=30)
        analyzer = TechnicalAnalyzer(df)
        sr = analyzer.support_resistance()
        assert sr["distance_to_resistance"] >= 0


class TestTechnicalAnalyzerGenerateSignals:
    def test_oversold_generates_bullish_rsi_signal(self) -> None:
        df = _make_downtrend(length=30)
        analyzer = TechnicalAnalyzer(df)
        signals = analyzer.generate_signals()
        rsi_signals = [s for s in signals if "RSI" in s.name]
        assert len(rsi_signals) >= 1
        assert rsi_signals[0].direction == SignalDirection.BULLISH

    def test_overbought_generates_bearish_rsi_signal(self) -> None:
        df = _make_uptrend(length=30)
        analyzer = TechnicalAnalyzer(df)
        signals = analyzer.generate_signals()
        rsi_signals = [s for s in signals if "RSI" in s.name]
        assert len(rsi_signals) >= 1
        assert rsi_signals[0].direction == SignalDirection.BEARISH

    def test_bullish_macd_generates_signal(self) -> None:
        df = _make_macd_bullish_crossover()
        analyzer = TechnicalAnalyzer(df)
        signals = analyzer.generate_signals()
        macd_signals = [s for s in signals if "MACD" in s.name]
        assert any("金叉" in s.name for s in macd_signals)

    def test_bearish_macd_generates_signal(self) -> None:
        df = _make_macd_bearish_crossover()
        analyzer = TechnicalAnalyzer(df)
        signals = analyzer.generate_signals()
        macd_signals = [s for s in signals if "MACD" in s.name]
        assert any("死叉" in s.name for s in macd_signals)

    def test_generate_signals_returns_list_of_signals(self) -> None:
        df = _make_uptrend(length=60)
        analyzer = TechnicalAnalyzer(df)
        signals = analyzer.generate_signals()
        assert isinstance(signals, list)
        if signals:
            from gold_miner.signals.base import Signal
            assert all(isinstance(s, Signal) for s in signals)

    def test_all_signals_have_technical_dimension(self) -> None:
        df = _make_macd_bullish_crossover()
        analyzer = TechnicalAnalyzer(df)
        signals = analyzer.generate_signals()
        for s in signals:
            assert s.dimension == "technical"


class TestTechnicalAnalyzerDataSorting:
    def test_ensures_sorted_by_timestamp(self) -> None:
        """Analyzer should sort data by timestamp even if input is unsorted."""
        timestamps = [
            datetime(2025, 1, 3),
            datetime(2025, 1, 1),
            datetime(2025, 1, 2),
        ]
        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": [102, 100, 101],
            "high": [103, 101, 102],
            "low": [101, 99, 100],
            "close": [102, 100, 101],
            "volume": [1000, 1000, 1000],
        })
        analyzer = TechnicalAnalyzer(df)
        # Access internal df to verify it was sorted
        assert analyzer.df["timestamp"].is_monotonic_increasing
