"""jdgold 资金炸弹/大单资金流信号 — 分钟级聪明钱方向 (补 COMEX 纯模拟窟窿).

数据源: jdgold 免登录 query_gold_analysis "资金炸弹" (京东金融官方公开数据, T1)。
一分钟大单成交 + 多空订单占比 → 方向/score; 相对 COT 周频, 这是分钟级即时资金流。
集成背景: docs/analysis/jdgold-integration-analysis-2026-08-12.md E1。
"""

from __future__ import annotations

from gold_miner.data.jdgold_client import fetch_bomb
from gold_miner.signals.base import (
    FactType,
    Signal,
    SignalDirection,
    SignalStrength,
)


def _to_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


class JdFundBombSignalGenerator:
    """京东金融黄金资金炸弹 (分钟级大单资金流).

    多空订单占比偏离 50% 越远 → 方向越明确; 1分钟成交额作强度参考。
    """

    def generate_signals(self) -> list[Signal]:
        try:
            data = fetch_bomb("latest")
        except Exception:
            return []
        if not data or not data.get("items"):
            return []
        item = data["items"][0]
        long_r = _to_float(item.get("longPositionRatio"))
        short_r = _to_float(item.get("shortPositionRatio"))
        if long_r is None or short_r is None or long_r + short_r <= 0:
            return []

        diff = long_r - short_r  # 多单占比 − 空单占比 (pp)
        volume = _to_float(item.get("tradingVolume")) or 0.0
        volume_yi = volume / 1e8  # 亿美元

        if diff >= 10:
            direction = SignalDirection.BULLISH
        elif diff <= -10:
            direction = SignalDirection.BEARISH
        else:
            direction = SignalDirection.NEUTRAL
        if abs(diff) >= 15:
            strength = SignalStrength.STRONG
        elif abs(diff) >= 8:
            strength = SignalStrength.MODERATE
        else:
            strength = SignalStrength.WEAK
        score = max(-0.6, min(0.6, diff / 100))

        extra = f" (净多 {diff:+.1f}pp)" if abs(diff) >= 1 else ""
        return [
            Signal(
                name="jdgold资金炸弹·大单多空占比",
                dimension="smart_money",
                direction=direction,
                strength=strength,
                score=score,
                description=(
                    f"1分钟成交 {volume_yi:.2f}亿美元, 多单 {long_r:.1f}% / 空单 {short_r:.1f}%"
                    f"{extra}"
                ),
                metadata={"source": "jd_fund_bomb", "source_tier": "T1"},
                fact_type=FactType.FACT,
            )
        ]
