"""基本面信号 — 美元指数、利率、通胀预期."""

import pandas as pd
from loguru import logger

from gold_miner.signals.base import Signal, SignalDirection, SignalStrength


class FundamentalAnalyzer:
    """基本面分析器."""

    def __init__(
        self,
        gold_df: pd.DataFrame | None = None,
        dxy_df: pd.DataFrame | None = None,
        rate_df: pd.DataFrame | None = None,
        silver_df: pd.DataFrame | None = None,
        breakeven_df: pd.DataFrame | None = None,
    ) -> None:
        self.gold = gold_df
        self.dxy = dxy_df
        self.rate = rate_df
        self.silver = silver_df
        self.breakeven = breakeven_df

    def analyze_dxy(self) -> list[Signal]:
        """分析美元指数对黄金的影响.

        美元走弱 → 黄金走强 (负相关)
        判断逻辑: DXY 5日均线 vs 20日均线
        """
        signals: list[Signal] = []
        if self.dxy is None or self.dxy.empty or len(self.dxy) < 20:
            return signals

        try:
            df = self.dxy.sort_values("timestamp").reset_index(drop=True)
            ma5 = df["value"].tail(5).mean()
            ma20 = df["value"].tail(20).mean()
            latest = df["value"].iloc[-1]

            if ma5 < ma20 * 0.995:  # 美元短期走弱
                score = min((ma20 - ma5) / ma20 * 10, 1.0)
                signals.append(
                    Signal(
                        name="美元指数走弱",
                        dimension="fundamental",
                        direction=SignalDirection.BULLISH,
                        strength=SignalStrength.MODERATE,
                        score=score,
                        description=f"DXY MA5({ma5:.2f}) < MA20({ma20:.2f})，利好黄金",
                    )
                )
            elif ma5 > ma20 * 1.005:  # 美元短期走强
                score = -min((ma5 - ma20) / ma20 * 10, 1.0)
                signals.append(
                    Signal(
                        name="美元指数走强",
                        dimension="fundamental",
                        direction=SignalDirection.BEARISH,
                        strength=SignalStrength.MODERATE,
                        score=score,
                        description=f"DXY MA5({ma5:.2f}) > MA20({ma20:.2f})，利空黄金",
                    )
                )
        except Exception as e:
            logger.warning(f"DXY分析失败: {e}")

        return signals

    def analyze_rates(self) -> list[Signal]:
        """分析实际利率对黄金的影响.

        实际利率下降 → 持有黄金机会成本降低 → 黄金走强 (强负相关)
        """
        signals: list[Signal] = []
        if self.rate is None or self.rate.empty or len(self.rate) < 20:
            return signals

        try:
            df = self.rate.sort_values("timestamp").reset_index(drop=True)
            ma5 = df["value"].tail(5).mean()
            ma20 = df["value"].tail(20).mean()
            latest = df["value"].iloc[-1]

            # 实际利率趋势
            if ma5 < ma20 * 0.995:
                score = min((ma20 - ma5) / abs(ma20) * 15, 1.0) if ma20 != 0 else 0.5
                signals.append(Signal(
                    name="实际利率下降",
                    dimension="fundamental",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.MODERATE if score > 0.4 else SignalStrength.WEAK,
                    score=score,
                    description=f"10Y实际利率 {latest:.2f}%, MA5 < MA20，利好黄金",
                ))
            elif ma5 > ma20 * 1.005:
                score = -min((ma5 - ma20) / abs(ma20) * 15, 1.0) if ma20 != 0 else -0.5
                signals.append(Signal(
                    name="实际利率上升",
                    dimension="fundamental",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.MODERATE if abs(score) > 0.4 else SignalStrength.WEAK,
                    score=score,
                    description=f"10Y实际利率 {latest:.2f}%, MA5 > MA20，利空黄金",
                ))

            # 实际利率为负 → 强烈利好
            if latest < 0:
                signals.append(Signal(
                    name="实际利率为负",
                    dimension="fundamental",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.STRONG,
                    score=0.7,
                    description=f"10Y实际利率为负 ({latest:.2f}%)，黄金极具吸引力",
                ))
        except Exception as e:
            logger.warning(f"利率分析失败: {e}")

        return signals

    def analyze_gold_silver_ratio(self) -> list[Signal]:
        """分析金银比对黄金的影响.

        金银比是经典的市场情绪指标:
        - 极高位 (>85): 市场恐慌，避险需求极端 → 短期看涨黄金
        - 极低位 (<60): 风险偏好极高 → 看跌黄金
        - 趋势上行: 避险升温 → 看涨
        """
        signals: list[Signal] = []
        if (self.gold is None or self.gold.empty or
                self.silver is None or self.silver.empty):
            return signals

        try:
            gold_price = self.gold["close"].iloc[-1]
            silver_price = self.silver["value"].iloc[-1]
            if silver_price <= 0:
                return signals

            ratio = gold_price / silver_price

            if ratio >= 85:
                score = min((ratio - 85) / 30, 1.0)
                signals.append(Signal(
                    name="金银比极高位",
                    dimension="fundamental",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.STRONG if ratio > 100 else SignalStrength.MODERATE,
                    score=score,
                    description=f"金银比 {ratio:.1f} > 85，避险情绪极端，利好黄金",
                ))
            elif ratio <= 60:
                score = -min((60 - ratio) / 20, 1.0)
                signals.append(Signal(
                    name="金银比低位",
                    dimension="fundamental",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.MODERATE,
                    score=score,
                    description=f"金银比 {ratio:.1f} < 60，风险偏好高，黄金吸引力下降",
                ))

            # 趋势: 近5日均值 vs 近20日均值
            if (self.gold is not None and len(self.gold) >= 5 and
                    self.silver is not None and len(self.silver) >= 5):
                gold_recent = self.gold["close"].tail(5).mean()
                silver_recent = self.silver["value"].tail(5).mean()
                ratio_recent = gold_recent / silver_recent if silver_recent > 0 else 0

                gold_prev = self.gold["close"].tail(20).head(15).mean()
                silver_prev = self.silver["value"].tail(20).head(15).mean()
                ratio_prev = gold_prev / silver_prev if silver_prev > 0 else 0

                if ratio_recent > ratio_prev * 1.03:
                    signals.append(Signal(
                        name="金银比趋势上行",
                        dimension="fundamental",
                        direction=SignalDirection.BULLISH,
                        strength=SignalStrength.WEAK,
                        score=0.2,
                        description=f"金银比近期 {ratio_recent:.1f} > 前期 {ratio_prev:.1f}，避险升温",
                    ))
        except Exception as e:
            logger.warning(f"金银比分析失败: {e}")

        return signals

    def analyze_inflation(self) -> list[Signal]:
        """分析通胀预期对黄金的影响.

        盈亏平衡通胀率上升 → 市场预期通胀升温 → 黄金保值需求增加 → 利好
        """
        signals: list[Signal] = []
        if self.breakeven is None or self.breakeven.empty or len(self.breakeven) < 20:
            return signals

        try:
            df = self.breakeven.sort_values("timestamp").reset_index(drop=True)
            ma5 = df["value"].tail(5).mean()
            ma20 = df["value"].tail(20).mean()
            latest = df["value"].iloc[-1]

            if ma5 > ma20 * 1.003:
                score = min((ma5 - ma20) / ma20 * 20, 1.0)
                signals.append(Signal(
                    name="通胀预期升温",
                    dimension="fundamental",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.MODERATE if score > 0.3 else SignalStrength.WEAK,
                    score=score,
                    description=f"盈亏平衡通胀率 {latest:.2f}%, MA5 > MA20，通胀预期上行利好黄金",
                ))
            elif ma5 < ma20 * 0.997:
                score = -min((ma20 - ma5) / ma20 * 20, 1.0)
                signals.append(Signal(
                    name="通胀预期回落",
                    dimension="fundamental",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.MODERATE if abs(score) > 0.3 else SignalStrength.WEAK,
                    score=score,
                    description=f"盈亏平衡通胀率 {latest:.2f}%, MA5 < MA20，通胀预期下行利空黄金",
                ))

            # 个人设置阈值
            if latest > 2.5:
                signals.append(Signal(
                    name="通胀预期高位",
                    dimension="fundamental",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.MODERATE,
                    score=0.4,
                    description=f"盈亏平衡通胀率 {latest:.2f}% > 2.5%，通胀担忧支撑金价",
                ))
        except Exception as e:
            logger.warning(f"通胀分析失败: {e}")

        return signals

    def analyze_central_bank(self) -> list[Signal]:
        """分析全球央行购金数据.

        央行持续购金 → 结构性利好（最可靠的长期看涨信号之一）
        数据来源: 世界黄金协会 (WGC) Gold Demand Trends
        """
        signals: list[Signal] = []
        try:
            from gold_miner.data.central_bank import CentralBankFetcher

            data = CentralBankFetcher().fetch()
            if data is None or data.net_purchases_tonnes <= 0:
                return signals

            # 央行购金 > 100吨/季度 → 强烈看涨
            if data.net_purchases_tonnes >= 200:
                signals.append(Signal(
                    name="央行大规模购金",
                    dimension="fundamental",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.STRONG,
                    score=0.8,
                    description=(
                        f"{data.quarter} 央行净购金 {data.net_purchases_tonnes:.0f}吨"
                        f"{' (同比' + f'{data.yoy_change_pct:+.0%})' if data.yoy_change_pct else ''}"
                        f"，结构性利好黄金"
                    ),
                ))
            elif data.net_purchases_tonnes >= 100:
                signals.append(Signal(
                    name="央行持续购金",
                    dimension="fundamental",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.MODERATE,
                    score=0.5,
                    description=(
                        f"{data.quarter} 央行净购金 {data.net_purchases_tonnes:.0f}吨，"
                        f"持续支撑金价"
                    ),
                ))
            else:
                signals.append(Signal(
                    name="央行购金放缓",
                    dimension="fundamental",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.WEAK,
                    score=0.15,
                    description=(
                        f"{data.quarter} 央行净购金 {data.net_purchases_tonnes:.0f}吨，"
                        f"仍在购买但规模较小"
                    ),
                ))

            # 购金占全球需求比例大 → 结构性支撑
            if data.total_demand_tonnes and data.total_demand_tonnes > 0:
                cb_share = data.net_purchases_tonnes / data.total_demand_tonnes
                if cb_share > 0.15:
                    signals.append(Signal(
                        name="央行购金占比高",
                        dimension="fundamental",
                        direction=SignalDirection.BULLISH,
                        strength=SignalStrength.MODERATE,
                        score=0.4,
                        description=(
                            f"央行购金占全球需求 {cb_share:.0%}，"
                            f"结构性需求强劲"
                        ),
                    ))
        except Exception as e:
            logger.warning(f"央行购金数据分析失败: {e}")

        return signals

    def generate_signals(self) -> list[Signal]:
        """生成所有基本面信号."""
        signals: list[Signal] = []
        signals.extend(self.analyze_dxy())
        signals.extend(self.analyze_rates())
        signals.extend(self.analyze_gold_silver_ratio())
        signals.extend(self.analyze_inflation())
        signals.extend(self.analyze_central_bank())
        return signals
