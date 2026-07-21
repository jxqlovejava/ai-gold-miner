"""K线形态识别 + 量价背离检测.

模块独立于 ``technical.py``，专门负责:
- 单K线反转形态（锤子线/倒锤子/十字星）
- 双K线反转形态（吞没）
- 三K线反转形态（晨星/暮星）
- 量价背离检测
- 多形态同向共振加成

所有信号归 ``dimension="technical"``, 单形态 WEAK，共振 MODERATE~STRONG。
"""
from __future__ import annotations

import logging

import pandas as pd

from gold_miner.signals.base import Signal, SignalDirection, SignalStrength

logger = logging.getLogger(__name__)


class CandlestickPatternDetector:
    """K线形态识别器.

    Args:
        df: 包含 open/high/low/close/volume 列的 OHLCV DataFrame。
    """

    def __init__(self, df: pd.DataFrame) -> None:
        required = {"open", "high", "low", "close"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"DataFrame 缺少必要列: {missing}")

        self.df = df.sort_values("timestamp").reset_index(drop=True).copy()
        self._has_volume = "volume" in self.df.columns and self.df["volume"].notna().any()

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _body(row: pd.Series) -> float:
        """实体大小."""
        return abs(row["close"] - row["open"])

    @staticmethod
    def _upper_shadow(row: pd.Series) -> float:
        """上影线长度."""
        return row["high"] - max(row["open"], row["close"])

    @staticmethod
    def _lower_shadow(row: pd.Series) -> float:
        """下影线长度."""
        return min(row["open"], row["close"]) - row["low"]

    @staticmethod
    def _total_range(row: pd.Series) -> float:
        """K线总振幅."""
        return row["high"] - row["low"]

    @staticmethod
    def _is_green(row: pd.Series) -> bool:
        return row["close"] > row["open"]

    @staticmethod
    def _is_red(row: pd.Series) -> bool:
        return row["close"] < row["open"]

    def _ma(self, period: int = 20) -> pd.Series:
        """简单移动平均（用于判断趋势位置）."""
        return self.df["close"].rolling(window=period).mean()

    # ------------------------------------------------------------------
    # 单K线形态
    # ------------------------------------------------------------------

    def detect_hammer(self) -> list[Signal]:
        """锤子线/倒锤子 — 单K线底部反转信号.

        锤子线判据:
        - 下影线 ≥ 实体 × 2
        - 上影线 ≤ 实体 × 0.3
        - 实体 > 0 (非十字星)
        - 价格在 MA20 下方（底部特征）

        倒锤子判据: 上影线替代下影线位置。
        射击之星判据: 同倒锤子但在顶部（看空）。

        Returns:
            0-1 条信号。
        """
        signals: list[Signal] = []
        if len(self.df) < 22:
            return signals

        try:
            latest = self.df.iloc[-1]
            body = self._body(latest)
            upper = self._upper_shadow(latest)
            lower = self._lower_shadow(latest)
            ma20 = self._ma(20).iloc[-1]
            close_price = float(latest["close"])

            if body <= 0:
                return signals  # doji, not a hammer

            in_lower_half = close_price < ma20
            in_upper_half = close_price > ma20

            # 锤子线 (底部看涨)
            if in_lower_half and lower >= body * 2 and upper <= body * 0.3:
                score = min(0.15, (lower / body - 2) * 0.05 + 0.10)
                signals.append(Signal(
                    name="锤子线(底部反转)",
                    dimension="technical",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.WEAK,
                    score=round(score, 2),
                    description=(
                        f"下影线({lower:.2f})为实体({body:.2f})的{lower/body:.1f}倍，"
                        f"价格({close_price:.1f})<MA20({ma20:.1f})，底部反转信号"
                    ),
                    metadata={
                        "pattern": "hammer",
                        "body": round(body, 2),
                        "lower_shadow": round(lower, 2),
                        "shadow_body_ratio": round(lower / body, 1),
                    },
                ))

            # 倒锤子 (底部看涨)
            if in_lower_half and upper >= body * 2 and lower <= body * 0.3:
                score = min(0.12, (upper / body - 2) * 0.04 + 0.08)
                signals.append(Signal(
                    name="倒锤子(底部反转)",
                    dimension="technical",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.WEAK,
                    score=round(score, 2),
                    description=(
                        f"上影线({upper:.2f})为实体({body:.2f})的{upper/body:.1f}倍，"
                        f"价格({close_price:.1f})<MA20({ma20:.1f})，底部反转信号"
                    ),
                    metadata={
                        "pattern": "inverted_hammer",
                        "body": round(body, 2),
                        "upper_shadow": round(upper, 2),
                    },
                ))

            # 射击之星 (顶部看空)
            if in_upper_half and upper >= body * 2 and lower <= body * 0.3:
                score = min(-0.12, -(upper / body - 2) * 0.04 - 0.08)
                signals.append(Signal(
                    name="射击之星(顶部反转)",
                    dimension="technical",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.WEAK,
                    score=round(score, 2),
                    description=(
                        f"上影线({upper:.2f})为实体({body:.2f})的{upper/body:.1f}倍，"
                        f"价格({close_price:.1f})>MA20({ma20:.1f})，顶部反转信号"
                    ),
                    metadata={
                        "pattern": "shooting_star",
                        "body": round(body, 2),
                        "upper_shadow": round(upper, 2),
                    },
                ))

        except Exception:
            logger.debug("锤子线检测异常", exc_info=True)

        return signals

    # ------------------------------------------------------------------
    # 双K线形态
    # ------------------------------------------------------------------

    def detect_engulfing(self) -> list[Signal]:
        """吞没形态 — 双K线反转信号.

        看涨吞没判据:
        - 前一根阴线 (close < open)
        - 当前阳线 (close > open)
        - 当前实体完全包裹前一根实体 (open ≤ prev_close AND close ≥ prev_open)

        看跌吞没: 相反。

        Returns:
            0-1 条信号。
        """
        signals: list[Signal] = []
        if len(self.df) < 3:
            return signals

        try:
            prev = self.df.iloc[-2]
            curr = self.df.iloc[-1]
            ma20 = self._ma(20).iloc[-1]
            close_price = float(curr["close"])

            prev_body = self._body(prev)
            curr_body = self._body(curr)

            if prev_body <= 0 or curr_body <= 0:
                return signals

            prev_green = self._is_green(prev)
            curr_green = self._is_green(curr)

            # 看涨吞没: 前阴后阳 + 当前包裹前一根
            if (not prev_green and curr_green
                    and curr["open"] <= prev["close"] and curr["close"] >= prev["open"]):
                engulf_ratio = curr_body / prev_body
                score = min(0.18, 0.08 + engulf_ratio * 0.05)
                # 若发生在底部(M20下方), 增强可信度
                if close_price < ma20:
                    score = min(0.25, score * 1.3)
                signals.append(Signal(
                    name="看涨吞没",
                    dimension="technical",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.WEAK,
                    score=round(score, 2),
                    description=(
                        f"阳线实体({curr_body:.2f})完全包裹前阴实体({prev_body:.2f})，"
                        f"吞没比 {engulf_ratio:.1f}:1，看涨反转"
                    ),
                    metadata={
                        "pattern": "bullish_engulfing",
                        "prev_body": round(prev_body, 2),
                        "curr_body": round(curr_body, 2),
                        "engulf_ratio": round(engulf_ratio, 1),
                    },
                ))

            # 看跌吞没: 前阳后阴 + 当前包裹前一根
            elif (prev_green and not curr_green
                    and curr["open"] >= prev["close"] and curr["close"] <= prev["open"]):
                engulf_ratio = curr_body / prev_body
                score = min(-0.18, -(0.08 + engulf_ratio * 0.05))
                if close_price > ma20:
                    score = max(-0.25, score * 1.3)
                signals.append(Signal(
                    name="看跌吞没",
                    dimension="technical",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.WEAK,
                    score=round(score, 2),
                    description=(
                        f"阴线实体({curr_body:.2f})完全包裹前阳实体({prev_body:.2f})，"
                        f"吞没比 {engulf_ratio:.1f}:1，看跌反转"
                    ),
                    metadata={
                        "pattern": "bearish_engulfing",
                        "prev_body": round(prev_body, 2),
                        "curr_body": round(curr_body, 2),
                        "engulf_ratio": round(engulf_ratio, 1),
                    },
                ))

        except Exception:
            logger.debug("吞没形态检测异常", exc_info=True)

        return signals

    # ------------------------------------------------------------------
    # 十字星
    # ------------------------------------------------------------------

    def detect_doji(self) -> list[Signal]:
        """十字星/长腿十字星 — 犹豫/转折信号.

        判据:
        - |open - close| ≤ (high - low) × threshold (默认 0.08)
        - 长腿十字星: 上下影线都 > 实体 × 3

        Returns:
            0-1 条信号（中性犹豫信号，score=0，仅作提醒）。
        """
        signals: list[Signal] = []
        if len(self.df) < 2:
            return signals

        try:
            latest = self.df.iloc[-1]
            body = self._body(latest)
            total = self._total_range(latest)

            if total <= 0:
                return signals

            body_ratio = body / total

            # 十字星: 实体极小
            if body_ratio <= 0.08:
                upper = self._upper_shadow(latest)
                lower = self._lower_shadow(latest)
                is_long_legged = upper > body * 3 and lower > body * 3

                # 十字星本身是中性/犹豫信号，不给出方向
                # 但长腿十字星在趋势末端更有意义
                name = "长腿十字星" if is_long_legged else "十字星"
                desc = (
                    f"{'长腿' if is_long_legged else ''}十字星: "
                    f"实体({body:.2f})仅占振幅({total:.2f})的{body_ratio:.1%}，"
                    f"多空僵持，关注次日方向确认"
                )

                signals.append(Signal(
                    name=name,
                    dimension="technical",
                    direction=SignalDirection.NEUTRAL,
                    strength=SignalStrength.WEAK,
                    score=0.0,  # doji has no directional bias
                    description=desc,
                    metadata={
                        "pattern": "long_legged_doji" if is_long_legged else "doji",
                        "body_ratio": round(body_ratio, 3),
                        "long_legged": is_long_legged,
                    },
                ))

        except Exception:
            logger.debug("十字星检测异常", exc_info=True)

        return signals

    # ------------------------------------------------------------------
    # 三K线形态
    # ------------------------------------------------------------------

    def detect_morning_evening_star(self) -> list[Signal]:
        """晨星/暮星 — 三K线反转形态.

        晨星 (看涨):
        - Day1: 大阴线 (body >= avg body, is_red)
        - Day2: 小实体/十字星 (body <= avg_body * 0.3), gap down or at bottom
        - Day3: 大阳线 (body >= avg_body, is_green), close > Day1 midpoint

        暮星 (看跌):
        - Day1: 大阳线, Day2: 小实体, Day3: 大阴线, close < Day1 midpoint

        Returns:
            0-1 条信号。
        """
        signals: list[Signal] = []
        if len(self.df) < 5:
            return signals

        try:
            d1 = self.df.iloc[-3]
            d2 = self.df.iloc[-2]
            d3 = self.df.iloc[-1]

            # 平均实体（最近20根）
            recent = self.df.tail(20)
            bodies = (recent["close"] - recent["open"]).abs()
            avg_body = float(bodies.mean()) if len(bodies) > 0 else 1.0
            if avg_body <= 0:
                return signals

            d1_body = self._body(d1)
            d2_body = self._body(d2)
            d3_body = self._body(d3)

            d1_mid = (d1["open"] + d1["close"]) / 2

            # 晨星
            if (
                self._is_red(d1) and d1_body >= avg_body * 0.8  # Day1: big red
                and d2_body <= avg_body * 0.3  # Day2: small body
                and self._is_green(d3) and d3_body >= avg_body * 0.8  # Day3: big green
                and d3["close"] > d1_mid  # close above D1 midpoint
            ):
                score = min(0.20, 0.10 + (d3["close"] - d1_mid) / d1_mid * 0.5)
                signals.append(Signal(
                    name="晨星(三K线底部反转)",
                    dimension="technical",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.WEAK,
                    score=round(score, 2),
                    description=(
                        f"大阴→小星→大阳，Day3收盘({d3['close']:.1f})超越"
                        f"Day1中点({d1_mid:.1f})，底部反转信号"
                    ),
                    metadata={
                        "pattern": "morning_star",
                        "d1_body": round(d1_body, 2),
                        "d3_body": round(d3_body, 2),
                        "avg_body": round(avg_body, 2),
                    },
                ))

            # 暮星
            elif (
                self._is_green(d1) and d1_body >= avg_body * 0.8
                and d2_body <= avg_body * 0.3
                and self._is_red(d3) and d3_body >= avg_body * 0.8
                and d3["close"] < d1_mid
            ):
                score = max(-0.20, -(0.10 + (d1_mid - d3["close"]) / d1_mid * 0.5))
                signals.append(Signal(
                    name="暮星(三K线顶部反转)",
                    dimension="technical",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.WEAK,
                    score=round(score, 2),
                    description=(
                        f"大阳→小星→大阴，Day3收盘({d3['close']:.1f})跌破"
                        f"Day1中点({d1_mid:.1f})，顶部反转信号"
                    ),
                    metadata={
                        "pattern": "evening_star",
                        "d1_body": round(d1_body, 2),
                        "d3_body": round(d3_body, 2),
                        "avg_body": round(avg_body, 2),
                    },
                ))

        except Exception:
            logger.debug("晨星/暮星检测异常", exc_info=True)

        return signals

    # ------------------------------------------------------------------
    # 量价背离
    # ------------------------------------------------------------------

    def detect_volume_price_divergence(self) -> list[Signal]:
        """量价背离检测.

        价格新高 + 量缩 → 顶部背离（看空）
        价格新低 + 量缩 → 底部背离（看多）

        需要 volume 列存在且非空。

        Returns:
            0-2 条信号。
        """
        signals: list[Signal] = []
        if not self._has_volume or len(self.df) < 21:
            return signals

        try:
            recent = self.df.tail(20)
            close = recent["close"]
            volume = recent["volume"]

            # 分前后两段对比：前10日 vs 后10日
            first_half_close = close.iloc[:10]
            second_half_close = close.iloc[10:]
            first_half_vol = volume.iloc[:10]
            second_half_vol = volume.iloc[10:]

            avg_close_1 = float(first_half_close.mean())
            avg_close_2 = float(second_half_close.mean())
            avg_vol_1 = float(first_half_vol.mean())
            avg_vol_2 = float(second_half_vol.mean())

            if avg_vol_1 <= 0:
                return signals

            price_change = (avg_close_2 / avg_close_1 - 1) * 100 if avg_close_1 > 0 else 0
            vol_change = (avg_vol_2 / avg_vol_1 - 1) * 100

            # 顶背离: 价格上涨 + 量萎缩
            if price_change > 1.0 and vol_change < -10:
                score = max(-0.30, -0.10 - abs(vol_change) / 100)
                signals.append(Signal(
                    name="量价顶背离",
                    dimension="technical",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.MODERATE,
                    score=round(score, 2),
                    description=(
                        f"近10日均价上涨 {price_change:+.1f}%，但均量萎缩 {vol_change:+.1f}%，"
                        f"量价背离预警顶部"
                    ),
                    metadata={
                        "pattern": "volume_divergence_bearish",
                        "price_change_pct": round(price_change, 1),
                        "vol_change_pct": round(vol_change, 1),
                    },
                ))

            # 底背离: 价格下跌 + 量萎缩
            elif price_change < -1.0 and vol_change < -10:
                score = min(0.30, 0.10 + abs(vol_change) / 100)
                signals.append(Signal(
                    name="量价底背离",
                    dimension="technical",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.MODERATE,
                    score=round(score, 2),
                    description=(
                        f"近10日均价下跌 {price_change:+.1f}%，但均量萎缩 {vol_change:+.1f}%，"
                        f"抛压减弱，底部信号"
                    ),
                    metadata={
                        "pattern": "volume_divergence_bullish",
                        "price_change_pct": round(price_change, 1),
                        "vol_change_pct": round(vol_change, 1),
                    },
                ))

        except Exception:
            logger.debug("量价背离检测异常", exc_info=True)

        return signals

    # ------------------------------------------------------------------
    # 共振加成
    # ------------------------------------------------------------------

    def _boost_resonance(self, signals: list[Signal]) -> list[Signal]:
        """多形态同向共振加成.

        统计非NEUTRAL信号的方向:
        - 2 个同向 → 追加 MODERATE 共振信号
        - 3+ 个同向 → 追加 STRONG 共振信号

        共振信号 score = 同向信号 score 之和 × 折扣系数。
        """
        if len(signals) < 2:
            return []

        # 按方向分组（排除 NEUTRAL）
        dir_map: dict[str, list[Signal]] = {"bullish": [], "bearish": []}
        for sig in signals:
            if sig.direction == SignalDirection.BULLISH:
                dir_map["bullish"].append(sig)
            elif sig.direction == SignalDirection.BEARISH:
                dir_map["bearish"].append(sig)

        boosted: list[Signal] = []

        for direction_label, group in dir_map.items():
            count = len(group)
            if count < 2:
                continue

            dir_enum = SignalDirection.BULLISH if direction_label == "bullish" else SignalDirection.BEARISH
            total_score = sum(abs(s.score) for s in group)
            pattern_names = [s.name for s in group]

            if count >= 3:
                strength = SignalStrength.STRONG
                score = min(0.50, total_score * 0.6)
            else:
                strength = SignalStrength.MODERATE
                score = min(0.35, total_score * 0.5)

            sign = 1 if direction_label == "bullish" else -1
            dir_cn = "看多" if direction_label == "bullish" else "看空"

            boosted.append(Signal(
                name=f"K线{count}形态共振({dir_cn})",
                dimension="technical",
                direction=dir_enum,
                strength=strength,
                score=round(sign * score, 2),
                description=(
                    f"{count}个K线形态同向{dir_cn}: "
                    f"{' + '.join(pattern_names)}，共振增强"
                ),
                metadata={
                    "pattern": "resonance",
                    "resonance_count": count,
                    "source_patterns": pattern_names,
                },
            ))

        return boosted

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def generate_signals(self) -> list[Signal]:
        """生成所有K线形态信号 + 共振加成.

        Returns:
            list[Signal]: 全部K线形态信号（0-8条），全部归 dimension="technical"。
        """
        signals: list[Signal] = []

        # 各形态独立检测
        signals.extend(self.detect_hammer())
        signals.extend(self.detect_engulfing())
        signals.extend(self.detect_doji())
        signals.extend(self.detect_morning_evening_star())
        signals.extend(self.detect_volume_price_divergence())

        # 共振加成（基于非 NEUTRAL 信号）
        non_neutral = [s for s in signals if s.direction != SignalDirection.NEUTRAL]
        signals.extend(self._boost_resonance(non_neutral))

        return signals
