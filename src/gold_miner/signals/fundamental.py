"""基本面信号 — 美元指数、利率、通胀预期."""
from __future__ import annotations

import pandas as pd
from loguru import logger

from gold_miner.signals.base import Signal, SignalDirection, SignalStrength


class FundamentalAnalyzer:
    """基本面分析器."""

    SOURCE_TIER = "T0"  # 数据源: FRED 美联储官方一手数据

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
        """分析 ICE 美元指数 (DXY) 对黄金的影响.

        美元走弱 → 黄金走强 (负相关)
        判断逻辑: DXY(ICE) 5日均线 vs 20日均线

        输入应为 ICE Dollar Index（水平约 100），而非 FRED 贸易加权美元指数
        DTWEXBGS（水平约 120）。
        """
        signals: list[Signal] = []
        if self.dxy is None or self.dxy.empty or len(self.dxy) < 20:
            return signals

        try:
            df = self.dxy.sort_values("timestamp").reset_index(drop=True)
            ma5 = df["value"].tail(5).mean()
            ma20 = df["value"].tail(20).mean()
            df["value"].iloc[-1]

            if ma5 < ma20 * 0.995:  # 美元短期走弱
                score = min((ma20 - ma5) / ma20 * 10, 1.0)
                signals.append(
                    Signal(
                        name="美元指数走弱",
                        dimension="fundamental",
                        direction=SignalDirection.BULLISH,
                        strength=SignalStrength.MODERATE,
                        score=score,
                        description=f"DXY(ICE) MA5({ma5:.2f}) < MA20({ma20:.2f})，利好黄金",
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
                        description=f"DXY(ICE) MA5({ma5:.2f}) > MA20({ma20:.2f})，利空黄金",
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
        """分析全球央行购金数据 — 季度+月度综合.

        央行持续购金 → 结构性利好（最可靠的长期看涨信号之一）
        数据来源: WGC季度报告 + 重点国别月度监控
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

        # 月度重点国别央行购金监控
        signals.extend(self._analyze_monthly_central_bank())

        return self._merge_central_bank_family(signals)

    @staticmethod
    def _merge_central_bank_family(signals: list[Signal]) -> list[Signal]:
        """合并央行购金信号族，消除同一底层事实的重复加权.

        族内信号数据嵌套包含：中国月度 ⊂ 重点国别月度合计 ⊂ WGC季度总量，
        占比信号与季度总量同分子。同向叠发会把「央行在买金」这一件事计分
        多次（2026-09-03 本期 +0.8/+0.4/+0.35/+0.3 合计 +1.85，占基本面
        看多力量 75%），虚增维度均分与看多计数。

        规则（参照 COT 方案A, cot_signal.py）：
        - 同向信号合并为一条主信号（取 |score| 最强档），
          score = 主分 ± 0.1×(确认数-1)，绝对值封顶 1.0；
        - 子信号明细并入主信号描述（信息不丢），不单独发信号；
        - 方向冲突（如季度 bullish + 月度 selling）不合并，双方独立保留
          —— 月度恶化 vs 季度结构性水平携带独立信息。
        """
        if len(signals) <= 1:
            return signals

        merged: list[Signal] = []
        for direction in (SignalDirection.BULLISH, SignalDirection.BEARISH):
            group = [s for s in signals if s.direction == direction]
            if not group:
                continue
            if len(group) == 1 or group[0].score == 0:
                merged.extend(group)
                continue
            primary = max(group, key=lambda s: abs(s.score))
            bonus = 0.1 * (len(group) - 1)
            new_score = (
                min(primary.score + bonus, 1.0)
                if primary.score > 0
                else max(primary.score - bonus, -1.0)
            )
            confirmations = [s for s in group if s is not primary]
            details = "；".join(s.description for s in confirmations if s.description)
            merged.append(Signal(
                name=primary.name,
                dimension=primary.dimension,
                direction=primary.direction,
                strength=primary.strength,
                score=round(new_score, 2),
                description=(
                    f"{primary.description}；同族确认: {details}"
                    if details else primary.description
                ),
                metadata={
                    **primary.metadata,
                    "family": "central_bank",
                    "family_merged": [s.name for s in confirmations],
                    "family_confirmations": len(confirmations),
                },
            ))
        merged.extend(s for s in signals if s.direction == SignalDirection.NEUTRAL)
        return merged

    def _analyze_monthly_central_bank(self) -> list[Signal]:
        """分析重点国别央行月度购金数据.

        补充季度WGC数据，提供更及时的央行购金信号。
        """
        signals: list[Signal] = []
        try:
            from gold_miner.data.central_bank import MonthlyCentralBankFetcher

            fetcher = MonthlyCentralBankFetcher()
            summary = fetcher.fetch_summary()
            if summary.get("status") != "ok":
                return signals

            total = summary.get("total_monthly_tonnes", 0)
            trend = summary.get("trend", "neutral")
            top_buyer = summary.get("top_buyer", {})
            significant_count = summary.get("significant_countries", 0)

            if trend == "strong_buying":
                signals.append(Signal(
                    name="重点央行月度大举购金",
                    dimension="fundamental",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.STRONG,
                    score=0.6,
                    description=(
                        f"重点央行月度合计购金{total:.0f}吨，"
                        f"{significant_count}国显著增持，"
                        f"{top_buyer.get('country', 'unknown')}领先({top_buyer.get('purchases', 0):.0f}t)"
                    ),
                    metadata={
                        "source": "monthly_cb",
                        "total_monthly": total,
                        "trend": trend,
                    },
                ))
            elif trend == "buying":
                signals.append(Signal(
                    name="重点央行月度持续购金",
                    dimension="fundamental",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.MODERATE,
                    score=0.35,
                    description=(
                        f"重点央行月度合计购金{total:.0f}吨，"
                        f"持续增持黄金储备"
                    ),
                    metadata={"source": "monthly_cb", "total_monthly": total},
                ))
            elif trend == "selling":
                signals.append(Signal(
                    name="重点央行月度净卖出",
                    dimension="fundamental",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.WEAK,
                    score=-0.15,
                    description=f"重点央行月度净卖出{abs(total):.0f}吨",
                    metadata={"source": "monthly_cb", "total_monthly": total},
                ))

            # 中国央行专项信号 (最重要单一变量)
            china_data = fetcher.fetch_china_pboc()
            if china_data and china_data.is_significant:
                signals.append(Signal(
                    name="中国央行加大购金",
                    dimension="fundamental",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.MODERATE,
                    score=0.3,
                    description=(
                        f"中国央行{china_data.date_label}购金"
                        f"{china_data.net_purchases_tonnes:.0f}吨，"
                        f"连续增持信号"
                    ),
                    metadata={
                        "source": "pboc_monthly",
                        "china_purchases": china_data.net_purchases_tonnes,
                    },
                ))

        except Exception as e:
            logger.debug(f"月度央行购金分析失败: {e}")

        return signals

    def generate_signals(self) -> list[Signal]:
        """生成所有基本面信号."""
        signals: list[Signal] = []
        signals.extend(self.analyze_dxy())
        signals.extend(self.analyze_rates())
        signals.extend(self.analyze_gold_silver_ratio())
        signals.extend(self.analyze_inflation())
        signals.extend(self.analyze_central_bank())
        signals.extend(self._analyze_election_cycle())
        signals.extend(self._analyze_india_gold_demand())
        for s in signals:
            s.metadata.setdefault("source_tier", self.SOURCE_TIER)
        return signals

    # ------------------------------------------------------------------
    # 美国中期选举周期信号
    # ------------------------------------------------------------------

    @staticmethod
    def _analyze_election_cycle() -> list[Signal]:
        """分析美国中期选举周期对黄金的影响.

        基于 14 个中期选举年（~56 年）的历史数据:
        - 中期选举年金价平均上涨 +12.83%（vs 总统年 +3.47%、选后年 +5.11%）
        - 最佳窗口: 7月4日-9月6日，平均 +7%，成功率 71%+
        - VIX 在中期年平均 +20%，75% 年份上涨
        - 选举前 6 个月平均黄金回报 +5.8%

        当前 (2026-07-22) 距离 2026-11-03 中期选举约 3.5 个月，
        处于历史上黄金表现最强的中期选举窗口内。

        Source: Seasonax.com / Resource Capital / SSGA (2026)
        """
        from datetime import date

        signals: list[Signal] = []
        today = date.today()

        # 中期选举日期: 2026年11月第一个星期二 = 2026-11-03
        election_date = date(2026, 11, 3)

        # 仅在中选年份生成信号
        if today.year != 2026:
            return signals

        months_to_election = (
            (election_date.year - today.year) * 12
            + (election_date.month - today.month)
        )

        # 只在选举前 6 个月内生成信号（选举年 5 月起）
        if months_to_election > 6 or months_to_election < 0:
            return signals

        # 最佳窗口: 7/4 - 9/6（当前正处此窗口）
        in_optimal_window = (
            today >= date(2026, 7, 4)
            and today <= date(2026, 9, 6)
        )

        if in_optimal_window:
            # 最优窗口: 历史上平均+7%，成功率71%+
            signals.append(Signal(
                name="美国中期选举最优窗口",
                dimension="fundamental",
                direction=SignalDirection.BULLISH,
                strength=SignalStrength.MODERATE,
                score=0.25,
                description=(
                    "美国中期选举最佳黄金窗口(7/4-9/6)已激活——"
                    "14个中期年中平均+7%，成功率71%+。"
                    "政策不确定性+VIX历史上升→避险买盘。"
                    f"距离选举约{months_to_election}个月"
                ),
                metadata={
                    "source": "election_cycle",
                    "source_tier": "T2",
                    "historical_avg_return_pct": 7.0,
                    "success_rate_pct": 71,
                    "months_to_election": months_to_election,
                },
            ))
        else:
            # 选举年非最优窗口但仍在前6月内
            score = 0.15 if months_to_election <= 3 else 0.10
            signals.append(Signal(
                name="美国中期选举政策不确定性",
                dimension="fundamental",
                direction=SignalDirection.BULLISH,
                strength=SignalStrength.WEAK,
                score=score,
                description=(
                    f"美国2026中期选举临近(约{months_to_election}个月)，"
                    "政策不确定性为黄金提供温和避险支撑。"
                    "历史中期年金价平均+12.83%"
                ),
                metadata={
                    "source": "election_cycle",
                    "source_tier": "T2",
                    "historical_avg_return_pct": 12.83,
                    "months_to_election": months_to_election,
                },
            ))

        return signals

    # ------------------------------------------------------------------
    # 印度黄金需求信号
    # ------------------------------------------------------------------

    @staticmethod
    def _analyze_india_gold_demand() -> list[Signal]:
        """分析印度黄金需求变化对全球金价的边际影响.

        三维度综合:
        1. 进口关税 (WGC)——关税-进口相关性-0.17, 弱看空
        2. INR/USD 汇率 (FRED H.10/TradingEconomics)——卢比贬值抑制购买力
        3. GDP 季度增速 (MoSPI)——收入弹性远大于价格弹性(Kanjilal & Ghosh 2014)

        背景 (WGC 2026年7月更新):
        - 印度是全球第二大黄金消费国(年均~800吨, 几乎全进口)
        - 2026年5月进口关税 6%→15% (历史最大单次上调)
        - WGC 估算全年需求减少 50-60 吨 (~10% YoY)
        - 关税-进口量相关性仅 -0.17 (WGC 13年数据)
        - 当前(2026年7月): 需求开始复苏, 珠宝购买回升
        - USD/INR ~95.97 (年初~90.8, 贬值~5.7%), 卢比贬值抑制购买力
        - Q2 FY26 GDP +8.2% (六季最高), 全年预期 7%+, 收入增长支撑需求

        Source: WGC India Gold Market Update (2026-07-17) /
                WGC Gold Mid-Year Outlook 2026 /
                MoSPI GDP Q2 FY2025-26 / FRED H.10 /
                Economic Times / TradingEconomics
        """
        from datetime import date

        signals: list[Signal] = []
        today = date.today()

        # ── 1. 关税维度 ──
        tariff_date = date(2026, 5, 13)
        months_since_tariff = (
            (today.year - tariff_date.year) * 12
            + (today.month - tariff_date.month)
        )

        if 0 <= months_since_tariff <= 12:
            signals.append(Signal(
                name="印度黄金进口关税上调",
                dimension="fundamental",
                direction=SignalDirection.BEARISH,
                strength=SignalStrength.WEAK,
                score=-0.10,
                description=(
                    f"印度黄金进口关税 6%→15% (2026-05-13, 已{months_since_tariff}个月)。"
                    "WGC估算全年需求-50~60吨(~10%)。"
                    "但关税-进口相关性仅-0.17——走私分流+7月需求复苏部分对冲。"
                    "边际影响弱"
                ),
                metadata={
                    "source": "india_gold_demand",
                    "source_tier": "T1",
                    "tariff_pct": 15,
                    "demand_impact_tonnes": -55,
                    "correlation_tariff_import": -0.17,
                    "months_since_tariff": months_since_tariff,
                },
            ))

        # ── 2. INR/USD 汇率维度 ──
        # Fallback 数据表: 关键时点的 INR/USD (来自 FRED H.10 / TradingEconomics)
        _inr_fallback: dict[str, float] = {
            "2026-01-02": 90.80,   # 年初
            "2026-07-20": 95.97,   # 最新 (TradingEconomics)
        }
        inr_year_start = _inr_fallback.get("2026-01-02", 90.80)
        inr_latest = _inr_fallback.get("2026-07-20", 95.97)
        inr_depreciation_pct = (inr_latest - inr_year_start) / inr_year_start * 100

        if abs(inr_depreciation_pct) > 3.0:
            if inr_depreciation_pct > 0:
                # 卢比贬值 → 本币金价更贵 → 抑制购买力 → 看空
                signals.append(Signal(
                    name="卢比贬值抑制印度购金力",
                    dimension="fundamental",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.WEAK,
                    score=-0.12,
                    description=(
                        f"USD/INR 从年初 {inr_year_start} 贬至 {inr_latest}"
                        f" ({inr_depreciation_pct:+.1f}%)。"
                        "卢比贬值→本币金价上涨→削弱印度消费者购买力。"
                        "但贬值也提升黄金作为卢比计价避险资产的吸引力, 影响复杂"
                    ),
                    metadata={
                        "source": "india_gold_demand",
                        "source_tier": "T1",
                        "usd_inr_year_start": inr_year_start,
                        "usd_inr_latest": inr_latest,
                        "depreciation_pct": round(inr_depreciation_pct, 1),
                    },
                ))
            else:
                # 卢比升值 → 利好
                signals.append(Signal(
                    name="卢比走强支撑印度购金力",
                    dimension="fundamental",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.WEAK,
                    score=0.08,
                    description=(
                        f"USD/INR 从年初 {inr_year_start} 升至 {inr_latest}"
                        f" ({inr_depreciation_pct:+.1f}%)。"
                        "卢比升值→本币金价下降→支撑消费者购买力"
                    ),
                    metadata={
                        "source": "india_gold_demand",
                        "source_tier": "T1",
                        "usd_inr_latest": inr_latest,
                        "appreciation_pct": round(abs(inr_depreciation_pct), 1),
                    },
                ))

        # ── 3. GDP 季度增速维度 ──
        # Fallback: 最新已知官方数据 (MoSPI Q2 FY2025-26 = Jul-Sep 2025)
        _gdp_fallback: dict[str, float] = {
            "Q2_FY26": 8.2,     # Jul-Sep 2025, 六季最高
            "Q1_FY26": 7.8,     # Apr-Jun 2025
            "H1_FY26": 8.0,     # 上半年平均
            "FY26_forecast": 7.0,  # 政府+CEA官方预测
        }
        gdp_latest = _gdp_fallback.get("Q2_FY26", 8.2)
        gdp_forecast = _gdp_fallback.get("FY26_forecast", 7.0)

        # GDP 增速 >7% → 收入增长支撑黄金消费 (收入弹性 ~1.5-2.0)
        # 收入弹性 > 价格弹性 — 印度人越有钱越买黄金 (Kanjilal & Ghosh 2014)
        if gdp_latest >= 7.0:
            signals.append(Signal(
                name="印度经济高增速支撑黄金需求",
                dimension="fundamental",
                direction=SignalDirection.BULLISH,
                strength=SignalStrength.WEAK,
                score=0.12,
                description=(
                    f"印度Q2 FY26 GDP增速 {gdp_latest}% (六季最高, H1 {_gdp_fallback['H1_FY26']}%)。"
                    "收入弹性>>价格弹性——经济高增长是黄金需求最强驱动力。"
                    f"全年预期 {gdp_forecast}%+。"
                    "部分对冲关税+卢比贬值的负面效应"
                ),
                metadata={
                    "source": "india_gold_demand",
                    "source_tier": "T0",
                    "gdp_q2_fy26": gdp_latest,
                    "gdp_h1_fy26": _gdp_fallback["H1_FY26"],
                    "fy26_forecast": gdp_forecast,
                    "income_elasticity": "1.5-2.0",
                    "reference": "Kanjilal & Ghosh (2014) Resour Policy",
                },
            ))
        elif gdp_latest >= 5.0:
            signals.append(Signal(
                name="印度经济温和增长支撑黄金需求",
                dimension="fundamental",
                direction=SignalDirection.BULLISH,
                strength=SignalStrength.WEAK,
                score=0.06,
                description=(
                    f"印度GDP增速 {gdp_latest}%，温和增长仍支撑黄金消费。"
                    f"全年预期 {gdp_forecast}%"
                ),
                metadata={
                    "source": "india_gold_demand",
                    "source_tier": "T0",
                    "gdp_latest": gdp_latest,
                },
            ))
        else:
            signals.append(Signal(
                name="印度经济放缓拖累黄金需求",
                dimension="fundamental",
                direction=SignalDirection.BEARISH,
                strength=SignalStrength.WEAK,
                score=-0.10,
                description=(
                    f"印度GDP增速降至 {gdp_latest}%——收入效应反转，"
                    "全球第二大黄金消费国需求可能走弱"
                ),
                metadata={
                    "source": "india_gold_demand",
                    "source_tier": "T0",
                    "gdp_latest": gdp_latest,
                },
            ))

        return signals
