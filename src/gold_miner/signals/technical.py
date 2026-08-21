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

    def _adx_series(self, period: int = 14) -> pd.Series | None:
        """计算 ADX 平滑序列 (供 adx / adx_convergence 共用)."""
        if len(self.df) < period * 2:
            return None
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
            return dx.ewm(alpha=1.0 / period, adjust=False).mean()
        except Exception:
            logger.debug("ADX 序列计算异常", exc_info=True)
            return None

    def adx(self, period: int = 14) -> dict[str, float]:
        """ADX 趋势强度.

        Returns:
            {"adx": float, "plus_di": float, "minus_di": float, "trend_regime": "trending"|"ranging"}
        """
        adx_series = self._adx_series(period)
        if adx_series is None:
            return {"adx": 20.0, "plus_di": 0.0, "minus_di": 0.0, "trend_regime": "ranging"}

        try:
            adx_val = float(adx_series.iloc[-1])
            regime = "trending" if adx_val > 25 else "ranging"

            # 复用 adx_series 的逻辑反推 DI 值不优雅, 直接基于原 df 重算 DI (保持与旧实现一致)
            high = self.df["high"]
            low = self.df["low"]
            from gold_miner.signals._price_utils import true_range
            tr = true_range(self.df)
            up_move = high.diff()
            down_move = -low.diff()
            plus_dm = pd.Series(0.0, index=self.df.index)
            minus_dm = pd.Series(0.0, index=self.df.index)
            plus_dm[(up_move > down_move) & (up_move > 0)] = up_move[(up_move > down_move) & (up_move > 0)]
            minus_dm[(down_move > up_move) & (down_move > 0)] = down_move[(down_move > up_move) & (down_move > 0)]
            atr_smoothed = tr.ewm(alpha=1.0 / period, adjust=False).mean()
            plus_di = (plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_smoothed) * 100
            minus_di = (minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_smoothed) * 100

            return {
                "adx": round(adx_val, 1),
                "plus_di": round(float(plus_di.iloc[-1]), 1),
                "minus_di": round(float(minus_di.iloc[-1]), 1),
                "trend_regime": regime,
            }
        except Exception:
            logger.debug("ADX 计算异常", exc_info=True)
            return {"adx": 20.0, "plus_di": 0.0, "minus_di": 0.0, "trend_regime": "ranging"}

    def adx_convergence(self, period: int = 14, lookback: int = 5) -> dict[str, float]:
        """ADX 趋势强度回落 — 趋势转震荡/蓄势检测.

        Returns:
            {"adx_converging": bool, "adx": float, "adx_prev": float, "drop_pct": float}
        """
        adx_series = self._adx_series(period)
        if adx_series is None or len(adx_series) <= lookback:
            return {"adx_converging": False, "adx": 0.0, "adx_prev": 0.0, "drop_pct": 0.0}

        try:
            adx_now = float(adx_series.iloc[-1])
            adx_prev = float(adx_series.iloc[-1 - lookback])
            drop_pct = (adx_prev - adx_now) / adx_prev if adx_prev > 0 else 0.0
            converging = adx_prev > 0 and drop_pct >= 0.15 and adx_now < 25
            return {
                "adx_converging": converging,
                "adx": round(adx_now, 1),
                "adx_prev": round(adx_prev, 1),
                "drop_pct": round(drop_pct, 3),
            }
        except Exception:
            logger.debug("ADX 收敛计算异常", exc_info=True)
            return {"adx_converging": False, "adx": 0.0, "adx_prev": 0.0, "drop_pct": 0.0}

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
    # 突破前兆检测 (2026-08-11 新增, Req1A)
    # ------------------------------------------------------------------

    def squeeze_detection(
        self, band_period: int = 20, std: int = 2, lookback: int = 20,
    ) -> dict[str, float]:
        """布林带收敛检测 — 突破前蓄势.

        Returns:
            {"squeeze": bool, "width_pct": float, "recent_min_pct": float,
             "contract_ratio": float, "in_tight_zone": bool}
        """
        if len(self.df) < band_period + lookback + 1:
            return {
                "squeeze": False, "width_pct": 0.0, "recent_min_pct": 0.0,
                "contract_ratio": 0.0, "in_tight_zone": False,
            }

        try:
            close = self.df["close"]
            sma = close.rolling(window=band_period).mean()
            rolling_std = close.rolling(window=band_period).std()
            width = ((sma + rolling_std * std) - (sma - rolling_std * std)) / sma

            w_now = float(width.iloc[-1])
            if pd.isna(w_now):
                return {
                    "squeeze": False, "width_pct": 0.0, "recent_min_pct": 0.0,
                    "contract_ratio": 0.0, "in_tight_zone": False,
                }

            recent_min = float(width.tail(lookback).min())
            prev_width = float(width.iloc[-1 - lookback]) if len(width) > lookback else w_now
            # 相对收敛: 处于窗口低位 ±5%
            near_floor = w_now <= recent_min * 1.05
            # 绝对收紧: 20日2σ带宽 < 3% (黄金 900-950 元/克日线的收敛阈值)
            tight = w_now <= 0.03

            return {
                "squeeze": bool(near_floor and tight),
                "width_pct": round(w_now, 4),
                "recent_min_pct": round(recent_min, 4),
                "contract_ratio": round(prev_width / w_now, 2) if w_now > 0 else 0.0,
                "in_tight_zone": bool(tight),
            }
        except Exception:
            logger.debug("布林带收敛计算异常", exc_info=True)
            return {
                "squeeze": False, "width_pct": 0.0, "recent_min_pct": 0.0,
                "contract_ratio": 0.0, "in_tight_zone": False,
            }

    def round_level_proximity(
        self, round_step: int = 50, band_pct: float = 0.01,
    ) -> dict[str, float]:
        """整数关口逼近检测 (950/1000 等心理关口).

        Returns:
            {"near_round_level": bool, "level": float, "distance_pct": float,
             "above": bool, "step": int}
        """
        latest = float(self.df["close"].iloc[-1])
        # 按量级定步长: <1000 用 50, 否则用 100
        step = 100 if latest >= 1000 else round_step
        nearest = round(latest / step) * step
        if nearest <= 0:
            return {"near_round_level": False, "level": 0.0, "distance_pct": 0.0, "above": False, "step": step}

        dist_pct = abs(latest - nearest) / nearest
        return {
            "near_round_level": bool(dist_pct <= band_pct),
            "level": float(nearest),
            "distance_pct": round(dist_pct, 4),
            "above": bool(latest > nearest),
            "step": step,
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

        # 6) 🆕 突破前兆: 布林收窄 + ADX回落 + 整数关口逼近 (2026-08-11 Req1A)
        squeeze = self.squeeze_detection()
        adxc = self.adx_convergence()
        rlp = self.round_level_proximity()
        sr = self.support_resistance()
        near_high = sr["resistance"] > 0 and sr["distance_to_resistance"] <= 0.015

        if squeeze["squeeze"]:
            # squeeze 本质是低波动状态, 不应被 high_vol 升级, 直设 WEAK
            signals.append(Signal(
                name="布林带收窄·蓄势待变", dimension="technical", direction=SignalDirection.NEUTRAL,
                strength=SignalStrength.WEAK, score=0.10,
                description=(
                    f"20日带宽 {squeeze['width_pct']*100:.1f}% 收敛至 {squeeze['recent_min_pct']*100:.1f}%"
                    f"最低区，突破前蓄势，方向未定，警惕放量突破"
                ),
                metadata={"source_tier": self.SOURCE_TIER, "adx": adx_data["adx"], "atr_pct": atr_data["atr_pct"]},
            ))

        if adxc["adx_converging"]:
            signals.append(Signal(
                name="ADX回落·趋势转震荡", dimension="technical", direction=SignalDirection.NEUTRAL,
                strength=SignalStrength.WEAK, score=0.08,
                description=(
                    f"ADX {adxc['adx']:.0f}（{adxc['adx_prev']:.0f}，回落{adxc['drop_pct']*100:.0f}%），"
                    f"趋势强度衰减，进入收敛蓄势"
                ),
                metadata={"source_tier": self.SOURCE_TIER, "adx": adx_data["adx"], "atr_pct": atr_data["atr_pct"]},
            ))

        if rlp["near_round_level"]:
            if not rlp["above"]:
                # 下方逼近 — 突破前兆主场景
                s, sc = _adjust(SignalStrength.WEAK, 0.15 * (1 - rlp["distance_pct"] / 0.01))
                signals.append(Signal(
                    name=f"逼近整数关口 {int(rlp['level'])}", dimension="technical",
                    direction=SignalDirection.BULLISH, strength=s, score=sc,
                    description=(
                        f"价格 {sr['latest']:.1f} 距整数关口 {int(rlp['level'])} 仅 "
                        f"{rlp['distance_pct']*100:.1f}%，突破前兆，警惕放量突破"
                    ),
                    metadata={"source_tier": self.SOURCE_TIER, "adx": adx_data["adx"], "atr_pct": atr_data["atr_pct"]},
                ))
            else:
                signals.append(Signal(
                    name=f"回踩整数关口 {int(rlp['level'])}", dimension="technical",
                    direction=SignalDirection.NEUTRAL, strength=SignalStrength.WEAK, score=0.05,
                    description=f"价格 {sr['latest']:.1f} 回踩整数关口 {int(rlp['level'])}",
                    metadata={"source_tier": self.SOURCE_TIER, "adx": adx_data["adx"], "atr_pct": atr_data["atr_pct"]},
                ))

        # 组合信号: 布林收窄 + (逼近关口 或 距20日高点≤1.5%) = 变盘窗口
        if squeeze["squeeze"] and ((rlp["near_round_level"] and not rlp["above"]) or near_high):
            signals.append(Signal(
                name="变盘窗口·突破前兆", dimension="technical", direction=SignalDirection.BULLISH,
                strength=SignalStrength.WEAK, score=0.20,
                description=(
                    f"布林收窄 + 逼近{'整数关口' + str(int(rlp['level'])) if rlp['near_round_level'] and not rlp['above'] else '20日高点'}，"
                    f"突破前兆，只出预警，人工决策是否提前布局（不自动挂单）"
                ),
                metadata={"source_tier": self.SOURCE_TIER, "adx": adx_data["adx"], "atr_pct": atr_data["atr_pct"]},
            ))

        # 7) 中性汇总: 无极端信号时输出综合指标快照，确保维度不消失
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


class IntradayAnalyzer:
    """日内分时分析器 - SGE 1分钟分时 (jdjr_query_stock intraday 免登录).

    输出日内动能/区间位置/振幅信号 (dimension="technical", WEAK 强度) + summary_dict 供报告板块。
    分时仅描述当日盘中状态，不构成独立趋势判断 (r033: 温和数据≠趋势确认)；
    休市时段数据冻结，快照信号会标注「数据截至」时间。
    """

    SOURCE_TIER = "T1"  # 京东金融网关转发的 SGE 分时

    # 阈值 (百分数值, 如 0.15 = 0.15%)
    _MOMENTUM_PCT = 0.15  # 30分钟动能触发阈值
    _POSITION_EXTREME = 0.8  # 日内区间位置高位/低位阈值
    _STALE_MINUTES = 120  # 分时数据陈旧阈值 (休市/接口延迟)

    def __init__(self, intraday: dict | None) -> None:
        self.points: list[dict] = [
            p for p in ((intraday or {}).get("points") or [])
            if p.get("price") and p.get("time") and p.get("date")
        ]
        self.prev_close = (intraday or {}).get("prev_close")

    # ------------------------------------------------------------------
    # 指标计算
    # ------------------------------------------------------------------

    def _last_point_dt(self):
        """末点时间 -> datetime; 解析失败返回 None."""
        from datetime import datetime

        try:
            last = self.points[-1]
            return datetime.strptime(f"{last['date']} {last['time']}", "%Y-%m-%d %H:%M")
        except (KeyError, ValueError):
            return None

    def _is_stale(self) -> bool:
        """末点距 now 超过阈值 (休市/接口延迟) -> 数据陈旧."""
        from datetime import datetime

        last_dt = self._last_point_dt()
        if last_dt is None:
            return True
        return (datetime.now() - last_dt).total_seconds() > self._STALE_MINUTES * 60

    def summary_dict(self) -> dict:
        """日内分时摘要 (报告板块渲染用)."""
        if not self.points:
            return {"gap": "无分时数据 (接口不可用或休市)"}

        prices = [float(p["price"]) for p in self.points]
        last = self.points[-1]
        day_open = prices[0]
        day_high, day_low = max(prices), min(prices)
        last_price = prices[-1]
        range_span = day_high - day_low
        position = (last_price - day_low) / range_span if range_span > 0 else 0.5
        vwap = sum(prices) / len(prices)

        # 30分钟动能 (最近30个1分钟点)
        window = prices[-30:]
        momentum_pct = ((window[-1] - window[0]) / window[0] * 100) if len(window) >= 2 and window[0] > 0 else 0.0

        # 振幅 (相对昨收)
        amplitude_pct = (
            (day_high - day_low) / self.prev_close * 100
            if self.prev_close and self.prev_close > 0 else None
        )

        # 涨跌分钟占比
        ups = sum(1 for i in range(1, len(prices)) if prices[i] > prices[i - 1])
        downs = sum(1 for i in range(1, len(prices)) if prices[i] < prices[i - 1])
        total_moves = ups + downs

        # 夜盘/日盘拆分 (夜盘: 时间>=20:00, 日盘: 09:00-15:30)
        night = [p for p in self.points if p["time"] >= "20:00"]
        day = [p for p in self.points if "09:00" <= p["time"] <= "15:30"]

        return {
            "point_count": len(self.points),
            "session_dates": sorted({p["date"] for p in self.points}),
            "day_open": round(day_open, 2),
            "day_high": round(day_high, 2),
            "day_low": round(day_low, 2),
            "last_price": round(last_price, 2),
            "last_time": f"{last['date']} {last['time']}",
            "position": round(position, 2),
            "vwap": round(vwap, 2),
            "dev_vs_vwap_pct": round((last_price - vwap) / vwap * 100, 2) if vwap > 0 else 0.0,
            "momentum_30m_pct": round(momentum_pct, 2),
            "amplitude_pct": round(amplitude_pct, 2) if amplitude_pct is not None else None,
            "change_pct": last.get("change_pct"),
            "up_minutes": ups,
            "down_minutes": downs,
            "up_ratio": round(ups / total_moves, 2) if total_moves else 0.5,
            "night_points": len(night),
            "day_points": len(day),
            "night_range": (
                [round(min(float(p["price"]) for p in night), 2), round(max(float(p["price"]) for p in night), 2)]
                if night else None
            ),
            "stale": self._is_stale(),
        }

    # ------------------------------------------------------------------
    # 信号生成
    # ------------------------------------------------------------------

    def generate_signals(self) -> list[Signal]:
        """生成日内分时信号.

        快照信号始终输出 (含完整日内统计); 动能/位置信号条件触发;
        数据陈旧 (休市) 时仅输出快照并标注截至时间。
        """
        if not self.points:
            return []

        s = self.summary_dict()
        if "gap" in s:
            return []

        stale_note = f"，数据截至 {s['last_time']}（休市/盘外）" if s["stale"] else ""
        signals: list[Signal] = []

        pos_desc = self._position_desc(s["position"])
        momentum = s["momentum_30m_pct"]
        mom_desc = "走强" if momentum > 0.05 else ("走弱" if momentum < -0.05 else "走平")

        # 1) 快照信号 (始终输出, 方向微偏由 30 分钟动能定)
        if momentum > self._MOMENTUM_PCT:
            snap_dir = SignalDirection.BULLISH
            snap_name = "日内分时·盘中偏强"
            snap_score = min(momentum / 10, 0.1)
        elif momentum < -self._MOMENTUM_PCT:
            snap_dir = SignalDirection.BEARISH
            snap_name = "日内分时·盘中偏弱"
            snap_score = -min(abs(momentum) / 10, 0.1)
        else:
            snap_dir = SignalDirection.NEUTRAL
            snap_name = "日内分时·盘中震荡"
            snap_score = 0.0

        amplitude_desc = f"振幅 {s['amplitude_pct']:.2f}%" if s["amplitude_pct"] is not None else ""
        signals.append(Signal(
            name=snap_name,
            dimension="technical",
            direction=snap_dir,
            strength=SignalStrength.WEAK,
            score=round(snap_score, 3),
            description=(
                f"现价 {s['last_price']:.2f}（{s['last_time']}）| 日内区间 "
                f"{s['day_low']:.2f}-{s['day_high']:.2f} {amplitude_desc} | "
                f"位置 {pos_desc} | 30分钟动能 {momentum:+.2f}% {mom_desc} | "
                f"现价较日内均价 {'上方' if s['dev_vs_vwap_pct'] >= 0 else '下方'} "
                f"{abs(s['dev_vs_vwap_pct']):.2f}%{stale_note}"
            ),
            metadata={"source_tier": self.SOURCE_TIER, **{k: v for k, v in s.items() if k != "gap"}},
        ))

        # 陈旧数据 (休市) 不再输出动能/位置信号 - 冻结盘面无交易意义
        if s["stale"]:
            return signals

        # 2) 30分钟动能信号
        if abs(momentum) >= self._MOMENTUM_PCT:
            is_up = momentum > 0
            signals.append(Signal(
                name=f"日内动能{'转强' if is_up else '转弱'}",
                dimension="technical",
                direction=SignalDirection.BULLISH if is_up else SignalDirection.BEARISH,
                strength=SignalStrength.WEAK,
                score=round(min(abs(momentum) / 20, 0.15), 3) * (1 if is_up else -1),
                description=(
                    f"最近30分钟 {momentum:+.2f}%，盘中短线动能{'上行' if is_up else '回落'}；"
                    f"仅日内状态描述，不构成加仓依据 (r033 温和动能≠趋势确认)"
                ),
                metadata={"source_tier": self.SOURCE_TIER, "momentum_30m_pct": momentum},
            ))

        # 3) 日内区间位置信号 (高位/低位徘徊)
        if s["position"] >= self._POSITION_EXTREME or s["position"] <= 1 - self._POSITION_EXTREME:
            near_high = s["position"] >= self._POSITION_EXTREME
            signals.append(Signal(
                name=f"日内{'高位' if near_high else '低位'}徘徊",
                dimension="technical",
                direction=SignalDirection.NEUTRAL,
                strength=SignalStrength.WEAK,
                score=0.0,
                description=(
                    f"现价处于日内区间 {s['position']:.0%} 位置（{'上沿' if near_high else '下沿'}），"
                    f"日内 {'冲高' if near_high else '回落'} 至 {'高点' if near_high else '低点'} "
                    f"{s['day_high'] if near_high else s['day_low']:.2f} 附近；"
                    f"对短线买卖点择时有参考意义"
                ),
                metadata={"source_tier": self.SOURCE_TIER, "position": s["position"]},
            ))

        return signals

    @staticmethod
    def _position_desc(position: float) -> str:
        """0-1 区间位置 -> 中文描述."""
        if position >= 0.8:
            return "区间上沿"
        if position >= 0.6:
            return "区间偏上"
        if position > 0.4:
            return "区间中轴"
        if position > 0.2:
            return "区间偏下"
        return "区间下沿"
