"""情绪面信号 — 上期所 AU 期货持仓 + 量价关系."""

import pandas as pd
from loguru import logger

from gold_miner.signals.base import Signal, SignalDirection, SignalStrength


class SentimentAnalyzer:
    """市场情绪分析器 — 基于国内期货数据."""

    def __init__(self, au_df: pd.DataFrame | None = None) -> None:
        self.au = au_df

    def generate_signals(self) -> list[Signal]:
        signals: list[Signal] = []
        if self.au is None or self.au.empty or len(self.au) < 5:
            return signals

        signals.extend(self._analyze_open_interest())
        signals.extend(self._analyze_volume_price())
        signals.extend(self._analyze_intraday_bias())
        return signals

    def _analyze_open_interest(self) -> list[Signal]:
        """持仓量趋势 — 增仓看涨，减仓看跌."""
        signals: list[Signal] = []
        try:
            oi = self.au["open_interest"].dropna()
            if len(oi) < 5:
                return signals

            latest = float(oi.iloc[-1])
            ma5 = float(oi.tail(5).mean())
            ma10 = float(oi.tail(10).mean()) if len(oi) >= 10 else ma5
            oi_change_5d = (oi.iloc[-1] - oi.iloc[-6]) / oi.iloc[-6] if len(oi) >= 6 else 0

            if ma5 > ma10 * 1.01:
                score = min(oi_change_5d * 10, 1.0)
                signals.append(Signal(
                    name="期货持仓量上升",
                    dimension="sentiment",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.MODERATE if score > 0.3 else SignalStrength.WEAK,
                    score=score,
                    description=(
                        f"AU期货持仓 {latest:.0f}手, 5日>10日均 (+{oi_change_5d:+.1%}), 资金流入"
                    ),
                ))
            elif ma5 < ma10 * 0.99:
                score = -min(abs(oi_change_5d) * 10, 1.0)
                signals.append(Signal(
                    name="期货持仓量下降",
                    dimension="sentiment",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.MODERATE if abs(score) > 0.3 else SignalStrength.WEAK,
                    score=score,
                    description=(
                        f"AU期货持仓 {latest:.0f}手, 5日<10日均 ({oi_change_5d:+.1%}), 资金流出"
                    ),
                ))
        except Exception as e:
            logger.warning(f"持仓量分析失败: {e}")
        return signals

    def _analyze_volume_price(self) -> list[Signal]:
        """量价关系 — 放量上涨=强势, 放量下跌=恐慌."""
        signals: list[Signal] = []
        try:
            close = self.au["close"]
            vol = self.au["volume"]
            if len(close) < 5:
                return signals

            chg_5d = (close.iloc[-1] - close.iloc[-6]) / close.iloc[-6]
            vol_ratio = float(vol.tail(5).mean()) / float(vol.tail(20).mean()) if len(vol) >= 20 else 1.0

            if chg_5d > 0.01 and vol_ratio > 1.2:
                signals.append(Signal(
                    name="放量上涨",
                    dimension="sentiment",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.STRONG,
                    score=0.6,
                    description=f"近5日涨{chg_5d:+.1%}, 量{vol_ratio:.1f}x, 多头强势",
                ))
            elif chg_5d < -0.01 and vol_ratio > 1.2:
                signals.append(Signal(
                    name="放量下跌",
                    dimension="sentiment",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.STRONG,
                    score=-0.5,
                    description=f"近5日跌{chg_5d:+.1%}, 量{vol_ratio:.1f}x, 恐慌抛售",
                ))
            elif chg_5d > 0.01 and vol_ratio < 0.8:
                signals.append(Signal(
                    name="缩量上涨",
                    dimension="sentiment",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.WEAK,
                    score=0.2,
                    description="涨但量缩, 上行动力不足",
                ))
        except Exception as e:
            logger.warning(f"量价分析失败: {e}")
        return signals

    def _analyze_intraday_bias(self) -> list[Signal]:
        """日内偏差 — 连续收阳/收阴."""
        signals: list[Signal] = []
        try:
            bias = self.au.get("intraday_bias", pd.Series(dtype=float))
            if bias.empty or len(bias) < 5:
                return signals

            positive = (bias.tail(5) > 0).sum()
            if positive >= 4:
                signals.append(Signal(
                    name="连续收阳",
                    dimension="sentiment",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.WEAK,
                    score=0.15,
                    description=f"近5日{positive}天收阳, 情绪偏多",
                ))
            elif positive <= 1:
                signals.append(Signal(
                    name="连续收阴",
                    dimension="sentiment",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.WEAK,
                    score=-0.15,
                    description=f"近5日仅{positive}天收阳, 情绪偏空",
                ))
        except Exception as e:
            logger.warning(f"日内偏差分析失败: {e}")
        return signals
