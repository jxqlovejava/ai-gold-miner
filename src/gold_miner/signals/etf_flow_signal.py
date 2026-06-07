"""ETF资金流信号 — 黄金ETF + 比特币ETF流入流出."""

from __future__ import annotations

from typing import Any

from loguru import logger

from gold_miner.data.etf_flow import BtcEtfFlowFetcher, GoldEtfFlowFetcher
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength


class EtfFlowSignalGenerator:
    """ETF资金流信号生成器 — 同时追踪黄金ETF和比特币ETF."""

    def __init__(self) -> None:
        self.gold_fetcher = GoldEtfFlowFetcher()
        self.btc_fetcher = BtcEtfFlowFetcher()

    def generate_signals(self) -> list[Signal]:
        """生成所有ETF资金流信号."""
        signals: list[Signal] = []
        signals.extend(self._gold_etf_signals())
        signals.extend(self._btc_etf_signals())
        signals.extend(self._cross_asset_signals())
        return signals

    # ------------------------------------------------------------------
    # 黄金ETF信号
    # ------------------------------------------------------------------

    def _gold_etf_signals(self) -> list[Signal]:
        """黄金ETF资金流信号 — 资金流入=看涨，流出=看跌."""
        signals: list[Signal] = []
        try:
            summary = self.gold_fetcher.fetch_daily_change()
            if summary.get("status") != "ok":
                return signals

            direction_str = summary.get("flow_direction", "neutral")
            nav_change = summary.get("avg_nav_change_pct", 0)
            total_vol = summary.get("total_volume", 0)
            total_turnover = summary.get("total_turnover", 0)

            # 量价配合信号
            if direction_str == "inflow" and nav_change > 0.5:
                strength = SignalStrength.STRONG if nav_change > 1.5 else SignalStrength.MODERATE
                signals.append(Signal(
                    name="黄金ETF资金流入",
                    dimension="sentiment",
                    direction=SignalDirection.BULLISH,
                    strength=strength,
                    score=min(nav_change / 3, 0.8),
                    description=(
                        f"黄金ETF量价齐升: 成交额{turnover_fmt(total_turnover)}, "
                        f"净值+{nav_change:.2f}%, 成交量{turnover_fmt(total_vol)}手"
                    ),
                    metadata={"source": "gold_etf", "nav_change": nav_change},
                ))
            elif direction_str == "outflow" and nav_change < -0.5:
                score = max(nav_change / 3, -0.8)
                signals.append(Signal(
                    name="黄金ETF资金流出",
                    dimension="sentiment",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.MODERATE if nav_change < -1.5 else SignalStrength.WEAK,
                    score=round(score, 2),
                    description=(
                        f"黄金ETF量价齐跌: 成交额{turnover_fmt(total_turnover)}, "
                        f"净值{nav_change:+.2f}%"
                    ),
                    metadata={"source": "gold_etf", "nav_change": nav_change},
                ))

            # 成交量异动
            if total_vol > 5_000_000:
                signals.append(Signal(
                    name="黄金ETF成交放量",
                    dimension="sentiment",
                    direction=SignalDirection.BULLISH if nav_change > 0 else SignalDirection.BEARISH,
                    strength=SignalStrength.WEAK,
                    score=0.15 if nav_change > 0 else -0.15,
                    description=f"黄金ETF成交{turnover_fmt(total_vol)}手, 显著放量",
                    metadata={"source": "gold_etf_volume"},
                ))

        except Exception as e:
            logger.debug(f"黄金ETF信号异常: {e}")

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
            score = flow.get("score", 0)
            avg_change = flow.get("avg_change_pct", 0)
            vol_surge = flow.get("volume_surge_etfs", 0)

            if direction == "strong_inflow":
                # BTC ETF严重流入 → 风险偏好极高, 黄金短期可能承压
                signals.append(Signal(
                    name="BTC ETF大幅流入(风险偏好↑)",
                    dimension="sentiment",
                    direction=SignalDirection.BEARISH,  # 对黄金轻微利空
                    strength=SignalStrength.WEAK,
                    score=-0.15,
                    description=(
                        f"BTC ETF资金大幅流入: 均涨{avg_change:+.1f}%, "
                        f"{vol_surge}只ETF放量, 风险偏好上升分流黄金需求"
                    ),
                    metadata={"source": "btc_etf", "btc_change": avg_change},
                ))
            elif direction == "strong_outflow":
                # BTC ETF严重流出 → 避险升温, 黄金受益
                signals.append(Signal(
                    name="BTC ETF大幅流出(避险↑)",
                    dimension="sentiment",
                    direction=SignalDirection.BULLISH,  # 对黄金利多
                    strength=SignalStrength.WEAK,
                    score=0.15,
                    description=(
                        f"BTC ETF资金大幅流出: 均跌{avg_change:+.1f}%, "
                        f"{vol_surge}只ETF放量, 避险情绪利好黄金"
                    ),
                    metadata={"source": "btc_etf", "btc_change": avg_change},
                ))

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
                signals.append(Signal(
                    name="金银背离: 黄金↑BTC↓ (强烈避险)",
                    dimension="sentiment",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.MODERATE,
                    score=0.25,
                    description="黄金ETF流入 + BTC ETF流出 → 资金从风险资产转向避险资产",
                    metadata={"source": "cross_etf", "pattern": "risk_off"},
                ))

            # 黄金流出 + BTC流入 → 风险偏好上升
            if gold_dir == "outflow" and "inflow" in btc_dir:
                signals.append(Signal(
                    name="金银背离: 黄金↓BTC↑ (风险偏好↑)",
                    dimension="sentiment",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.WEAK,
                    score=-0.15,
                    description="黄金ETF流出 + BTC ETF流入 → 资金从避险转向风险资产",
                    metadata={"source": "cross_etf", "pattern": "risk_on"},
                ))

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
