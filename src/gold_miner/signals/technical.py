"""技术面信号 — RSI、MACD、布林带、支撑阻力、ATR、短期均线、ADX.

ATR/ADX 作为内部调节器，调节现有信号的 strength/score，不输出独立 Signal。
仅 MA crossover 输出独立 Signal (WEAK 强度)。
"""
from __future__ import annotations

import logging

import pandas as pd

from gold_miner.signals._price_utils import average_true_range
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength

logger = logging.getLogger(__name__)


class TechnicalAnalyzer:
    """技术分析器.

    在 ``generate_signals()`` 内部:
    1. 先计算 ATR/ADX 作为市场环境判断
    2. 用 ATR/ADX 调节 RSI/MACD/布林带信号的 strength
    3. 追加 MA crossover 信号
    """

    SOURCE_TIER = "T0"  # 数据源: SGE 官方交易所一手数据

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df.copy()
        self._ensure_sorted()

    def _ensure_sorted(self) -> None:
        self.df = self.df.sort_values("timestamp").reset_index(drop=True)

    # ------------------------------------------------------------------
    # 共享价格工具
    # ------------------------------------------------------------------

    def atr(self, period: int = 14) -> dict[str, float]:
        """计算 ATR 及波动率区间.

        Returns:
            {"atr": float, "atr_pct": float, "volatility_regime": "low"|"normal"|"high"}
        """
        if len(self.df) < period + 1:
            return {"atr": 0.0, "atr_pct": 0.0, "volatility_regime": "normal"}

        try:
            atr_series = average_true_range(self.df, period=period)
            latest_atr = float(atr_series.iloc[-1])
            latest_close = float(self.df["close"].iloc[-1])
            atr_pct = (latest_atr / latest_close) * 100 if latest_close > 0 else 0.0

            if atr_pct > 2.0:
                regime = "high"
            elif atr_pct < 1.0:
                regime = "low"
            else:
                regime = "normal"

            return {"atr": round(latest_atr, 2), "atr_pct": round(atr_pct, 2), "volatility_regime": regime}
        except Exception:
            logger.debug("ATR 计算异常", exc_info=True)
            return {"atr": 0.0, "atr_pct": 0.0, "volatility_regime": "normal"}

    def ma_crossover(self, fast: int = 5, slow: int = 20) -> dict:
        """短期均线交叉检测.

        Returns:
            {"crossover": "bullish"|"bearish"|"none", "fast_ma": float, "slow_ma": float, "gap_pct": float}
        """
        if len(self.df) < slow + 1:
            return {"crossover": "none", "fast_ma": 0.0, "slow_ma": 0.0, "gap_pct": 0.0}

        try:
            ma_fast = self.df["close"].rolling(window=fast).mean()
            ma_slow = self.df["close"].rolling(window=slow).mean()

            prev_fast, curr_fast = float(ma_fast.iloc[-2]), float(ma_fast.iloc[-1])
            prev_slow, curr_slow = float(ma_slow.iloc[-2]), float(ma_slow.iloc[-1])

            gap_pct = ((curr_fast - curr_slow) / curr_slow * 100) if curr_slow > 0 else 0.0

            crossover = "none"
            if prev_fast <= prev_slow and curr_fast > curr_slow:
                crossover = "bullish"
            elif prev_fast >= prev_slow and curr_fast < curr_slow:
                crossover = "bearish"

            return {
                "crossover": crossover,
                "fast_ma": round(curr_fast, 2),
                "slow_ma": round(curr_slow, 2),
                "gap_pct": round(gap_pct, 2),
            }
        except Exception:
            logger.debug("MA crossover 计算异常", exc_info=True)
            return {"crossover": "none", "fast_ma": 0.0, "slow_ma": 0.0, "gap_pct": 0.0}

    def adx(self, period: int = 14) -> dict[str, float]:
        """ADX 趋势强度.

        Returns:
            {"adx": float, "plus_di": float, "minus_di": float, "trend_regime": "trending"|"ranging"}
        """
        if len(self.df) < period * 2:
            return {"adx": 20.0, "plus_di": 0.0, "minus_di": 0.0, "trend_regime": "ranging"}

        try:
            high = self.df["high"]
            low = self.df["low"]

            # True Range (已用共享工具, 这里直接调 internal)
            from gold_miner.signals._price_utils import true_range
            tr = true_range(self.df)

            up_move = high.diff()
            down_move = -low.diff()

            plus_dm = pd.Series(0.0, index=self.df.index)
            minus_dm = pd.Series(0.0, index=self.df.index)

            plus_mask = (up_move > down_move) & (up_move > 0)
            minus_mask = (down_move > up_move) & (down_move > 0)
            plus_dm[plus_mask] = up_move[plus_mask]
            minus_dm[minus_mask] = down_move[minus_mask]

            atr_smoothed = tr.ewm(alpha=1.0 / period, adjust=False).mean()
            plus_di = (plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_smoothed) * 100
            minus_di = (minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_smoothed) * 100

            dx = ((plus_di - minus_di).abs() / (plus_di + minus_di)) * 100
            adx_val = float(dx.ewm(alpha=1.0 / period, adjust=False).mean().iloc[-1])

            regime = "trending" if adx_val > 25 else "ranging"

            return {
                "adx": round(adx_val, 1),
                "plus_di": round(float(plus_di.iloc[-1]), 1),
                "minus_di": round(float(minus_di.iloc[-1]), 1),
                "trend_regime": regime,
            }
        except Exception:
            logger.debug("ADX 计算异常", exc_info=True)
            return {"adx": 20.0, "plus_di": 0.0, "minus_di": 0.0, "trend_regime": "ranging"}

    # ------------------------------------------------------------------
    # 原有指标 (不变)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # 信号生成 (含 ATR/ADX 调节)
    # ------------------------------------------------------------------

    def generate_signals(self) -> list[Signal]:
        """生成所有技术面信号.

        ATR/ADX 作为内部调节器 — 不输出独立 Signal，仅调节已有信号的
        strength 和 score。MA crossover 输出 WEAK 强度的独立 Signal。
        """
        signals: list[Signal] = []

        # 1) 市场环境判断
        atr_data = self.atr()
        adx_data = self.adx()

        in_range = adx_data["adx"] < 20
        high_vol = atr_data["volatility_regime"] == "high"
        low_vol = atr_data["volatility_regime"] == "low"

        def _adjust(strength: SignalStrength, score: float) -> tuple[SignalStrength, float]:
            """ATR/ADX 调节器: 低波降级, 震荡市削弱, 高波保留/升级."""
            # 低波市场: 所有信号降一级, score 打折
            if low_vol:
                if strength == SignalStrength.STRONG:
                    return (SignalStrength.MODERATE, score * 0.7)
                elif strength == SignalStrength.MODERATE:
                    return (SignalStrength.WEAK, score * 0.5)
                else:
                    return (SignalStrength.WEAK, score * 0.5)
            # 震荡市: MODERATE 以上信号降一级
            if in_range:
                if strength == SignalStrength.STRONG:
                    return (SignalStrength.MODERATE, score * 0.8)
                elif strength == SignalStrength.MODERATE:
                    return (SignalStrength.WEAK, score * 0.7)
            # 高波市场: WEAK 升 MODERATE (宽幅边界的信号更有意义)
            if high_vol and strength == SignalStrength.WEAK:
                return (SignalStrength.MODERATE, score * 1.3)
            # 趋势市: 保持原值
            return (strength, score)

        # 2) RSI — 阈值 20/80（更极端才触发），分数随阈值线性缩放
        rsi_val = self.rsi()
        if rsi_val < 20:
            s, sc = _adjust(SignalStrength.MODERATE, min((20 - rsi_val) / 20, 1.0))
            signals.append(Signal(
                name="RSI超卖", dimension="technical", direction=SignalDirection.BULLISH,
                strength=s, score=sc,
                description=f"RSI={rsi_val:.1f} < 20，超卖反弹信号",
                metadata={"source_tier": self.SOURCE_TIER, "adx": adx_data["adx"], "atr_pct": atr_data["atr_pct"]},
            ))
        elif rsi_val > 80:
            s, sc = _adjust(SignalStrength.MODERATE, -min((rsi_val - 80) / 20, 1.0))
            signals.append(Signal(
                name="RSI超买", dimension="technical", direction=SignalDirection.BEARISH,
                strength=s, score=sc,
                description=f"RSI={rsi_val:.1f} > 80，超买回调信号",
                metadata={"source_tier": self.SOURCE_TIER, "adx": adx_data["adx"], "atr_pct": atr_data["atr_pct"]},
            ))

        # 3) MACD
        macd_data = self.macd()
        if macd_data["crossover"] == "bullish":
            s, sc = _adjust(SignalStrength.STRONG, 0.6)
            signals.append(Signal(
                name="MACD金叉", dimension="technical", direction=SignalDirection.BULLISH,
                strength=s, score=sc,
                description="MACD线上穿信号线",
                metadata={"source_tier": self.SOURCE_TIER, "adx": adx_data["adx"], "atr_pct": atr_data["atr_pct"]},
            ))
        elif macd_data["crossover"] == "bearish":
            s, sc = _adjust(SignalStrength.STRONG, -0.6)
            signals.append(Signal(
                name="MACD死叉", dimension="technical", direction=SignalDirection.BEARISH,
                strength=s, score=sc,
                description="MACD线下穿信号线",
                metadata={"source_tier": self.SOURCE_TIER, "adx": adx_data["adx"], "atr_pct": atr_data["atr_pct"]},
            ))

        # 4) 布林带
        bb = self.bollinger()
        if bb["position"] < 0.1:
            s, sc = _adjust(SignalStrength.WEAK, 0.3)
            signals.append(Signal(
                name="布林带下轨", dimension="technical", direction=SignalDirection.BULLISH,
                strength=s, score=sc,
                description="价格触及布林带下轨",
                metadata={"source_tier": self.SOURCE_TIER, "adx": adx_data["adx"], "atr_pct": atr_data["atr_pct"]},
            ))
        elif bb["position"] > 0.9:
            s, sc = _adjust(SignalStrength.WEAK, -0.3)
            signals.append(Signal(
                name="布林带上轨", dimension="technical", direction=SignalDirection.BEARISH,
                strength=s, score=sc,
                description="价格触及布林带上轨",
                metadata={"source_tier": self.SOURCE_TIER, "adx": adx_data["adx"], "atr_pct": atr_data["atr_pct"]},
            ))

        # 5) 🆕 MA crossover
        ma = self.ma_crossover()
        if ma["crossover"] == "bullish":
            s, sc = _adjust(SignalStrength.WEAK, 0.2)
            signals.append(Signal(
                name="MA5金叉MA20", dimension="technical", direction=SignalDirection.BULLISH,
                strength=s, score=sc,
                description=f"MA5({ma['fast_ma']:.1f})上穿MA20({ma['slow_ma']:.1f})，短期趋势转多",
                metadata={"source_tier": self.SOURCE_TIER, "adx": adx_data["adx"], "atr_pct": atr_data["atr_pct"]},
            ))
        elif ma["crossover"] == "bearish":
            s, sc = _adjust(SignalStrength.WEAK, -0.2)
            signals.append(Signal(
                name="MA5死叉MA20", dimension="technical", direction=SignalDirection.BEARISH,
                strength=s, score=sc,
                description=f"MA5({ma['fast_ma']:.1f})下穿MA20({ma['slow_ma']:.1f})，短期趋势转空",
                metadata={"source_tier": self.SOURCE_TIER, "adx": adx_data["adx"], "atr_pct": atr_data["atr_pct"]},
            ))

        # 6) 中性汇总: 无极端信号时输出综合指标快照，确保维度不消失
        if not signals:
            bb = self.bollinger()
            sr = self.support_resistance()
            rsi_val = self.rsi()
            macd_data = self.macd()

            trend_detail_parts: list[str] = []
            if adx_data["adx"] >= 25:
                if adx_data["plus_di"] > adx_data["minus_di"]:
                    trend_detail_parts.append(f"ADX {adx_data['adx']:.0f} 趋势市 +DI占优")
                else:
                    trend_detail_parts.append(f"ADX {adx_data['adx']:.0f} 趋势市 -DI占优")
            else:
                trend_detail_parts.append(f"ADX {adx_data['adx']:.0f} 震荡市")

            if macd_data["histogram"] > 0:
                trend_detail_parts.append("MACD柱转正")
            else:
                trend_detail_parts.append("MACD柱为负")

            if bb["position"] > 0.5:
                trend_detail_parts.append("布林带上半区")
            else:
                trend_detail_parts.append("布林带下半区")

            # 信号名: 综合 ADX 趋势 + MACD + 布林带判断微偏方向
            adx_bearish = adx_data["adx"] >= 25 and adx_data["plus_di"] <= adx_data["minus_di"]
            adx_bullish = adx_data["adx"] >= 25 and adx_data["plus_di"] > adx_data["minus_di"]
            macd_improving = macd_data["histogram"] > 0
            bb_upper = bb["position"] > 0.5

            # 三信号投票定名称
            bias_votes = 0
            if adx_bullish:
                bias_votes += 1
            elif adx_bearish:
                bias_votes -= 1
            if macd_improving:
                bias_votes += 1
            else:
                bias_votes -= 1
            if bb_upper:
                bias_votes += 1
            else:
                bias_votes -= 1

            if bias_votes >= 2:
                sig_name = "技术面无极端信号·微偏多"
                sig_dir = SignalDirection.BULLISH
            elif bias_votes <= -2:
                sig_name = "技术面无极端信号·微偏空"
                sig_dir = SignalDirection.BEARISH
            else:
                sig_name = "技术面无极端信号·中性"
                sig_dir = SignalDirection.NEUTRAL

            # 微偏得分: RSI 偏离 50 的量 + ADX DI 差 + BB 位置偏离
            neutral_score = round(
                (rsi_val - 50) * 0.002  # RSI 60→+0.02, 40→-0.02
                + (adx_data["plus_di"] - adx_data["minus_di"]) * 0.005  # ±5 DI diff→±0.025
                + (bb["position"] - 0.5) * 0.05,  # ±0.25 pos→±0.0125
                3,
            )

            summary = (
                f"RSI={rsi_val:.0f} | {', '.join(trend_detail_parts)} | "
                f"支撑{sr['support']:.0f}/阻力{sr['resistance']:.0f} | "
                f"距支撑{sr['distance_to_support']*100:.1f}% 距阻力{sr['distance_to_resistance']*100:.1f}%"
            )
            signals.append(Signal(
                name=sig_name,
                dimension="technical",
                direction=sig_dir,
                strength=SignalStrength.WEAK,
                score=neutral_score,
                description=summary,
                metadata={
                    "source_tier": self.SOURCE_TIER,
                    "rsi": round(rsi_val, 1),
                    "adx": adx_data["adx"],
                    "plus_di": adx_data["plus_di"],
                    "minus_di": adx_data["minus_di"],
                    "bb_position": round(bb["position"], 2),
                    "macd_histogram": round(macd_data["histogram"], 1),
                    "ma5": ma["fast_ma"],
                    "ma20": ma["slow_ma"],
                    "ma_gap_pct": ma["gap_pct"],
                    "support": sr["support"],
                    "resistance": sr["resistance"],
                    "volatility_regime": atr_data["volatility_regime"],
                    "atr_pct": atr_data["atr_pct"],
                },
            ))

        return signals
