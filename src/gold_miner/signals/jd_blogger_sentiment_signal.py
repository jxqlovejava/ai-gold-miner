"""jdgold 黄金大V加仓榜 — 散户黄金情绪代理.

数据源: jdgold 免登录 query_blogger_trend "黄金大V买入排行" (京东金融官方公开数据, T1)。
顶部大V近期买入/卖出行为 (latestTrade) → 散户情绪方向。
集成背景: docs/analysis/jdgold-integration-analysis-2026-08-12.md E4。
"""

from __future__ import annotations

from gold_miner.data.jdgold_client import fetch_blogger_trend
from gold_miner.signals.base import (
    FactType,
    Signal,
    SignalDirection,
    SignalStrength,
)

_BULLISH_BUY_RATIO = 0.6   # 加仓榜 top-N 中 ≥60% 近期买入 → 看多
_BEARISH_BUY_RATIO = 0.4   # ≤40% 近期买入 (多数卖出) → 看空


class JdBloggerSentimentSignalGenerator:
    """京东金融黄金大V加仓榜 (散户黄金情绪代理)."""

    def generate_signals(self) -> list[Signal]:
        try:
            data = fetch_blogger_trend("黄金大V买入排行")
        except Exception:
            return []
        rankings = (data or {}).get("rankings") or []
        if not rankings:
            return []
        buy_rank = next((r for r in rankings if r.get("rankMode") == "buy"), rankings[0])
        items = buy_rank.get("items") or []
        if not items:
            return []

        buys = 0
        sells = 0
        for it in items:
            t = str(it.get("latestTrade") or "")
            if "买入" in t or "加仓" in t:
                buys += 1
            elif "卖出" in t or "减仓" in t:
                sells += 1
        if buys + sells == 0:
            return []

        buy_ratio = buys / (buys + sells)
        if buy_ratio >= _BULLISH_BUY_RATIO:
            direction = SignalDirection.BULLISH
        elif buy_ratio <= _BEARISH_BUY_RATIO:
            direction = SignalDirection.BEARISH
        else:
            direction = SignalDirection.NEUTRAL
        if buy_ratio >= 0.75 or buy_ratio <= 0.25:
            strength = SignalStrength.STRONG
        elif buy_ratio >= 0.6 or buy_ratio <= 0.4:
            strength = SignalStrength.MODERATE
        else:
            strength = SignalStrength.WEAK
        score = max(-0.6, min(0.6, round((buy_ratio - 0.5) * 1.2, 3)))

        return [
            Signal(
                name="jdgold大V加仓榜·散户情绪",
                dimension="sentiment",
                direction=direction,
                strength=strength,
                score=score,
                description=(
                    f"加仓榜 top{len(items)}: 近期买入 {buys} / 卖出 {sells} "
                    f"(买入占比 {buy_ratio * 100:.0f}%)"
                ),
                metadata={"source": "jd_blogger_rank", "source_tier": "T1"},
                fact_type=FactType.FACT,
            )
        ]
