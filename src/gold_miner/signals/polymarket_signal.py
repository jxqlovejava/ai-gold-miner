"""Polymarket 预测市场信号 — 基于"真金白银"的市场预期生成交易信号.

Polymarket 的价格是用户用真实资金押注的结果，比调查、社交媒体更可靠。
信号逻辑核心：根据市场问题判断 YES/NO 分别对应黄金的哪个方向。

示例:
- "Will Fed cut rates?" YES = 降息 → 黄金 BULLISH
- "Will USD strengthen?" YES = 美元走强 → 黄金 BEARISH
- "Will gold price exceed $2500?" YES = 金价涨 → 黄金 BULLISH
"""

from dataclasses import dataclass
from typing import Callable

from loguru import logger

from gold_miner.data.polymarket import PredictionMarket
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength


# ------------------------------------------------------------------
# 方向判定规则
# ------------------------------------------------------------------
# 每条规则: (关键词列表, 方向映射函数)
# 方向映射函数: question (str) -> YES 对黄金的方向

Rule = tuple[list[str], Callable[[str], SignalDirection]]


def _yes_bullish(_q: str) -> SignalDirection:
    return SignalDirection.BULLISH


def _yes_bearish(_q: str) -> SignalDirection:
    return SignalDirection.BEARISH


def _yes_bullish_if_contains(phrase: str) -> Callable[[str], SignalDirection]:
    def fn(q: str) -> SignalDirection:
        return SignalDirection.BULLISH if phrase.lower() in q.lower() else SignalDirection.BEARISH
    return fn


# 规则表（按优先级排序，先匹配先命中）
DIRECTION_RULES: list[Rule] = [
    # --- 宏观：降息/低利率利好黄金 ---
    (["cut rate", "rate cut", "lower rate", "loosen"], _yes_bullish),
    (["hike rate", "rate hike", "raise rate", "tighten"], _yes_bearish),
    (["pause rate", "hold rate", "no hike"], _yes_bullish),  # 暂停加息中性偏利好

    # --- 宏观：高通胀利好黄金 ---
    (["inflation above", "inflation >", "cpi above", "cpi >"], _yes_bullish),
    (["inflation below", "inflation <", "cpi below", "cpi <", "disinflation"], _yes_bearish),

    # --- 宏观：衰退利好黄金（避险）---
    (["recession", "economic contraction", "negative gdp"], _yes_bullish),

    # --- 地缘：冲突/战争利好黄金 ---
    (["war", "conflict", "attack", "invasion", "strike", "sanction"], _yes_bullish),
    (["peace", "ceasefire", "deal signed", "normalize relation"], _yes_bearish),

    # --- 美元：美元走强利空黄金 ---
    (["dollar strengthen", "usd strengthen", "dxy above", "dollar rise"], _yes_bearish),
    (["dollar weaken", "usd weaken", "dxy below", "dollar fall"], _yes_bullish),

    # --- 黄金直连 ---
    (["gold above", "gold >", "gold exceed", "gold rise", "gold higher", "gold bullish"], _yes_bullish),
    (["gold below", "gold <", "gold under", "gold fall", "gold lower", "gold bearish"], _yes_bearish),

    # --- 政策：财政扩张/关税战利好黄金 ---
    (["tariff", "trade war", "debt ceiling", "fiscal expansion", "stimulus"], _yes_bullish),
]

# 信号强度阈值
PROB_THRESHOLD_STRONG = 0.70
PROB_THRESHOLD_MODERATE = 0.55
VOLUME_THRESHOLD_SIGNIFICANT = 10_000.0  # 24h 交易量门槛


@dataclass
class PolymarketSignalConfig:
    """信号生成配置."""

    prob_threshold_strong: float = 0.70
    prob_threshold_moderate: float = 0.55
    min_volume_24h: float = 500.0
    max_age_hours: float = 24.0  # 数据新鲜度要求


class PolymarketSignalGenerator:
    """基于 Polymarket 预测市场数据生成黄金交易信号."""

    def __init__(self, config: PolymarketSignalConfig | None = None) -> None:
        self.config = config or PolymarketSignalConfig()

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def generate(self, markets: list[PredictionMarket]) -> list[Signal]:
        """生成所有 Polymarket 信号."""
        signals: list[Signal] = []

        for m in markets:
            sig = self._market_to_signal(m)
            if sig:
                signals.append(sig)

        # 同方向合并：如果多个宏观市场同向，生成综合信号
        signals.extend(self._aggregate_by_category(markets))

        logger.info(f"Polymarket 信号: 生成 {len(signals)} 个")
        return signals

    # ------------------------------------------------------------------
    # 单市场信号转换
    # ------------------------------------------------------------------

    def _market_to_signal(self, market: PredictionMarket) -> Signal | None:
        """将单个预测市场转为信号.

        逻辑:
        1. 用规则表判定 YES 对应黄金方向
        2. 取 outcome_yes_price 作为概率
        3. 概率越高、交易量越大，信号越强
        """
        direction = self._infer_direction(market.question)
        if direction is None:
            return None

        prob = market.outcome_yes_price
        if prob < self.config.prob_threshold_moderate:
            return None  # 概率不够高，信号太弱

        # 市场流动性评分: 交易量越大越可信
        volume_score = min(market.volume_24h / VOLUME_THRESHOLD_SIGNIFICANT, 1.0)

        # 价格动量加分: 如果近期概率在快速变化
        momentum_bonus = 0.0
        if market.price_change_1w is not None:
            momentum_bonus = min(abs(market.price_change_1w) * 2, 0.2)

        # 综合得分
        base_score = prob * 0.6 + volume_score * 0.3 + momentum_bonus
        score = min(base_score, 1.0)
        if direction == SignalDirection.BEARISH:
            score = -score

        # 强度分级
        if prob >= self.config.prob_threshold_strong and volume_score > 0.5:
            strength = SignalStrength.STRONG
        elif prob >= self.config.prob_threshold_moderate:
            strength = SignalStrength.MODERATE
        else:
            strength = SignalStrength.WEAK

        category_label = {
            "macro": "宏观",
            "geopolitical": "地缘",
            "currency": "货币",
            "gold_direct": "金价",
            "policy": "政策",
        }.get(market.matched_category, market.matched_category)

        return Signal(
            name=f"Polymarket-{category_label}: {market.question[:40]}...",
            dimension="polymarket",
            direction=direction,
            strength=strength,
            score=score,
            description=(
                f"Polymarket 市场 '{market.question[:60]}' "
                f"YES 概率 {prob*100:.1f}%，"
                f"24h 交易量 ${market.volume_24h:,.0f}。"
                f"市场预期对应黄金{'利好' if direction == SignalDirection.BULLISH else '利空'}。"
            ),
            metadata={
                "market_id": market.market_id,
                "slug": market.slug,
                "yes_price": prob,
                "volume_24h": market.volume_24h,
                "liquidity": market.liquidity,
                "category": market.matched_category,
                "end_date": market.end_date.isoformat() if market.end_date else None,
            },
        )

    def _infer_direction(self, question: str) -> SignalDirection | None:
        """根据问题文本推断 YES 对应黄金的方向."""
        q_lower = question.lower()

        for keywords, direction_fn in DIRECTION_RULES:
            if any(kw.lower() in q_lower for kw in keywords):
                return direction_fn(question)

        return None

    # ------------------------------------------------------------------
    # 聚合信号
    # ------------------------------------------------------------------

    def _aggregate_by_category(
        self, markets: list[PredictionMarket]
    ) -> list[Signal]:
        """按类别聚合多个市场的共识.

        如果同一类别内有多个市场同向，生成一个综合信号。
        """
        signals: list[Signal] = []
        from collections import defaultdict

        # 按类别分组
        by_cat: dict[str, list[tuple[PredictionMarket, SignalDirection]]] = defaultdict(list)
        for m in markets:
            direction = self._infer_direction(m.question)
            if direction and m.outcome_yes_price >= self.config.prob_threshold_moderate:
                by_cat[m.matched_category].append((m, direction))

        for cat, items in by_cat.items():
            bullish = [(m, d) for m, d in items if d == SignalDirection.BULLISH]
            bearish = [(m, d) for m, d in items if d == SignalDirection.BEARISH]

            # 生成综合信号（至少2个市场同向）
            if len(bullish) >= 2:
                avg_prob = sum(m.outcome_yes_price for m, _ in bullish) / len(bullish)
                total_vol = sum(m.volume_24h for m, _ in bullish)
                signals.append(self._build_aggregate_signal(
                    cat, SignalDirection.BULLISH, avg_prob, total_vol, len(bullish)
                ))

            if len(bearish) >= 2:
                avg_prob = sum(m.outcome_yes_price for m, _ in bearish) / len(bearish)
                total_vol = sum(m.volume_24h for m, _ in bearish)
                signals.append(self._build_aggregate_signal(
                    cat, SignalDirection.BEARISH, avg_prob, total_vol, len(bearish)
                ))

        return signals

    def _build_aggregate_signal(
        self,
        category: str,
        direction: SignalDirection,
        avg_prob: float,
        total_vol: float,
        count: int,
    ) -> Signal:
        category_label = {
            "macro": "宏观",
            "geopolitical": "地缘",
            "currency": "货币",
            "gold_direct": "金价",
            "policy": "政策",
        }.get(category, category)

        score = min(avg_prob * 0.8 + min(count * 0.05, 0.2), 1.0)
        if direction == SignalDirection.BEARISH:
            score = -score

        if avg_prob >= 0.70:
            strength = SignalStrength.STRONG
        elif avg_prob >= 0.55:
            strength = SignalStrength.MODERATE
        else:
            strength = SignalStrength.WEAK

        return Signal(
            name=f"Polymarket-{category_label}共识",
            dimension="polymarket",
            direction=direction,
            strength=strength,
            score=score,
            description=(
                f"Polymarket {category_label}类别中 {count} 个市场"
                f"一致预期黄金{'上涨' if direction == SignalDirection.BULLISH else '下跌'}，"
                f"平均 YES 概率 {avg_prob*100:.1f}%，"
                f"合计交易量 ${total_vol:,.0f}。"
            ),
            metadata={
                "aggregate": True,
                "category": category,
                "market_count": count,
                "avg_prob": avg_prob,
                "total_volume": total_vol,
            },
        )
