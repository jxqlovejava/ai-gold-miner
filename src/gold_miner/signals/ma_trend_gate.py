"""长期均线趋势闸门 — MA50/MA100/MA200 三重过滤.

定位: 长期趋势过滤器(门禁), 非短期择时信号.
对齐军规 r026: 200日均线仅作长期过滤, 不单独作为买卖信号; 需基本面/资金流向确认.

设计原则:
- 闸门是"允许做多/重仓的通道"判定, 输出 WEAK 信号, 不喧宾夺主.
- 需要 ≥200 根日线(MA200 滚动窗口), 数据不足返回 insufficient_data, 不强行出信号.
- analyze() 结果同时供军规 ctx(price_above_200ma) 与报告板块渲染.

信号规则:
- state=bull   (现价>MA200 且 MA50>MA100>MA200 多头排列) → BULLISH, 闸门开, 允许做多/加仓
- state=bear   (现价<MA200 或 空头排列) → BEARISH, 闸门关, 禁重仓/暂停加仓
- state=mixed  (排列未确认) → NEUTRAL, 仅警示, 不参与维度投票
"""
from __future__ import annotations

import logging

import pandas as pd

from gold_miner.signals.base import Signal, SignalDirection, SignalStrength

logger = logging.getLogger(__name__)

# MA200 滚动窗口所需最小样本数(留 1 根余量, 避免 NaN)
MIN_MA200_BARS = 200


class MaTrendGateSignal:
    """长期均线趋势闸门信号生成器.

    输入日线 DataFrame(需 ≥200 根, 由 pipeline 拉长历史窗口提供),
    输出 0-1 个 WEAK Signal。计算结果存于 ``analyze()``, 供军规与报告复用。
    """

    SOURCE_TIER = "T0"  # 数据源: SGE 官方交易所一手数据

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df.copy()
        self._ensure_sorted()

    def _ensure_sorted(self) -> None:
        self.df = self.df.sort_values("timestamp").reset_index(drop=True)

    # ------------------------------------------------------------------
    # 均线计算
    # ------------------------------------------------------------------

    def _ma(self, period: int) -> float | None:
        """计算指定周期简单均线的当前值; 样本不足返回 None."""
        closes = self.df["close"].dropna()
        if len(closes) < period:
            return None
        val = float(closes.rolling(window=period).mean().iloc[-1])
        return round(val, 2)

    # ------------------------------------------------------------------
    # 闸门状态
    # ------------------------------------------------------------------

    def analyze(self) -> dict:
        """计算 MA50/MA100/MA200 闸门状态.

        Returns:
            dict: state/gate_open/price_above_ma200/ma50/ma100/ma200/
                  bull_alignment/bear_alignment/vs_ma200_pct/latest_close。
                  样本不足时 state=insufficient_data, price_above_ma200=None。
        """
        closes = self.df["close"].dropna()
        if len(closes) < MIN_MA200_BARS:
            logger.debug(
                f"MA200 数据不足: {len(closes)} 根 < {MIN_MA200_BARS}, 闸门返回 insufficient_data"
            )
            return {"state": "insufficient_data", "gate_open": False, "price_above_ma200": None}

        latest_close = float(closes.iloc[-1])
        ma50 = self._ma(50)
        ma100 = self._ma(100)
        ma200 = self._ma(200)

        if ma200 is None or ma200 <= 0:
            return {"state": "insufficient_data", "gate_open": False, "price_above_ma200": None}

        price_above_ma200 = latest_close > ma200
        bull_alignment = (ma50 is not None and ma100 is not None
                          and ma50 > ma100 > ma200)
        bear_alignment = (ma50 is not None and ma100 is not None
                          and ma50 < ma100 < ma200)
        vs_ma200_pct = (latest_close / ma200 - 1) * 100

        if price_above_ma200 and bull_alignment:
            state = "bull"
        elif (not price_above_ma200) or bear_alignment:
            state = "bear"
        else:
            state = "mixed"

        return {
            "state": state,
            "gate_open": state == "bull",
            "price_above_ma200": price_above_ma200,
            "ma50": ma50,
            "ma100": ma100,
            "ma200": ma200,
            "bull_alignment": bull_alignment,
            "bear_alignment": bear_alignment,
            "vs_ma200_pct": round(vs_ma200_pct, 2),
            "latest_close": round(latest_close, 2),
        }

    # ------------------------------------------------------------------
    # 信号生成
    # ------------------------------------------------------------------

    def generate_signals(self) -> list[Signal]:
        """生成长期趋势闸门信号 (0-1 条).

        闸门开 → BULLISH WEAK; 闸门关 → BEARISH WEAK; 排列未确认 → NEUTRAL 警示;
        数据不足 → 不输出 (闸门未知, 不产生方向偏见)。
        """
        gate = self.analyze()
        if gate["state"] == "insufficient_data":
            return []

        metadata = {
            "source_tier": self.SOURCE_TIER,
            "gate": True,  # 标记: 趋势闸门信号, 报告可特殊渲染
            "ma50": gate["ma50"],
            "ma100": gate["ma100"],
            "ma200": gate["ma200"],
            "price_above_ma200": gate["price_above_ma200"],
            "vs_ma200_pct": gate["vs_ma200_pct"],
            "bull_alignment": gate["bull_alignment"],
            "bear_alignment": gate["bear_alignment"],
        }

        if gate["state"] == "bull":
            return [Signal(
                name="长期趋势闸门:开启",
                dimension="technical",
                direction=SignalDirection.BULLISH,
                strength=SignalStrength.WEAK,
                score=0.15,
                description=(
                    f"现价({gate['latest_close']:.1f})站上MA200({gate['ma200']:.1f})且"
                    f"MA50({gate['ma50']:.1f})>MA100({gate['ma100']:.1f})>MA200，"
                    f"多头排列，允许做多/加仓"
                ),
                metadata=metadata,
            )]
        if gate["state"] == "bear":
            return [Signal(
                name="长期趋势闸门:关闭",
                dimension="technical",
                direction=SignalDirection.BEARISH,
                strength=SignalStrength.WEAK,
                score=-0.15,
                description=(
                    f"现价({gate['latest_close']:.1f}){'低于' if not gate['price_above_ma200'] else '高于'}"
                    f"MA200({gate['ma200']:.1f})"
                    f"{'且空头排列' if gate['bear_alignment'] else '但排列未确认'}，"
                    f"长期趋势走弱，禁重仓/暂停加仓"
                ),
                metadata=metadata,
            )]
        # mixed: 排列未确认, 仅警示不投票
        return [Signal(
            name="长期趋势闸门:中性",
            dimension="technical",
            direction=SignalDirection.NEUTRAL,
            strength=SignalStrength.WEAK,
            score=0.0,
            description=(
                f"MA50({gate['ma50']:.1f})/MA100({gate['ma100']:.1f})/MA200({gate['ma200']:.1f})"
                f"排列未确认，长期趋势方向不明，等待趋势明朗"
            ),
            metadata=metadata,
        )]
