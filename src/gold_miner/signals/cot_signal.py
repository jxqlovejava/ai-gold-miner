"""COT持仓信号 — 聪明钱方向判断.

基于CFTC每周COT报告中的非商业持仓变化，生成趋势和极端信号。

信号逻辑:
1. 非商业净多仓趋势 — 连续3周增加=看涨，连续3周减少=看跌
2. 极端持仓 — 52周极值区间位置 (>90% = 超买警告, <10% = 超卖机会)
3. 商业持仓背离 — 商业净空仓减少 = 套保盘退缩 = 看涨
4. 非商业/商业持仓比 — 比值过高 = 聪明钱过于拥挤
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from gold_miner.data.cot_report import CotReportFetcher
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength


class CotSignalGenerator:
    """COT持仓信号生成器."""

    def __init__(self) -> None:
        self.fetcher = CotReportFetcher()

    def generate_signals(self) -> list[Signal]:
        """生成所有COT相关信号."""
        signals: list[Signal] = []
        signals.extend(self._trend_signals())
        signals.extend(self._extreme_signals())
        signals.extend(self._divergence_signals())
        return signals

    def _trend_signals(self) -> list[Signal]:
        """趋势信号 — 非商业净持仓连续变化方向."""
        signals: list[Signal] = []
        try:
            summary = self.fetcher.fetch_net_position(weeks=4)
            if summary.get("status") != "ok":
                return signals

            trend = summary.get("trend", "neutral")
            change = summary.get("change", 0)
            pct_change = summary.get("pct_change", 0)
            latest_net = summary.get("latest_net", 0)

            if trend == "up" and change > 0:
                strength = SignalStrength.STRONG if pct_change > 5 else SignalStrength.MODERATE
                score = min(pct_change / 10, 0.8)
                signals.append(Signal(
                    name="COT聪明钱加仓",
                    dimension="sentiment",
                    direction=SignalDirection.BULLISH,
                    strength=strength,
                    score=round(score, 2),
                    description=(
                        f"非商业净多仓连续增加: {latest_net:,}手 "
                        f"(+{change:,}, +{pct_change:.1f}%)，机构看涨"
                    ),
                    metadata={
                        "source": "cot_report",
                        "latest_net": latest_net,
                        "change": change,
                        "trend": trend,
                    },
                ))
            elif trend == "down" and change < 0:
                strength = SignalStrength.STRONG if pct_change < -5 else SignalStrength.MODERATE
                score = max(pct_change / 10, -0.8)
                signals.append(Signal(
                    name="COT聪明钱减仓",
                    dimension="sentiment",
                    direction=SignalDirection.BEARISH,
                    strength=strength,
                    score=round(score, 2),
                    description=(
                        f"非商业净多仓连续减少: {latest_net:,}手 "
                        f"({change:,}, {pct_change:.1f}%)，机构看空"
                    ),
                    metadata={
                        "source": "cot_report",
                        "latest_net": latest_net,
                        "change": change,
                        "trend": trend,
                    },
                ))

        except Exception as e:
            logger.debug(f"COT趋势信号异常: {e}")

        return signals

    def _extreme_signals(self) -> list[Signal]:
        """极端持仓信号 — 52周极值区间位置."""
        signals: list[Signal] = []
        try:
            summary = self.fetcher.fetch_net_position(weeks=52)
            if summary.get("status") != "ok":
                return signals

            position = summary.get("position_in_52w_range", 0.5)
            latest_net = summary.get("latest_net", 0)

            if position > 0.90:
                signals.append(Signal(
                    name="COT聪明钱极度拥挤(警告)",
                    dimension="sentiment",
                    direction=SignalDirection.BEARISH,  # 极度拥挤后通常反转
                    strength=SignalStrength.MODERATE,
                    score=-0.35,
                    description=(
                        f"非商业净多仓处于52周高位 ({position:.0%}), "
                        f"机构过于拥挤，回调风险上升"
                    ),
                    metadata={
                        "source": "cot_report",
                        "position_52w": position,
                        "latest_net": latest_net,
                        "signal_type": "crowded_long_warning",
                    },
                ))
            elif position > 0.80:
                signals.append(Signal(
                    name="COT聪明钱持仓偏高",
                    dimension="sentiment",
                    direction=SignalDirection.BULLISH,  # 仍偏多但需警惕
                    strength=SignalStrength.WEAK,
                    score=0.15,
                    description=(
                        f"非商业净多仓处于52周 {position:.0%} 分位，"
                        f"机构偏多但尚未极端"
                    ),
                    metadata={
                        "source": "cot_report",
                        "position_52w": position,
                    },
                ))
            elif position < 0.10:
                signals.append(Signal(
                    name="COT聪明钱极度悲观(机会)",
                    dimension="sentiment",
                    direction=SignalDirection.BULLISH,  # 极度悲观 = 反向机会
                    strength=SignalStrength.MODERATE,
                    score=0.4,
                    description=(
                        f"非商业净多仓处于52周低位 ({position:.0%}), "
                        f"机构极度悲观，可能形成反向买点"
                    ),
                    metadata={
                        "source": "cot_report",
                        "position_52w": position,
                        "latest_net": latest_net,
                        "signal_type": "extreme_pessimism",
                    },
                ))
            elif position < 0.20:
                signals.append(Signal(
                    name="COT聪明钱持仓偏低",
                    dimension="sentiment",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.WEAK,
                    score=-0.15,
                    description=(
                        f"非商业净多仓处于52周 {position:.0%} 分位，"
                        f"机构偏空"
                    ),
                    metadata={
                        "source": "cot_report",
                        "position_52w": position,
                    },
                ))

        except Exception as e:
            logger.debug(f"COT极端信号异常: {e}")

        return signals

    def _divergence_signals(self) -> list[Signal]:
        """背离信号 — 商业 vs 非-commercial 持仓背离."""
        signals: list[Signal] = []
        try:
            df = self.fetcher.fetch()
            if df.empty or len(df) < 2:
                return signals

            df = df.sort_values("timestamp")
            latest = df.iloc[-1]
            prev = df.iloc[-2]

            # 非商业净多仓减少 + 商业净空仓也减少 = 套保盘退缩
            # 说明生产商认为价格不会大跌，减少套保 → 看涨
            noncomm_net_latest = latest["close"]
            noncomm_net_prev = prev["close"]
            comm_net_latest = latest.get("comm_net", 0)
            comm_net_prev = prev.get("comm_net", 0)

            # 一致看涨: 聪明钱加仓 + 商业套保减少 (comm_net 增加)
            if noncomm_net_latest > noncomm_net_prev and comm_net_latest > comm_net_prev:
                signals.append(Signal(
                    name="COT一致看多信号",
                    dimension="sentiment",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.MODERATE,
                    score=0.3,
                    description="非商业加仓 + 商业套保减少，多空一致看多",
                    metadata={"source": "cot_report", "pattern": "aligned_bullish"},
                ))
            # 一致看跌: 聪明钱减仓 + 商业套保增加 (comm_net 减少)
            elif noncomm_net_latest < noncomm_net_prev and comm_net_latest < comm_net_prev:
                signals.append(Signal(
                    name="COT一致看空信号",
                    dimension="sentiment",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.MODERATE,
                    score=-0.3,
                    description="非商业减仓 + 商业套保增加，多空一致看空",
                    metadata={"source": "cot_report", "pattern": "aligned_bearish"},
                ))
            # 背离看涨: 聪明钱减仓但商业套保减少 (Producer 端偏乐观)
            elif noncomm_net_latest < noncomm_net_prev and comm_net_latest > comm_net_prev:
                signals.append(Signal(
                    name="COT持仓背离: 商业减套保",
                    dimension="sentiment",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.WEAK,
                    score=0.15,
                    description="聪明钱减仓但商业套保减少，Producer 端偏乐观",
                    metadata={"source": "cot_report", "pattern": "divergence_bullish"},
                ))
            # 背离看跌: 聪明钱加仓但商业套保增加
            elif noncomm_net_latest > noncomm_net_prev and comm_net_latest < comm_net_prev:
                signals.append(Signal(
                    name="COT持仓背离: 商业加套保",
                    dimension="sentiment",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.WEAK,
                    score=-0.15,
                    description="聪明钱加仓但商业套保增加，Producer 端偏悲观",
                    metadata={"source": "cot_report", "pattern": "divergence_bearish"},
                ))

        except Exception as e:
            logger.debug(f"COT背离信号异常: {e}")

        return signals
