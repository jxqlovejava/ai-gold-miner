"""COT持仓信号 — 聪明钱方向判断.

基于CFTC每周COT报告中的非商业持仓变化，生成趋势和极端信号。

信号逻辑:
1. 非商业净多仓趋势 — 连续3周增加=看涨，连续3周减少=看跌
2. 极端持仓 — 52周极值区间位置 (>90% = 超买警告, <10% = 超卖机会)
3. 商业持仓背离 — 商业净空仓减少 = 套保盘退缩 = 看涨
4. 非商业/商业持仓比 — 比值过高 = 聪明钱过于拥挤
"""

from __future__ import annotations

from loguru import logger

from gold_miner.data.cot_report import CotReportFetcher
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength


class CotSignalGenerator:
    """COT持仓信号生成器."""

    def __init__(self) -> None:
        self.fetcher = CotReportFetcher()

    def generate_signals(self) -> list[Signal]:
        """生成所有COT相关信号.

        方案A去重 (2026-08-11): 趋势信号与同向 aligned 分歧信号合并, 避免
        「非商业净多变化」这一底层事实被两个信号重复加权 (事故: 同日同向
        输出 聪明钱减仓 -0.80 + 一致看空 -0.30, 同一事实计两次)。
        """
        signals: list[Signal] = []
        trend = self._trend_signals()
        divergence = self._divergence_signals()
        signals.extend(self._merge_trend_and_divergence(trend, divergence))
        signals.extend(self._extreme_signals())
        signals.extend(self._structure_signals())
        return signals

    @staticmethod
    def _merge_trend_and_divergence(
        trend_signals: list[Signal],
        divergence_signals: list[Signal],
    ) -> list[Signal]:
        """合并趋势信号与分歧信号, 消除同一底层事实的重复加权.

        规则 (方案A):
        - 存在趋势信号 且 分歧信号为 aligned_* 且方向同向 → 商业确认合并进
          趋势信号 (提分 + 追加描述), 不单独发分歧信号;
        - 分歧信号为 divergence_* (商业端与聪明钱背离, 方向相反, 携带独立
          信息) 或 趋势信号缺失 → 分歧信号独立触发。

        趋势信号基于 4 周趋势, 分歧信号基于最近 1 期对比, 二者可能在
        「趋势 bearish 但最新一期转多」等情形下方向相反 —— 此时保留双方
        作为有效冲突信号, 不合并。
        """
        if not trend_signals:
            return list(divergence_signals)

        trend = trend_signals[0]  # 趋势信号至多 1 个 (up/down 互斥分支)
        merged: list[Signal] = [trend]

        for div in divergence_signals:
            is_aligned = div.metadata.get("pattern", "").startswith("aligned_")
            if is_aligned and div.direction == trend.direction:
                new_score = max(-0.95, min(0.95, trend.score + div.score))
                merged = [Signal(
                    name=trend.name,
                    dimension=trend.dimension,
                    direction=trend.direction,
                    strength=trend.strength,
                    score=round(new_score, 2),
                    description=(
                        f"{trend.description}; 商业套保同向确认 "
                        f"({div.description})"
                    ),
                    metadata={
                        **trend.metadata,
                        "merged_divergence": div.name,
                        "commercial_confirmation": True,
                    },
                )]
            else:
                # divergence_* 背离 (方向相反) → 独立触发, 保留独立信息
                merged.append(div)
        return merged

    def _structure_signals(self) -> list[Signal]:
        """持仓结构三段式信号 — 总持仓出清 / 空头投降 / 多头回归.

        借鉴交易员框架：把非商业净持仓拆回 gross 多头/空头/总持仓三根线，
        各与近 52 周周期极值比较，识别「杠杆浮筹出清 → 空头认赔 → 多头回归」
        的结构性洗盘，用于区分「反转」与「反弹」。

        仅使用真实 CFTC 数据（fetch_real），fallback 合成常量数据不参与计算；
        合成数据的 OI/空头恒定，会破坏周期极值判断。
        """
        signals: list[Signal] = []
        try:
            df = self.fetcher.fetch_real()
            if df.empty or len(df) < 6:
                return signals

            df = df.sort_values("timestamp")
            window = min(len(df), 52)

            longs = df["open"].astype(float)    # 非商业多头 (gross long)
            shorts = df["low"].astype(float)    # 非商业空头 (gross short)
            oi = df["volume"].astype(float)     # 总持仓 (Open Interest)
            latest = len(df) - 1

            oi_peak = oi.tail(window).max()
            short_peak = shorts.tail(window).max()
            long_trough = longs.tail(window).min()
            long_peak = longs.tail(window).max()

            cur_oi = oi.iloc[latest]
            cur_short = shorts.iloc[latest]
            cur_long = longs.iloc[latest]

            # 1) 总持仓出清: 当前 OI 较周期顶回落 ≥25% → 杠杆浮筹被挤出
            washout = bool(oi_peak > 0 and cur_oi / oi_peak <= 0.75)
            washout_ratio = cur_oi / oi_peak if oi_peak > 0 else 1.0

            # 2) 空头投降: 当前空头较周期顶回落 ≥50% → 空头认赔离场
            capitulation = bool(short_peak > 0 and cur_short / short_peak <= 0.50)
            capitulation_ratio = cur_short / short_peak if short_peak > 0 else 1.0

            # 3) 多头回归: 多头从周期低谷回升 ≥30% 且最近一期仍在增仓
            spread = long_peak - long_trough
            return_from_trough = bool(spread > 0 and (cur_long - long_trough) / spread >= 0.30)
            rising = bool(latest >= 1 and longs.iloc[latest] > longs.iloc[latest - 1])
            long_return = bool(return_from_trough and rising)

            confirmed = [name for name, ok in (
                ("总持仓出清", washout),
                ("空头投降", capitulation),
                ("多头回归", long_return),
            ) if ok]

            if not confirmed:
                return signals

            n = len(confirmed)
            if n >= 3:
                name = "COT持仓反转结构确认"
                strength = SignalStrength.STRONG
                score = 0.5
            elif n == 2:
                name = "COT持仓结构改善"
                strength = SignalStrength.MODERATE
                score = 0.3
            else:
                name = "COT持仓结构初现改善"
                strength = SignalStrength.WEAK
                score = 0.15

            signals.append(Signal(
                name=name,
                dimension="smart_money",
                direction=SignalDirection.BULLISH,
                strength=strength,
                score=score,
                description=(
                    f"持仓结构{len(confirmed)}/3段改善 [{', '.join(confirmed)}]: "
                    f"总持仓 {cur_oi:,.0f}/{oi_peak:,.0f}手({washout_ratio:.0%}), "
                    f"空头 {cur_short:,.0f}/{short_peak:,.0f}手({capitulation_ratio:.0%}), "
                    f"多头 {cur_long:,.0f}手。结构性洗盘特征支持反转而非反弹"
                ),
                metadata={
                    "source": "cot_report",
                    "signal_type": "position_structure",
                    "confirmed": confirmed,
                    "washout_ratio": round(washout_ratio, 3),
                    "capitulation_ratio": round(capitulation_ratio, 3),
                    "oi_peak": int(oi_peak),
                    "short_peak": int(short_peak),
                    "long_trough": int(long_trough),
                    "window_weeks": window,
                    "real_data": True,
                },
            ))
        except Exception as e:
            logger.debug(f"COT持仓结构信号异常: {e}")

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
                    dimension="smart_money",
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
                    dimension="smart_money",
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
                    dimension="smart_money",
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
                    dimension="smart_money",
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
                    dimension="smart_money",
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
                    dimension="smart_money",
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
                    dimension="smart_money",
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
                    dimension="smart_money",
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
                    dimension="smart_money",
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
                    dimension="smart_money",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.WEAK,
                    score=-0.15,
                    description="聪明钱加仓但商业套保增加，Producer 端偏悲观",
                    metadata={"source": "cot_report", "pattern": "divergence_bearish"},
                ))

        except Exception as e:
            logger.debug(f"COT背离信号异常: {e}")

        return signals
