"""聪明钱综合信号 — 整合13F/COT/投行/大户多维度机构资金流向.

覆盖维度:
1. 13F机构持仓 (季度) — 桥水/索罗斯/巴菲特等顶级基金黄金仓位
2. 投行目标价共识 — 主流投行金价预测的一致性与离散度
3. COT聪明钱 (已独立实现) — 期货非商业持仓
4. 国际ETF资金流 (已独立实现) — GLD/IAU等ETF日频流入
5. COMEX大户集中度 (新增) — 大户多空拥挤度与逼空风险

信号权重分配:
- COT趋势: 0.25 (周频,反应最快)
- 国际ETF: 0.25 (日频,流动性最好)
- 投行共识: 0.20 (定性,方向指引)
- 大户集中度: 0.15 (结构风险预警)
- 13F持仓: 0.15 (季度,长期趋势)
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from gold_miner.data.comex_large_traders import ComexLargeTraderFetcher
from gold_miner.data.institutional_13f import Institutional13FFetcher
from gold_miner.data.investment_bank_targets import InvestmentBankTargetFetcher
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength


class InstitutionalSignalGenerator:
    """聪明钱综合信号生成器.

    整合多个机构资金流向维度，生成统一的聪明钱方向信号。
    """

    # 权重配置
    WEIGHTS = {
        "cot": 0.25,
        "intl_etf": 0.25,
        "bank_targets": 0.20,
        "large_traders": 0.15,
        "13f": 0.15,
    }

    def __init__(self, current_spot: float = 3300) -> None:
        self.current_spot = current_spot
        self.bank_fetcher = InvestmentBankTargetFetcher()
        self.trader_fetcher = ComexLargeTraderFetcher()
        self.inst_13f_fetcher = Institutional13FFetcher()

    def generate_signals(self) -> list[Signal]:
        """生成所有聪明钱维度信号."""
        signals: list[Signal] = []
        signals.extend(self._bank_target_signals())
        signals.extend(self._large_trader_signals())
        signals.extend(self._institutional_13f_signals())
        signals.extend(self._composite_smart_money_signal())
        return signals

    # ------------------------------------------------------------------
    # 投行目标价信号
    # ------------------------------------------------------------------

    def _bank_target_signals(self) -> list[Signal]:
        """投行目标价共识信号."""
        signals: list[Signal] = []
        try:
            consensus = self.bank_fetcher.fetch_consensus(self.current_spot)
            if consensus.get("status") != "ok":
                return signals

            upside = consensus.get("upside_pct", 0)
            bullish = consensus.get("bullish_count", 0)
            bearish = consensus.get("bearish_count", 0)
            neutral = consensus.get("neutral_count", 0)
            total = consensus.get("total_banks", 0)
            avg_target = consensus.get("avg_target", 0)

            if total == 0:
                return signals

            # 共识看涨信号
            bullish_ratio = bullish / total
            if bullish_ratio >= 0.6 and upside > 5:
                strength = SignalStrength.STRONG if bullish_ratio >= 0.7 else SignalStrength.MODERATE
                signals.append(Signal(
                    name="投行共识强烈看涨",
                    dimension="sentiment",
                    direction=SignalDirection.BULLISH,
                    strength=strength,
                    score=min(upside / 20, 0.8),
                    description=(
                        f"{bullish}/{total}家投行看涨，"
                        f"平均目标价${avg_target:,.0f}，"
                        f"上涨空间{upside:.1f}%"
                    ),
                    metadata={
                        "source": "bank_targets",
                        "bullish_ratio": bullish_ratio,
                        "upside_pct": upside,
                        "avg_target": avg_target,
                    },
                ))
            elif bullish_ratio >= 0.5 and upside > 0:
                signals.append(Signal(
                    name="投行共识偏多",
                    dimension="sentiment",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.WEAK,
                    score=upside / 30,
                    description=f"{bullish}/{total}家投行看多，平均目标价${avg_target:,.0f}",
                    metadata={"source": "bank_targets", "upside_pct": upside},
                ))
            elif bearish >= 3 and upside < -3:
                signals.append(Signal(
                    name="投行共识看空",
                    dimension="sentiment",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.MODERATE,
                    score=max(upside / 20, -0.6),
                    description=f"{bearish}家投行看空，平均目标价低于现价",
                    metadata={"source": "bank_targets", "upside_pct": upside},
                ))

            # 目标价离散度预警
            if total >= 5:
                high = consensus.get("highest_target", avg_target)
                low = consensus.get("lowest_target", avg_target)
                if avg_target > 0:
                    dispersion = (high - low) / avg_target
                    if dispersion > 0.3:
                        signals.append(Signal(
                            name="投行目标价分歧大",
                            dimension="sentiment",
                            direction=SignalDirection.NEUTRAL,
                            strength=SignalStrength.WEAK,
                            score=0.0,
                            description=(
                                f"投行目标价分歧显著: 最高${high:,.0f} vs 最低${low:,.0f}，"
                                f"方向不确定性高"
                            ),
                            metadata={"source": "bank_targets", "dispersion": dispersion},
                        ))

        except Exception as e:
            logger.debug(f"投行目标价信号异常: {e}")

        return signals

    # ------------------------------------------------------------------
    # COMEX大户集中度信号
    # ------------------------------------------------------------------

    def _large_trader_signals(self) -> list[Signal]:
        """COMEX大户集中度信号."""
        signals: list[Signal] = []
        try:
            summary = self.trader_fetcher.fetch_concentration_summary()
            if summary.get("status") != "ok":
                return signals

            long4 = summary.get("long4_concentration_pct", 0)
            short4 = summary.get("short4_concentration_pct", 0)
            dominance = summary.get("long_dominance", 0)
            crowded_short = summary.get("crowded_short", False)
            crowded_long = summary.get("crowded_long", False)
            squeeze_risk = summary.get("squeeze_risk", False)

            # 逼空机会 — 最强烈的信号
            if squeeze_risk:
                signals.append(Signal(
                    name="COMEX大户逼空风险",
                    dimension="sentiment",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.STRONG,
                    score=0.6,
                    description=(
                        f"前4大空头集中度{short4:.1f}%，"
                        f"大户净空拥挤，逼空风险显著"
                    ),
                    metadata={
                        "source": "comex_large_traders",
                        "short4": short4,
                        "pattern": "short_squeeze_risk",
                    },
                ))
            elif crowded_short and not squeeze_risk:
                signals.append(Signal(
                    name="COMEX大户空头拥挤",
                    dimension="sentiment",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.MODERATE,
                    score=0.3,
                    description=f"前4大空头集中度{short4:.1f}%，空头仓位拥挤",
                    metadata={"source": "comex_large_traders", "short4": short4},
                ))

            # 多头拥挤警告
            if crowded_long:
                signals.append(Signal(
                    name="COMEX大户多头拥挤(警告)",
                    dimension="sentiment",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.MODERATE,
                    score=-0.25,
                    description=f"前4大多头集中度{long4:.1f}%，多头过于拥挤",
                    metadata={"source": "comex_large_traders", "long4": long4},
                ))

            # 多空集中度变化趋势
            if dominance > 5:
                signals.append(Signal(
                    name="COMEX大户多头占优",
                    dimension="sentiment",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.WEAK,
                    score=0.15,
                    description=f"大户多头集中度领先空头{dominance:.1f}个百分点",
                    metadata={"source": "comex_large_traders", "dominance": dominance},
                ))
            elif dominance < -5:
                signals.append(Signal(
                    name="COMEX大户空头占优",
                    dimension="sentiment",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.WEAK,
                    score=-0.15,
                    description=f"大户空头集中度领先多头{abs(dominance):.1f}个百分点",
                    metadata={"source": "comex_large_traders", "dominance": dominance},
                ))

        except Exception as e:
            logger.debug(f"大户集中度信号异常: {e}")

        return signals

    # ------------------------------------------------------------------
    # 13F机构持仓信号
    # ------------------------------------------------------------------

    def _institutional_13f_signals(self) -> list[Signal]:
        """13F机构持仓信号."""
        signals: list[Signal] = []
        try:
            summary = self.inst_13f_fetcher.fetch_latest_quarter()
            if summary is None:
                return signals

            bullish = summary.net_gold_bullish
            bearish = summary.net_gold_bearish
            total = summary.total_institutions

            if total == 0:
                return signals

            ratio = bullish / total if total > 0 else 0.5

            if bullish >= 4 and ratio >= 0.6:
                signals.append(Signal(
                    name="13F机构大举增持黄金",
                    dimension="fundamental",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.STRONG,
                    score=0.5,
                    description=(
                        f"{bullish}家顶级机构增持黄金资产，"
                        f"仅{bearish}家减持"
                    ),
                    metadata={
                        "source": "13f_institutional",
                        "bullish": bullish,
                        "bearish": bearish,
                    },
                ))
            elif bullish > bearish:
                signals.append(Signal(
                    name="13F机构净增持黄金",
                    dimension="fundamental",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.WEAK,
                    score=0.15,
                    description=f"{bullish}家增持 vs {bearish}家减持，机构方向偏多",
                    metadata={"source": "13f_institutional"},
                ))
            elif bearish > bullish:
                signals.append(Signal(
                    name="13F机构净减持黄金",
                    dimension="fundamental",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.WEAK,
                    score=-0.1,
                    description=f"{bearish}家减持 vs {bullish}家增持，机构方向偏空",
                    metadata={"source": "13f_institutional"},
                ))

            # 特定机构信号
            for buyer in summary.top_buyers:
                if buyer.institution in {"Bridgewater Associates", "Berkshire Hathaway"}:
                    signals.append(Signal(
                        name=f"{buyer.institution[:15]}增持{buyer.ticker}",
                        dimension="fundamental",
                        direction=SignalDirection.BULLISH,
                        strength=SignalStrength.MODERATE,
                        score=0.2,
                        description=(
                            f"{buyer.institution} Q{buyer.quarter} "
                            f"增持{buyer.shares:,}股{buyer.ticker}"
                        ),
                        metadata={
                            "source": "13f_institutional",
                            "institution": buyer.institution,
                            "ticker": buyer.ticker,
                        },
                    ))

        except Exception as e:
            logger.debug(f"13F信号异常: {e}")

        return signals

    # ------------------------------------------------------------------
    # 聪明钱综合信号
    # ------------------------------------------------------------------

    def _composite_smart_money_signal(self) -> list[Signal]:
        """综合聪明钱信号 — 多维度加权汇总.

        当多个聪明钱维度方向一致时，生成综合信号。
        """
        signals: list[Signal] = []
        try:
            scores: dict[str, float] = {}

            # COT (复用已有的CotSignalGenerator逻辑)
            try:
                from gold_miner.data.cot_report import CotReportFetcher
                cot = CotReportFetcher().fetch_net_position()
                if cot.get("status") == "ok":
                    trend = cot.get("trend", "neutral")
                    position = cot.get("position_in_52w_range", 0.5)
                    if trend == "up":
                        scores["cot"] = 0.5 + position * 0.3
                    elif trend == "down":
                        scores["cot"] = -0.5 - (1 - position) * 0.3
                    else:
                        scores["cot"] = 0.0
            except Exception:
                scores["cot"] = 0.0

            # 国际ETF
            try:
                from gold_miner.data.etf_flow import IntlGoldEtfFlowFetcher
                etf = IntlGoldEtfFlowFetcher().fetch_flow_summary()
                if etf.get("status") == "ok":
                    scores["intl_etf"] = etf.get("flow_score", 0)
            except Exception:
                scores["intl_etf"] = 0.0

            # 投行目标价
            try:
                bank = self.bank_fetcher.get_bullish_score(self.current_spot)
                scores["bank_targets"] = bank
            except Exception:
                scores["bank_targets"] = 0.0

            # 大户集中度
            try:
                lt = self.trader_fetcher.fetch_concentration_summary()
                if lt.get("status") == "ok":
                    if lt.get("squeeze_risk", False):
                        scores["large_traders"] = 0.6
                    elif lt.get("crowded_short", False):
                        scores["large_traders"] = 0.3
                    elif lt.get("crowded_long", False):
                        scores["large_traders"] = -0.3
                    else:
                        scores["large_traders"] = lt.get("long_dominance", 0) / 50
            except Exception:
                scores["large_traders"] = 0.0

            # 13F
            try:
                inst = self.inst_13f_fetcher.get_bullish_score()
                scores["13f"] = inst
            except Exception:
                scores["13f"] = 0.0

            # 加权汇总
            total_score = 0.0
            total_weight = 0.0
            for key, weight in self.WEIGHTS.items():
                if key in scores:
                    total_score += scores[key] * weight
                    total_weight += weight

            if total_weight > 0:
                composite = total_score / total_weight
            else:
                composite = 0.0

            # 生成综合信号
            if abs(composite) >= 0.3:
                direction = (
                    SignalDirection.BULLISH if composite > 0
                    else SignalDirection.BEARISH
                )
                strength = (
                    SignalStrength.STRONG if abs(composite) >= 0.5
                    else SignalStrength.MODERATE
                )
                signals.append(Signal(
                    name="聪明钱综合信号",
                    dimension="sentiment",
                    direction=direction,
                    strength=strength,
                    score=round(composite, 2),
                    description=(
                        f"多维度聪明钱加权综合: {composite:+.2f} "
                        f"(COT:{scores.get('cot', 0):+.2f} "
                        f"ETF:{scores.get('intl_etf', 0):+.2f} "
                        f"投行:{scores.get('bank_targets', 0):+.2f} "
                        f"大户:{scores.get('large_traders', 0):+.2f} "
                        f"13F:{scores.get('13f', 0):+.2f})"
                    ),
                    metadata={
                        "source": "smart_money_composite",
                        "composite": round(composite, 3),
                        "components": {k: round(v, 3) for k, v in scores.items()},
                    },
                ))

        except Exception as e:
            logger.debug(f"聪明钱综合信号异常: {e}")

        return signals
