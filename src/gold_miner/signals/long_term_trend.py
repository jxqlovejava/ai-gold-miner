"""中长期趋势信号 — 央行购金、国际 ETF、COT 持仓的 12M/24M 趋势."""

from __future__ import annotations

from loguru import logger

from gold_miner.data.central_bank import CentralBankHistoryFetcher
from gold_miner.data.cot_report import CotReportFetcher
from gold_miner.data.etf_flow import IntlGoldEtfFlowFetcher
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength


class LongTermTrendSignal:
    """中长期机构资金流向趋势信号生成器."""

    def __init__(
        self,
        cb_fetcher: CentralBankHistoryFetcher | None = None,
        etf_fetcher: IntlGoldEtfFlowFetcher | None = None,
        cot_fetcher: CotReportFetcher | None = None,
    ) -> None:
        self.cb_fetcher = cb_fetcher or CentralBankHistoryFetcher()
        self.etf_fetcher = etf_fetcher or IntlGoldEtfFlowFetcher()
        self.cot_fetcher = cot_fetcher or CotReportFetcher()

    def generate_signals(self) -> list[Signal]:
        """生成所有中长期趋势信号."""
        signals: list[Signal] = []
        signals.extend(self._central_bank_signals())
        signals.extend(self._etf_signals())
        signals.extend(self._cot_signals())
        signals.extend(self._composite_trend_signal(signals))
        return signals

    def _central_bank_signals(self) -> list[Signal]:
        """央行购金趋势信号."""
        signals: list[Signal] = []
        try:
            trend = self.cb_fetcher.fetch_rolling_trend(quarters=8)
            if trend.get("status") != "ok":
                return signals

            avg = trend.get("avg_quarterly_tonnes", 0)
            yoy = trend.get("avg_yoy_change_pct", 0)
            trend_dir = trend.get("trend", "neutral")

            if trend_dir == "strong_buying":
                signals.append(Signal(
                    name="央行购金强劲",
                    dimension="long_term",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.STRONG,
                    score=0.6,
                    description=(
                        f"近8个季度央行平均季度购金 {avg:.0f}t，"
                        f"同比 {yoy:+.1f}%，结构性买盘持续"
                    ),
                    metadata={
                        "source": "central_bank",
                        "avg_quarterly_tonnes": avg,
                        "avg_yoy_change_pct": yoy,
                        "trend": trend_dir,
                    },
                ))
            elif trend_dir == "buying":
                signals.append(Signal(
                    name="央行购金稳健",
                    dimension="long_term",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.MODERATE,
                    score=0.35,
                    description=(
                        f"近8个季度央行平均季度购金 {avg:.0f}t，"
                        f"维持净买入"
                    ),
                    metadata={
                        "source": "central_bank",
                        "avg_quarterly_tonnes": avg,
                        "trend": trend_dir,
                    },
                ))
            elif trend_dir == "selling":
                signals.append(Signal(
                    name="央行购金放缓",
                    dimension="long_term",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.WEAK,
                    score=-0.2,
                    description="近8个季度央行购金放缓或转为净卖出",
                    metadata={
                        "source": "central_bank",
                        "avg_quarterly_tonnes": avg,
                        "trend": trend_dir,
                    },
                ))

        except Exception as e:
            logger.debug(f"央行趋势信号异常: {e}")

        return signals

    def _etf_signals(self) -> list[Signal]:
        """国际 ETF 持仓趋势信号."""
        signals: list[Signal] = []
        try:
            summary = self.etf_fetcher.fetch_flow_summary()
            if summary.get("status") != "ok":
                return signals

            score = summary.get("flow_score", 0)
            direction = summary.get("flow_direction", "neutral")
            gld_change = summary.get("gld_change_pct", 0)

            if direction == "strong_inflow" and score > 0.5:
                signals.append(Signal(
                    name="国际黄金ETF大幅流入",
                    dimension="long_term",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.STRONG,
                    score=min(score, 0.6),
                    description=f"国际黄金ETF资金大幅流入，GLD日涨 {gld_change:+.2f}%",
                    metadata={
                        "source": "intl_etf",
                        "flow_direction": direction,
                        "flow_score": score,
                        "gld_change_pct": gld_change,
                    },
                ))
            elif direction == "inflow" and score > 0.15:
                signals.append(Signal(
                    name="国际黄金ETF净流入",
                    dimension="long_term",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.MODERATE,
                    score=min(score, 0.35),
                    description=f"国际黄金ETF资金净流入，GLD日涨 {gld_change:+.2f}%",
                    metadata={
                        "source": "intl_etf",
                        "flow_direction": direction,
                        "flow_score": score,
                    },
                ))
            elif direction == "strong_outflow" and score < -0.5:
                signals.append(Signal(
                    name="国际黄金ETF大幅流出",
                    dimension="long_term",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.MODERATE,
                    score=max(score, -0.5),
                    description=f"国际黄金ETF资金大幅流出，GLD日涨 {gld_change:+.2f}%",
                    metadata={
                        "source": "intl_etf",
                        "flow_direction": direction,
                        "flow_score": score,
                    },
                ))
            elif direction == "outflow" and score < -0.15:
                signals.append(Signal(
                    name="国际黄金ETF净流出",
                    dimension="long_term",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.WEAK,
                    score=max(score, -0.25),
                    description="国际黄金ETF资金净流出",
                    metadata={
                        "source": "intl_etf",
                        "flow_direction": direction,
                        "flow_score": score,
                    },
                ))

        except Exception as e:
            logger.debug(f"ETF趋势信号异常: {e}")

        return signals

    def _cot_signals(self) -> list[Signal]:
        """COT 持仓长期趋势信号."""
        signals: list[Signal] = []
        try:
            summary = self.cot_fetcher.fetch_net_position(weeks=12)
            if summary.get("status") != "ok":
                return signals

            position = summary.get("position_in_52w_range", 0.5)
            trend = summary.get("trend", "neutral")
            pct_change = summary.get("pct_change", 0)

            # 长期趋势
            if trend == "up" and pct_change > 5:
                signals.append(Signal(
                    name="COT聪明钱长期加仓",
                    dimension="long_term",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.MODERATE,
                    score=min(pct_change / 15, 0.4),
                    description=f"非商业净多仓12周趋势上行，变化 {pct_change:+.1f}%",
                    metadata={
                        "source": "cot",
                        "trend": trend,
                        "pct_change": pct_change,
                        "position_in_52w_range": position,
                    },
                ))
            elif trend == "down" and pct_change < -5:
                signals.append(Signal(
                    name="COT聪明钱长期减仓",
                    dimension="long_term",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.MODERATE,
                    score=max(pct_change / 15, -0.4),
                    description=f"非商业净多仓12周趋势下行，变化 {pct_change:+.1f}%",
                    metadata={
                        "source": "cot",
                        "trend": trend,
                        "pct_change": pct_change,
                        "position_in_52w_range": position,
                    },
                ))

            # 极端持仓长期警示
            if position > 0.85:
                signals.append(Signal(
                    name="COT持仓长期拥挤(警示)",
                    dimension="long_term",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.WEAK,
                    score=-0.2,
                    description=f"非商业净多仓处于52周 {position:.0%} 高位，长期回调风险累积",
                    metadata={
                        "source": "cot",
                        "position_in_52w_range": position,
                    },
                ))
            elif position < 0.15:
                signals.append(Signal(
                    name="COT持仓长期悲观(机会)",
                    dimension="long_term",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.MODERATE,
                    score=0.3,
                    description=f"非商业净多仓处于52周 {position:.0%} 低位，长期反向机会",
                    metadata={
                        "source": "cot",
                        "position_in_52w_range": position,
                    },
                ))

        except Exception as e:
            logger.debug(f"COT趋势信号异常: {e}")

        return signals

    def _composite_trend_signal(self, signals: list[Signal]) -> list[Signal]:
        """机构资金综合趋势信号."""
        long_term_signals = [s for s in signals if s.dimension == "long_term"]
        if len(long_term_signals) < 2:
            return []

        bullish = [s for s in long_term_signals if s.direction == SignalDirection.BULLISH]
        bearish = [s for s in long_term_signals if s.direction == SignalDirection.BEARISH]

        total_score = sum(s.score for s in long_term_signals)
        total_weight = sum(abs(s.score) for s in long_term_signals)
        composite = total_score / total_weight if total_weight > 0 else 0.0

        if abs(composite) < 0.15:
            return []

        direction = SignalDirection.BULLISH if composite > 0 else SignalDirection.BEARISH
        strength = SignalStrength.STRONG if abs(composite) >= 0.4 else SignalStrength.MODERATE

        return [Signal(
            name="机构资金长期趋势综合",
            dimension="long_term",
            direction=direction,
            strength=strength,
            score=round(composite, 2),
            description=(
                f"央行/ETF/COT 长期趋势综合: {composite:+.2f} "
                f"(看多{len(bullish)}项 / 看空{len(bearish)}项)"
            ),
            metadata={
                "source": "long_term_trend_composite",
                "composite": round(composite, 3),
                "bullish_count": len(bullish),
                "bearish_count": len(bearish),
            },
        )]
