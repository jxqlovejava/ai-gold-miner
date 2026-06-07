"""技术面信号 — RSI、MACD、布林带、支撑阻力."""

from datetime import datetime

import numpy as np
import pandas as pd

from gold_miner.signals.base import Signal, SignalDirection, SignalStrength


class TechnicalAnalyzer:
    """技术分析器."""

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df.copy()
        self._ensure_sorted()

    def _ensure_sorted(self) -> None:
        self.df = self.df.sort_values("timestamp").reset_index(drop=True)

    def rsi(self, period: int = 14) -> float:
        if len(self.df) < period + 1:
            return 50.0
        delta = self.df["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.rolling(window=period).mean().iloc[-1]
        avg_loss = loss.rolling(window=period).mean().iloc[-1]
        if avg_loss == 0 or pd.isna(avg_loss):
            return 100.0 if avg_gain > 0 else 50.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def macd(self, fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, float]:
        if len(self.df) < slow + signal + 1:
            return {"macd": 0.0, "signal": 0.0, "histogram": 0.0, "crossover": "none"}
        ema_fast = self.df["close"].ewm(span=fast).mean()
        ema_slow = self.df["close"].ewm(span=slow).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal).mean()
        histogram = macd_line - signal_line
        crossover = "none"
        if len(histogram) >= 2:
            prev, curr = histogram.iloc[-2], histogram.iloc[-1]
            if curr > 0 and prev <= 0:
                crossover = "bullish"
            elif curr < 0 and prev >= 0:
                crossover = "bearish"
        return {
            "macd": macd_line.iloc[-1],
            "signal": signal_line.iloc[-1],
            "histogram": histogram.iloc[-1],
            "crossover": crossover,
        }

    def bollinger(self, period: int = 20, std: int = 2) -> dict[str, float]:
        if len(self.df) < period:
            return {"upper": 0.0, "middle": 0.0, "lower": 0.0, "width_pct": 0.0, "position": 0.5}
        sma = self.df["close"].rolling(window=period).mean()
        rolling_std = self.df["close"].rolling(window=period).std()
        upper = sma + rolling_std * std
        lower = sma - rolling_std * std
        latest_close = self.df["close"].iloc[-1]
        upper_val = upper.iloc[-1]
        lower_val = lower.iloc[-1]
        return {
            "upper": upper_val,
            "middle": sma.iloc[-1],
            "lower": lower_val,
            "width_pct": (upper_val - lower_val) / sma.iloc[-1] if sma.iloc[-1] != 0 else 0.0,
            "position": (latest_close - lower_val) / (upper_val - lower_val)
            if upper_val != lower_val else 0.5,
        }

    def support_resistance(self, lookback: int = 20) -> dict[str, float]:
        recent = self.df.tail(lookback)
        return {
            "support": recent["low"].min(),
            "resistance": recent["high"].max(),
            "latest": self.df["close"].iloc[-1],
            "distance_to_support": (self.df["close"].iloc[-1] - recent["low"].min()) / recent["low"].min(),
            "distance_to_resistance": (recent["high"].max() - self.df["close"].iloc[-1]) / recent["high"].max(),
        }

    def generate_signals(self) -> list[Signal]:
        signals: list[Signal] = []

        rsi_val = self.rsi()
        if rsi_val < 30:
            signals.append(Signal(
                name="RSI超卖", dimension="technical", direction=SignalDirection.BULLISH,
                strength=SignalStrength.MODERATE, score=min((30 - rsi_val) / 30, 1.0),
                description=f"RSI={rsi_val:.1f} < 30，超卖反弹信号",
            ))
        elif rsi_val > 70:
            signals.append(Signal(
                name="RSI超买", dimension="technical", direction=SignalDirection.BEARISH,
                strength=SignalStrength.MODERATE, score=-min((rsi_val - 70) / 30, 1.0),
                description=f"RSI={rsi_val:.1f} > 70，超买回调信号",
            ))

        macd_data = self.macd()
        if macd_data["crossover"] == "bullish":
            signals.append(Signal(
                name="MACD金叉", dimension="technical", direction=SignalDirection.BULLISH,
                strength=SignalStrength.STRONG, score=0.6,
                description="MACD线上穿信号线",
            ))
        elif macd_data["crossover"] == "bearish":
            signals.append(Signal(
                name="MACD死叉", dimension="technical", direction=SignalDirection.BEARISH,
                strength=SignalStrength.STRONG, score=-0.6,
                description="MACD线下穿信号线",
            ))

        bb = self.bollinger()
        if bb["position"] < 0.1:
            signals.append(Signal(
                name="布林带下轨", dimension="technical", direction=SignalDirection.BULLISH,
                strength=SignalStrength.WEAK, score=0.3,
                description="价格触及布林带下轨",
            ))
        elif bb["position"] > 0.9:
            signals.append(Signal(
                name="布林带上轨", dimension="technical", direction=SignalDirection.BEARISH,
                strength=SignalStrength.WEAK, score=-0.3,
                description="价格触及布林带上轨",
            ))

        return signals
