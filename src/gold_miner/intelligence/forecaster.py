"""价格预判生成器 — 基于文章分析推演黄金走势."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from gold_miner.intelligence.analyzer import ArticleAnalysis


@dataclass
class PriceForecast:
    """价格走势预判."""

    direction: str  # bullish / bearish / neutral
    confidence: float  # 0.0 ~ 1.0
    horizon_days: int  # 预判时间窗口（天）
    target_change_pct: float  # 预期涨跌幅
    reasoning: str = ""
    risk_factors: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    valid_until: datetime | None = None


class PriceForecaster:
    """基于文章分析生成价格走势预判."""

    def forecast(
        self,
        analysis: ArticleAnalysis,
        llm_analysis: dict[str, Any] | None = None,
        cross_ref: dict[str, Any] | None = None,
    ) -> PriceForecast:
        """综合规则分析、LLM分析和交叉验证，生成预判."""
        direction = analysis.sentiment_direction
        confidence = abs(analysis.sentiment_score)

        # 调整方向（LLM 可能覆盖规则判断）
        if llm_analysis and llm_analysis.get("sentiment"):
            llm_dir = llm_analysis["sentiment"]
            if llm_dir in ("bullish", "bearish", "neutral"):
                direction = llm_dir
                confidence = max(confidence, float(llm_analysis.get("confidence", 0.5)))

        # 交叉验证调整置信度
        if cross_ref:
            confirming = len(cross_ref.get("confirming", []))
            contradicting = len(cross_ref.get("contradicting", []))
            if confirming + contradicting > 0:
                agreement_ratio = confirming / (confirming + contradicting)
                # 交叉验证调整置信度：一致升高，矛盾降低
                confidence = confidence * (0.5 + 0.5 * agreement_ratio)

        # 操纵话术惩罚
        if analysis.is_suspicious:
            confidence *= 0.6

        # 计算预期涨跌幅
        target_change_pct = self._estimate_change(analysis, direction)

        # 时间窗口
        horizon_days = self._determine_horizon(analysis, llm_analysis)

        # 推理链
        reasoning = self._build_reasoning(analysis, llm_analysis, cross_ref)

        # 风险因子
        risk_factors = self._extract_risk_factors(analysis)

        return PriceForecast(
            direction=direction,
            confidence=round(min(confidence, 1.0), 2),
            horizon_days=horizon_days,
            target_change_pct=target_change_pct,
            reasoning=reasoning,
            risk_factors=risk_factors,
            valid_until=datetime.now() + timedelta(days=horizon_days),
        )

    def _estimate_change(
        self, analysis: ArticleAnalysis, direction: str
    ) -> float:
        """估算预期涨跌幅."""
        base = abs(analysis.sentiment_score) * 0.03  # 3% max for score=1.0

        # 极端信号放大
        if analysis.bullish_count + analysis.bearish_count > 10:
            base *= 1.5

        return round(base if direction == "bullish" else -base, 4)

    def _determine_horizon(
        self,
        analysis: ArticleAnalysis,
        llm_analysis: dict[str, Any] | None = None,
    ) -> int:
        """确定预判时间窗口."""
        if llm_analysis and llm_analysis.get("horizon_days"):
            return int(llm_analysis["horizon_days"])

        # 规则：情感越强，窗口越短（短期催化剂）
        total_emotional = analysis.bullish_count + analysis.bearish_count
        if total_emotional > 15:
            return 3  # 强情绪文章 → 短期影响
        if total_emotional > 8:
            return 7
        return 14  # 温和文章 → 中期视角

    def _build_reasoning(
        self,
        analysis: ArticleAnalysis,
        llm_analysis: dict[str, Any] | None = None,
        cross_ref: dict[str, Any] | None = None,
    ) -> str:
        parts: list[str] = []

        parts.append(f"规则分析: {analysis.summary}")

        if llm_analysis and llm_analysis.get("reasoning"):
            parts.append(f"LLM分析: {llm_analysis['reasoning']}")

        if cross_ref:
            confirming = cross_ref.get("confirming", [])
            contradicting = cross_ref.get("contradicting", [])
            parts.append(
                f"交叉验证: {len(confirming)}条一致 / {len(contradicting)}条矛盾"
            )

        return " | ".join(parts)

    def _extract_risk_factors(self, analysis: ArticleAnalysis) -> list[str]:
        factors: list[str] = []
        if analysis.is_suspicious:
            factors.append("文章来源可信度存疑，预判置信度已降权")
            factors.extend(analysis.manipulation_flags[:3])

        if analysis.word_count < 200:
            factors.append("文章过短，信息量不足")

        if analysis.bullish_count + analysis.bearish_count < 3:
            factors.append("情感信号不足，预判偏差可能较大")

        return factors
