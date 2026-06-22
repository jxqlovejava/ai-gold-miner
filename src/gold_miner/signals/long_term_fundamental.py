"""中长期基本面信号 — 财政信用、实际利率、估值锚."""

from __future__ import annotations

import pandas as pd
from loguru import logger

from gold_miner.data.fiscal import FiscalDataFetcher
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength


class LongTermFundamentalSignal:
    """中长期结构性基本面信号生成器."""

    def __init__(self, fiscal_fetcher: FiscalDataFetcher | None = None) -> None:
        self.fiscal_fetcher = fiscal_fetcher or FiscalDataFetcher()

    def generate_signals(self, gold_history: pd.DataFrame | None = None) -> list[Signal]:
        """生成所有中长期基本面信号."""
        signals: list[Signal] = []
        signals.extend(self._fiscal_signals())
        signals.extend(self._valuation_signals(gold_history))
        signals.extend(self._composite_fundamental_signal(signals))
        return signals

    def _fiscal_signals(self) -> list[Signal]:
        """财政信用与货币体系信号."""
        signals: list[Signal] = []
        try:
            summary = self.fiscal_fetcher.fetch_trend_summary()
            if summary.get("status") != "ok":
                return signals

            debt_gdp = summary.get("debt_to_gdp_pct", 0)
            debt_yoy = summary.get("debt_yoy_change_pct", 0)
            real_rate = summary.get("real_rate_10y_pct", 0)
            dollar_share = summary.get("dollar_reserve_share_pct", 0)
            dollar_share_yoy = summary.get("dollar_share_yoy_change_pct", 0)

            # 债务/GDP 高位且持续上升 → 长期利好黄金
            if debt_gdp > 120 and debt_yoy > 3:
                signals.append(Signal(
                    name="美国债务/GDP高位运行",
                    dimension="long_term",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.STRONG,
                    score=0.5,
                    description=(
                        f"美国债务/GDP 达 {debt_gdp:.1f}%，"
                        f"同比升 {debt_yoy:.1f}%，货币信用风险支撑黄金长期配置"
                    ),
                    metadata={
                        "source": "fiscal",
                        "debt_to_gdp_pct": debt_gdp,
                        "debt_yoy_change_pct": debt_yoy,
                    },
                ))
            elif debt_gdp > 115:
                signals.append(Signal(
                    name="美国债务水平偏高",
                    dimension="long_term",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.MODERATE,
                    score=0.25,
                    description=f"美国债务/GDP 达 {debt_gdp:.1f}%，处于历史高位区间",
                    metadata={"source": "fiscal", "debt_to_gdp_pct": debt_gdp},
                ))

            # 实际利率低位/负值 → 利好黄金
            if real_rate < 0:
                signals.append(Signal(
                    name="实际利率为负",
                    dimension="long_term",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.STRONG,
                    score=0.5,
                    description=f"10年期TIPS实际利率为 {real_rate:.2f}%，持有黄金机会成本极低",
                    metadata={"source": "fiscal", "real_rate_10y_pct": real_rate},
                ))
            elif real_rate < 1.0:
                signals.append(Signal(
                    name="实际利率偏低",
                    dimension="long_term",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.WEAK,
                    score=0.15,
                    description=f"10年期TIPS实际利率为 {real_rate:.2f}%，对黄金压制有限",
                    metadata={"source": "fiscal", "real_rate_10y_pct": real_rate},
                ))
            elif real_rate > 2.5:
                signals.append(Signal(
                    name="实际利率偏高",
                    dimension="long_term",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.MODERATE,
                    score=-0.3,
                    description=f"10年期TIPS实际利率为 {real_rate:.2f}%，对黄金长期不利",
                    metadata={"source": "fiscal", "real_rate_10y_pct": real_rate},
                ))

            # 美元储备份额下降 → 去美元化利好黄金
            if dollar_share_yoy < -0.5:
                signals.append(Signal(
                    name="美元储备份额下降",
                    dimension="long_term",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.MODERATE,
                    score=0.3,
                    description=(
                        f"美元全球储备份额 {dollar_share:.1f}%，"
                        f"同比下降 {abs(dollar_share_yoy):.1f}%，央行储备多元化支撑黄金"
                    ),
                    metadata={
                        "source": "fiscal",
                        "dollar_reserve_share_pct": dollar_share,
                        "dollar_share_yoy_change_pct": dollar_share_yoy,
                    },
                ))

        except Exception as e:
            logger.debug(f"财政信号异常: {e}")

        return signals

    def _valuation_signals(self, gold_history: pd.DataFrame | None = None) -> list[Signal]:
        """黄金长期估值锚信号."""
        signals: list[Signal] = []
        if gold_history is None or gold_history.empty:
            return signals

        try:
            closes = gold_history["close"].dropna()
            if len(closes) < 60:
                return signals

            current = float(closes.iloc[-1])
            ma_200 = float(closes.tail(200).mean()) if len(closes) >= 200 else float(closes.mean())
            ma_60 = float(closes.tail(60).mean())

            # 价格相对 200 日均线位置
            vs_200ma = (current / ma_200 - 1) * 100 if ma_200 > 0 else 0
            if vs_200ma > 15:
                signals.append(Signal(
                    name="金价大幅高于200日均线",
                    dimension="long_term",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.WEAK,
                    score=-0.15,
                    description=f"金价较200日均线高 {vs_200ma:.1f}%，长期追高风险",
                    metadata={"source": "valuation", "vs_200ma_pct": vs_200ma},
                ))
            elif vs_200ma < -10:
                signals.append(Signal(
                    name="金价大幅低于200日均线",
                    dimension="long_term",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.MODERATE,
                    score=0.25,
                    description=f"金价较200日均线低 {abs(vs_200ma):.1f}%，长期均值回归机会",
                    metadata={"source": "valuation", "vs_200ma_pct": vs_200ma},
                ))

            # 60日均线与 200日均线关系
            if ma_60 > ma_200:
                signals.append(Signal(
                    name="长期均线多头排列",
                    dimension="long_term",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.WEAK,
                    score=0.15,
                    description="60日均线位于200日均线上方，中期趋势向上",
                    metadata={"source": "valuation", "ma60": ma_60, "ma200": ma_200},
                ))
            elif ma_60 < ma_200:
                signals.append(Signal(
                    name="长期均线空头排列",
                    dimension="long_term",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.WEAK,
                    score=-0.15,
                    description="60日均线位于200日均线下方，中期趋势向下",
                    metadata={"source": "valuation", "ma60": ma_60, "ma200": ma_200},
                ))

        except Exception as e:
            logger.debug(f"估值信号异常: {e}")

        return signals

    def _composite_fundamental_signal(self, signals: list[Signal]) -> list[Signal]:
        """结构性基本面综合信号."""
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
            name="结构性基本面综合",
            dimension="long_term",
            direction=direction,
            strength=strength,
            score=round(composite, 2),
            description=(
                f"财政/利率/估值综合: {composite:+.2f} "
                f"(看多{len(bullish)}项 / 看空{len(bearish)}项)"
            ),
            metadata={
                "source": "long_term_fundamental_composite",
                "composite": round(composite, 3),
                "bullish_count": len(bullish),
                "bearish_count": len(bearish),
            },
        )]
