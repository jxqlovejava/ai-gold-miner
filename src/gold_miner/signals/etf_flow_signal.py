"""ETF资金流信号 — 黄金ETF + 比特币ETF流入流出."""

from __future__ import annotations

from loguru import logger

from gold_miner.data.etf_flow import (
    BtcEtfFlowFetcher,
    GoldEtfFlowFetcher,
    IntlGoldEtfFlowFetcher,
)
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength


class EtfFlowSignalGenerator:
    """ETF资金流信号生成器 — 同时追踪黄金ETF和比特币ETF."""

    SOURCE_TIER = "T1"  # 默认; 持仓路径单独标 T0
    HOLDINGS_SOURCE_TIER = "T0"  # SPDR GLD 官方持仓

    def __init__(self) -> None:
        self.gold_fetcher = GoldEtfFlowFetcher()
        self.btc_fetcher = BtcEtfFlowFetcher()
        self.intl_fetcher = IntlGoldEtfFlowFetcher()

    def generate_signals(self) -> list[Signal]:
        """生成所有ETF资金流信号 — 独立 fetcher 并行拉取.

        国内(东财)/国际(GLD持仓)两个数据源互相独立, 串行执行耗时相加
        (profile: gold_etf ~6.5s + intl_etf ~4.2s ≈ 11s 网络)。
        并行后总耗时由最慢一条决定 (~max ≈6.5s)。_cross_asset_signals
        依赖前两者数据, 在并行完成后执行 — 其 fetch 命中 TtlCache(600s),
        不再发网络请求。

        btc_etf 维度 2026-08-21 禁用 (yfinance 持续 429 拿不到数据,
        CoinGlass 接入后恢复)。cross_etf 依赖 BTC 数据, 一并跳过。
        _btc_etf_signals/_cross_asset_signals 方法保留供测试/恢复。
        """
        from concurrent.futures import ThreadPoolExecutor

        signals: list[Signal] = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_gold = pool.submit(self._gold_etf_signals)
            f_intl = pool.submit(self._intl_gold_etf_signals)
            for name, f in (("国内黄金ETF", f_gold), ("国际黄金ETF", f_intl)):
                try:
                    signals.extend(f.result())
                except Exception as e:
                    logger.warning(f"{name}信号异常: {e}")
        for s in signals:
            s.metadata.setdefault("source_tier", self.SOURCE_TIER)
        return signals

    # ------------------------------------------------------------------
    # 国内黄金ETF信号（价格 proxy，非真实资金流）
    # ------------------------------------------------------------------

    def _gold_etf_signals(self) -> list[Signal]:
        """国内黄金ETF价格/成交量 proxy 信号.

        注意: 国内路径目前仅有日增长率+成交量，不是真实申赎资金流。
        信号名与描述必须标明 proxy，score 上限 |0.3|。
        """
        signals: list[Signal] = []
        try:
            summary = self.gold_fetcher.fetch_daily_change()
            if summary.get("status") != "ok":
                return signals

            direction_str = summary.get("flow_direction", "neutral")
            nav_change = summary.get("avg_nav_change_pct", 0)
            total_vol = summary.get("total_volume", 0)
            total_turnover = summary.get("total_turnover", 0)

            # 价格变动 proxy（非真实资金流），score 严格封顶
            if direction_str == "inflow" and nav_change > 0.5:
                score = min(nav_change / 10, 0.3)
                signals.append(
                    Signal(
                        name="国内黄金ETF价格变动(proxy)",
                        dimension="smart_money",
                        direction=SignalDirection.BULLISH,
                        strength=SignalStrength.WEAK,
                        score=round(score, 2),
                        description=(
                            f"国内黄金ETF价格变动(proxy，非真实资金流): "
                            f"日涨{nav_change:+.2f}%, 成交额{turnover_fmt(total_turnover)}, "
                            f"成交量{turnover_fmt(total_vol)}手"
                        ),
                        metadata={
                            "source": "gold_etf_price_proxy",
                            "nav_change": nav_change,
                            "is_real_flow": False,
                        },
                    )
                )
            elif direction_str == "outflow" and nav_change < -0.5:
                score = max(nav_change / 10, -0.3)
                signals.append(
                    Signal(
                        name="国内黄金ETF价格变动(proxy)",
                        dimension="smart_money",
                        direction=SignalDirection.BEARISH,
                        strength=SignalStrength.WEAK,
                        score=round(score, 2),
                        description=(
                            f"国内黄金ETF价格变动(proxy，非真实资金流): "
                            f"日跌{nav_change:+.2f}%, 成交额{turnover_fmt(total_turnover)}"
                        ),
                        metadata={
                            "source": "gold_etf_price_proxy",
                            "nav_change": nav_change,
                            "is_real_flow": False,
                        },
                    )
                )

            # 成交量异动（弱 proxy）
            if total_vol > 5_000_000:
                signals.append(
                    Signal(
                        name="国内黄金ETF成交放量(proxy)",
                        dimension="smart_money",
                        direction=(
                            SignalDirection.BULLISH
                            if nav_change > 0
                            else SignalDirection.BEARISH
                        ),
                        strength=SignalStrength.WEAK,
                        score=0.1 if nav_change > 0 else -0.1,
                        description=(
                            f"国内黄金ETF成交{turnover_fmt(total_vol)}手, "
                            f"放量(价格/成交量 proxy，非真实资金流)"
                        ),
                        metadata={
                            "source": "gold_etf_volume_proxy",
                            "is_real_flow": False,
                        },
                    )
                )

        except Exception as e:
            logger.debug(f"黄金ETF信号异常: {e}")

        return signals

    # ------------------------------------------------------------------
    # 国际黄金ETF信号 — 持仓(吨)为主
    # ------------------------------------------------------------------

    def _intl_gold_etf_signals(self) -> list[Signal]:
        """国际黄金ETF资金流信号 — 以 GLD 官方持仓(吨)日变化为主信号.

        逻辑:
        - GLD 持仓吨数↑ = 真实申购/增持 → 看涨 (T0)
        - GLD 持仓吨数↓ = 真实赎回/减持 → 看跌 (T0)
        - 成交量异动仅作 secondary 弱 proxy，|score|≤0.2，不得当资金流
        """
        signals: list[Signal] = []
        try:
            summary = self.intl_fetcher.fetch_flow_summary()
            if summary.get("status") != "ok":
                return signals

            direction = summary.get("flow_direction", "neutral")
            score = float(summary.get("flow_score", 0.0))
            tonnes_delta = float(summary.get("tonnes_delta", 0.0))
            holdings_pct = float(summary.get("holdings_change_pct", 0.0))
            holdings_tonnes = float(summary.get("holdings_tonnes", 0.0))
            gld_vol_ratio = float(summary.get("gld_volume_ratio", 1.0))
            gld_change = float(summary.get("gld_change_pct", 0.0))

            holdings_meta = {
                "source": "gld_holdings_tonnes",
                "source_tier": self.HOLDINGS_SOURCE_TIER,
                "tonnes_delta": tonnes_delta,
                "holdings_change_pct": holdings_pct,
                "holdings_tonnes": holdings_tonnes,
                "as_of": summary.get("as_of"),
                "is_real_flow": True,
            }

            if direction == "strong_inflow":
                signals.append(
                    Signal(
                        name="国际黄金ETF大幅流入",
                        dimension="smart_money",
                        direction=SignalDirection.BULLISH,
                        strength=SignalStrength.STRONG,
                        score=round(score, 2),
                        description=(
                            f"GLD持仓(吨)变化: {tonnes_delta:+.2f}吨 "
                            f"({holdings_pct:+.3f}%), 现持仓{holdings_tonnes:.1f}吨, 大幅增持"
                        ),
                        metadata=holdings_meta,
                    )
                )
            elif direction == "inflow":
                signals.append(
                    Signal(
                        name="国际黄金ETF资金流入",
                        dimension="smart_money",
                        direction=SignalDirection.BULLISH,
                        strength=SignalStrength.MODERATE,
                        score=round(score, 2),
                        description=(
                            f"GLD持仓(吨)变化: {tonnes_delta:+.2f}吨 "
                            f"({holdings_pct:+.3f}%), 现持仓{holdings_tonnes:.1f}吨"
                        ),
                        metadata=holdings_meta,
                    )
                )
            elif direction == "strong_outflow":
                signals.append(
                    Signal(
                        name="国际黄金ETF大幅流出",
                        dimension="smart_money",
                        direction=SignalDirection.BEARISH,
                        strength=SignalStrength.STRONG,
                        score=round(score, 2),
                        description=(
                            f"GLD持仓(吨)变化: {tonnes_delta:+.2f}吨 "
                            f"({holdings_pct:+.3f}%), 现持仓{holdings_tonnes:.1f}吨, 大幅减持"
                        ),
                        metadata=holdings_meta,
                    )
                )
            elif direction == "outflow":
                signals.append(
                    Signal(
                        name="国际黄金ETF资金流出",
                        dimension="smart_money",
                        direction=SignalDirection.BEARISH,
                        strength=SignalStrength.MODERATE,
                        score=round(score, 2),
                        description=(
                            f"GLD持仓(吨)变化: {tonnes_delta:+.2f}吨 "
                            f"({holdings_pct:+.3f}%), 现持仓{holdings_tonnes:.1f}吨"
                        ),
                        metadata=holdings_meta,
                    )
                )

            # Secondary: 成交量 proxy（非真实资金流），|score|≤0.2
            if gld_vol_ratio > 2.0:
                vol_score = 0.2 if gld_change > 0 else -0.15
                vol_score = max(-0.2, min(0.2, vol_score))
                signals.append(
                    Signal(
                        name="GLD成交量异常放大(proxy)",
                        dimension="smart_money",
                        direction=(
                            SignalDirection.BULLISH
                            if gld_change > 0
                            else SignalDirection.BEARISH
                        ),
                        strength=SignalStrength.WEAK,
                        score=vol_score,
                        description=(
                            f"价格/成交量 proxy（非真实资金流）: "
                            f"GLD成交量达20日均值{gld_vol_ratio:.1f}倍, 价格{gld_change:+.1f}%"
                        ),
                        metadata={
                            "source": "intl_gold_etf_volume_proxy",
                            "gld_vol_ratio": gld_vol_ratio,
                            "is_real_flow": False,
                        },
                    )
                )

            # 国内(价格proxy) vs 国际(真实持仓) 背离
            domestic = self.gold_fetcher.fetch_daily_change()
            if domestic.get("status") == "ok":
                dom_dir = domestic.get("flow_direction", "neutral")
                intl_dir = direction
                if dom_dir == "inflow" and "outflow" in intl_dir:
                    signals.append(
                        Signal(
                            name="内外盘背离: 国内↑国际↓",
                            dimension="smart_money",
                            direction=SignalDirection.BULLISH,
                            strength=SignalStrength.WEAK,
                            score=0.1,
                            description=(
                                "国内黄金ETF价格上涨(proxy)但国际GLD持仓(吨)流出，内资更乐观"
                            ),
                            metadata={"source": "domestic_intl_divergence"},
                        )
                    )
                elif dom_dir == "outflow" and "inflow" in intl_dir:
                    signals.append(
                        Signal(
                            name="内外盘背离: 国内↓国际↑",
                            dimension="smart_money",
                            direction=SignalDirection.BEARISH,
                            strength=SignalStrength.WEAK,
                            score=-0.1,
                            description=(
                                "国内黄金ETF价格下跌(proxy)但国际GLD持仓(吨)流入，外资更乐观"
                            ),
                            metadata={"source": "domestic_intl_divergence"},
                        )
                    )

        except Exception as e:
            logger.debug(f"国际黄金ETF信号异常: {e}")

        return signals

    # ------------------------------------------------------------------
    # 比特币ETF信号
    # ------------------------------------------------------------------

    def _btc_etf_signals(self) -> list[Signal]:
        """比特币ETF资金流信号 — 作为风险偏好/避险情绪的辅助指标.

        逻辑:
        - BTC ETF大幅流入=风险偏好上升, 短期可能分流黄金资金 (轻微利空黄金)
        - BTC ETF大幅流出=避险情绪升温, 资金可能转向黄金 (利多黄金)
        - 量价背离=信号减弱
        """
        signals: list[Signal] = []
        try:
            flow = self.btc_fetcher.fetch_flow_signal()
            if flow.get("status") != "ok":
                return signals

            direction = flow.get("direction", "neutral")
            flow.get("score", 0)
            avg_change = flow.get("avg_change_pct", 0)
            vol_surge = flow.get("volume_surge_etfs", 0)

            if direction == "strong_inflow":
                # BTC ETF严重流入 → 风险偏好极高, 黄金短期可能承压
                signals.append(
                    Signal(
                        name="BTC ETF大幅流入(风险偏好↑)",
                        dimension="smart_money",
                        direction=SignalDirection.BEARISH,  # 对黄金轻微利空
                        strength=SignalStrength.WEAK,
                        score=-0.15,
                        description=(
                            f"BTC ETF资金大幅流入: 均涨{avg_change:+.1f}%, "
                            f"{vol_surge}只ETF放量, 风险偏好上升分流黄金需求"
                        ),
                        metadata={"source": "btc_etf", "btc_change": avg_change},
                    )
                )
            elif direction == "strong_outflow":
                # BTC ETF严重流出 → 避险升温, 黄金受益
                signals.append(
                    Signal(
                        name="BTC ETF大幅流出(避险↑)",
                        dimension="smart_money",
                        direction=SignalDirection.BULLISH,  # 对黄金利多
                        strength=SignalStrength.WEAK,
                        score=0.15,
                        description=(
                            f"BTC ETF资金大幅流出: 均跌{avg_change:+.1f}%, "
                            f"{vol_surge}只ETF放量, 避险情绪利好黄金"
                        ),
                        metadata={"source": "btc_etf", "btc_change": avg_change},
                    )
                )

        except Exception as e:
            logger.debug(f"BTC ETF信号异常: {e}")

        return signals

    # ------------------------------------------------------------------
    # 跨资产信号
    # ------------------------------------------------------------------

    def _cross_asset_signals(self) -> list[Signal]:
        """跨资产对比信号 — 黄金 vs BTC ETF资金流向背离."""
        signals: list[Signal] = []
        try:
            gold = self.gold_fetcher.fetch_daily_change()
            btc = self.btc_fetcher.fetch_flow_signal()

            if gold.get("status") != "ok" or btc.get("status") != "ok":
                return signals

            gold_dir = gold.get("flow_direction", "neutral")
            btc_dir = btc.get("direction", "neutral")

            # 背离信号: 黄金流入 + BTC流出 → 强烈避险信号
            if gold_dir == "inflow" and "outflow" in btc_dir:
                signals.append(
                    Signal(
                        name="金银背离: 黄金↑BTC↓ (强烈避险)",
                        dimension="smart_money",
                        direction=SignalDirection.BULLISH,
                        strength=SignalStrength.MODERATE,
                        score=0.25,
                        description="黄金ETF流入 + BTC ETF流出 → 资金从风险资产转向避险资产",
                        metadata={"source": "cross_etf", "pattern": "risk_off"},
                    )
                )

            # 黄金流出 + BTC流入 → 风险偏好上升
            if gold_dir == "outflow" and "inflow" in btc_dir:
                signals.append(
                    Signal(
                        name="金银背离: 黄金↓BTC↑ (风险偏好↑)",
                        dimension="smart_money",
                        direction=SignalDirection.BEARISH,
                        strength=SignalStrength.WEAK,
                        score=-0.15,
                        description="黄金ETF流出 + BTC ETF流入 → 资金从避险转向风险资产",
                        metadata={"source": "cross_etf", "pattern": "risk_on"},
                    )
                )

        except Exception as e:
            logger.debug(f"跨资产ETF信号异常: {e}")

        return signals


def turnover_fmt(n: float) -> str:
    """格式化成交额/量."""
    if abs(n) >= 1e8:
        return f"{n / 1e8:.2f}亿"
    if abs(n) >= 1e4:
        return f"{n / 1e4:.0f}万"
    return str(int(n))
