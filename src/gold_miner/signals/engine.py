"""多因子打分引擎."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from gold_miner.signals.base import SignalBundle, SignalStrength


@dataclass
class DimensionWeights:
    """各维度权重配置（维度间加权）.

    维度内信号级别加权由 ScoringEngine._strength_weight() 处理：
    STRONG=3x, MODERATE=2x, WEAK=1x — 确保央行购金、COT大减仓等
    决定性信号不会被同维度弱信号（如印度关税调整）稀释。
    """

    technical: float = 0.18
    fundamental: float = 0.22
    news: float = 0.18
    sentiment: float = 0.12
    event: float = 0.10
    polymarket: float = 0.05
    anomaly: float = 0.05
    scenario: float = 0.10

    def __post_init__(self) -> None:
        total = (
            self.technical + self.fundamental + self.news
            + self.sentiment + self.event + self.polymarket
            + self.anomaly + self.scenario
        )
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"权重之和必须等于1，当前={total}")


class ScoringEngine:
    """多因子综合评分引擎.

    两层加权：
    1. 维度内 — 按信号强度 (STRONG=3x, MODERATE=2x, WEAK=1x) 加权平均
    2. 维度间 — 按 DimensionWeights 各维度权重合成总分
    """

    DEFAULT_WEIGHTS = DimensionWeights()

    # 信号强度 → 维度内权重
    _STRENGTH_MAP: dict[SignalStrength, float] = {
        SignalStrength.STRONG: 3.0,
        SignalStrength.MODERATE: 2.0,
        SignalStrength.WEAK: 1.0,
    }

    def __init__(self, weights: DimensionWeights | None = None) -> None:
        self.weights = weights or self.DEFAULT_WEIGHTS

    def score(self, bundle: SignalBundle) -> SignalBundle:
        if not bundle.signals:
            bundle.composite_score = 0.0
            bundle.confidence = 0.0
            return bundle

        # 维度内加权平均 — 按信号强度加权
        dim_weighted_sum: dict[str, float] = {}
        dim_weight_total: dict[str, float] = {}
        for signal in bundle.signals:
            dim = signal.dimension
            w = self._STRENGTH_MAP.get(signal.strength, 1.0)
            dim_weighted_sum[dim] = dim_weighted_sum.get(dim, 0.0) + signal.score * w
            dim_weight_total[dim] = dim_weight_total.get(dim, 0.0) + w

        dim_avg: dict[str, float] = {}
        for dim in dim_weighted_sum:
            if dim_weight_total[dim] > 0:
                dim_avg[dim] = dim_weighted_sum[dim] / dim_weight_total[dim]

        composite = 0.0
        weight_used = 0.0
        for dim, weight in [
            ("technical", self.weights.technical),
            ("fundamental", self.weights.fundamental),
            ("news", self.weights.news),
            ("sentiment", self.weights.sentiment),
            ("event", self.weights.event),
            ("polymarket", self.weights.polymarket),
            ("anomaly", self.weights.anomaly),
            ("scenario", self.weights.scenario),
        ]:
            if dim in dim_avg:
                composite += dim_avg[dim] * weight
                weight_used += weight

        if weight_used > 0 and weight_used < 1.0:
            composite /= weight_used

        signal_count = len(bundle.signals)
        max_expected = 12
        confidence = min(signal_count / max_expected, 1.0)

        bull_count = bundle.bullish_count()
        bear_count = bundle.bearish_count()
        if bull_count + bear_count > 0:
            alignment = max(bull_count, bear_count) / (bull_count + bear_count)
            confidence = confidence * 0.5 + alignment * 0.5

        bundle.composite_score = max(-1.0, min(1.0, composite))
        bundle.confidence = confidence
        return bundle

    def apply_anomaly_adjustments(
        self,
        bundle: SignalBundle,
        anomaly_reports: list[Any] | None = None,
    ) -> SignalBundle:
        """对异常信号降低置信度.

        Feature 1 (异常检测) 挂载点。
        """
        if not anomaly_reports:
            return bundle

        from gold_miner.signals.anomaly import AnomalyReport  # noqa: F811

        high_severity = sum(
            1 for r in anomaly_reports
            if isinstance(r, AnomalyReport) and r.severity == "high"
        )
        if high_severity > 0:
            bundle.confidence *= max(0.3, 1.0 - high_severity * 0.3)
            logger.info(f"异常检测: {high_severity}个高危异常，置信度降至 {bundle.confidence:.0%}")

        return bundle

    def recommend(
        self,
        bundle: SignalBundle,
        threshold_buy: float = 0.3,
        threshold_sell: float = -0.3,
    ) -> dict[str, str]:
        """用户可见动作建议（pipeline 展示层应优先用此结果，勿把弱分当 buy）.

        阈值（与 PortfolioManager.SCORE_THRESHOLD 对齐）：
        - buy:  composite_score >= 0.3 且 confidence > 0.4
        - sell: composite_score <= -0.3 且 confidence > 0.4
        - 其余: hold（含弱分 +0.07 等，不得展示为买入）

        注意：仅做多积存金场景下 sell 表示减仓/止盈参考，不是开空。
        持仓感知动作（加仓/持有/减仓）见 decision.position_state。
        """
        score = bundle.composite_score
        conf = bundle.confidence

        if score >= threshold_buy and conf > 0.4:
            return {
                "action": "buy",
                "reason": f"综合评分 {score:+.2f}，置信度 {conf:.0%}",
                "urgency": "high" if score > 0.6 else "medium",
            }

        if score <= threshold_sell and conf > 0.4:
            return {
                "action": "sell",
                "reason": f"综合评分 {score:+.2f}，置信度 {conf:.0%}",
                "urgency": "high" if score < -0.6 else "medium",
            }

        return {
            "action": "hold",
            "reason": f"评分 {score:+.2f} 未达阈值，或置信度 {conf:.0%} 不足",
            "urgency": "low",
        }
