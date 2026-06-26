"""中长期情景矩阵 — 牛市/基准/熊市三情景概率与金价区间.

复用现有 ScenarioAnalyzer 对每个情景做影响推演，再基于当前长期信号调整概率。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from gold_miner.scenarios.analyzer import ScenarioAnalyzer
from gold_miner.scenarios.models import ScenarioReport
from gold_miner.signals.base import Signal, SignalBundle, SignalDirection, SignalStrength


@dataclass
class ScenarioEstimate:
    """单情景估计."""

    name: str
    description: str
    probability_pct: float
    gold_change_pct: float
    gold_low: float
    gold_high: float
    reasoning: str = ""
    report: ScenarioReport | None = None


@dataclass
class ScenarioMatrix:
    """三情景矩阵."""

    base_price: float = 0.0
    horizon_months: int = 12
    scenarios: list[ScenarioEstimate] = field(default_factory=list)
    weighted_expected_change_pct: float = 0.0
    expected_price: float = 0.0


class LongTermScenarioSignal:
    """中长期情景矩阵信号生成器."""

    SCENARIOS: dict[str, str] = {
        "bull": (
            "基准牛市情景：美联储进入降息周期，美国财政赤字持续扩大，"
            "去美元化加速推动央行购金，地缘风险维持高位。"
        ),
        "base": (
            "基准情景：美国经济软着陆，美联储保持限制性利率更久但不再加息，"
            "通胀缓慢回落，央行购金维持稳健，金价区间震荡上行。"
        ),
        "bear": (
            "基准熊市情景：美国经济强劲复苏，美联储重新加息或维持高利率更久，"
            "实际利率大幅上行，美元走强，风险资产吸引力上升，黄金承压。"
        ),
        "extreme_up": (
            "极端上行情景：美元信用遭遇历史性质疑（美联储财政货币化/美债拍卖危机/主权评级下调），"
            "或全球地缘冲突全面升级导致能源与供应链断裂，全球央行和主权基金恐慌性增持黄金，"
            "去美元化从渐进转为加速。"
        ),
        "extreme_down": (
            "极端下行情景：美国经济强劲复苏且通胀粘性迫使美联储连续加息，实际利率大幅上行；"
            "同时全球风险偏好全面回升、地缘紧张显著缓和、美元趋势性走强，黄金遭遇流动性紧缩与机会成本双重打击。"
        ),
    }

    def __init__(self, analyzer: ScenarioAnalyzer | None = None) -> None:
        self.analyzer = analyzer or ScenarioAnalyzer()

    def generate_matrix(
        self,
        base_price: float,
        horizon_months: int = 12,
        context: dict[str, Any] | None = None,
    ) -> ScenarioMatrix:
        """生成三情景矩阵."""
        context = context or {}
        context["spot_gold"] = base_price

        estimates: list[ScenarioEstimate] = []
        for key, description in self.SCENARIOS.items():
            try:
                report = self.analyzer.analyze(
                    scenario_description=description,
                    time_horizon_months=horizon_months,
                    context=context,
                )
                pi = report.price_impact
                if pi is None:
                    raise ValueError("情景报告缺少 price_impact")
                estimates.append(ScenarioEstimate(
                    name=key,
                    description=description,
                    probability_pct=0.0,  # 后续基于信号调整
                    gold_change_pct=pi.base_case_change_pct,
                    gold_low=base_price * (1 + pi.bearish_case_change_pct / 100),
                    gold_high=base_price * (1 + pi.bullish_case_change_pct / 100),
                    reasoning=pi.reasoning,
                    report=report,
                ))
            except Exception as e:
                logger.warning(f"情景 {key} 分析失败: {e}")
                estimates.append(self._fallback_estimate(key, description, base_price))

        matrix = ScenarioMatrix(
            base_price=base_price,
            horizon_months=horizon_months,
            scenarios=estimates,
        )
        return matrix

    def generate_signals(
        self,
        base_price: float,
        horizon_months: int = 12,
        context: dict[str, Any] | None = None,
        existing_bundle: SignalBundle | None = None,
    ) -> tuple[list[Signal], ScenarioMatrix]:
        """生成情景信号并返回矩阵."""
        matrix = self.generate_matrix(base_price, horizon_months, context)

        # 基于现有长期信号调整概率
        matrix = self._apply_probabilities(matrix, existing_bundle)

        signals: list[Signal] = []
        if not matrix.scenarios:
            return signals, matrix

        # 生成综合情景信号
        expected_change = matrix.weighted_expected_change_pct
        if expected_change > 5:
            direction = SignalDirection.BULLISH
            strength = SignalStrength.STRONG if expected_change > 10 else SignalStrength.MODERATE
            score = min(expected_change / 20, 0.6)
        elif expected_change > 1:
            direction = SignalDirection.BULLISH
            strength = SignalStrength.WEAK
            score = expected_change / 20
        elif expected_change < -5:
            direction = SignalDirection.BEARISH
            strength = SignalStrength.MODERATE
            score = max(expected_change / 20, -0.5)
        elif expected_change < -1:
            direction = SignalDirection.BEARISH
            strength = SignalStrength.WEAK
            score = max(expected_change / 20, -0.25)
        else:
            direction = SignalDirection.NEUTRAL
            strength = SignalStrength.WEAK
            score = 0.0

        prob_str = ", ".join(
            f"{s.name} {s.probability_pct:.0f}%" for s in matrix.scenarios
        )
        signals.append(Signal(
            name=f"{horizon_months}个月情景矩阵预期",
            dimension="long_term",
            direction=direction,
            strength=strength,
            score=round(score, 2),
            description=(
                f"五情景加权预期金价变动 {expected_change:+.1f}%，"
                f"预期价格 ${matrix.expected_price:,.0f}。"
                f"{prob_str}"
            ),
            metadata={
                "source": "scenario_matrix",
                "horizon_months": horizon_months,
                "expected_change_pct": expected_change,
                "expected_price": matrix.expected_price,
                "probabilities": {
                    s.name: s.probability_pct for s in matrix.scenarios
                },
            },
        ))

        return signals, matrix

    def _apply_probabilities(
        self,
        matrix: ScenarioMatrix,
        bundle: SignalBundle | None,
    ) -> ScenarioMatrix:
        """基于现有长期信号调整五情景概率.

        极端上下行各保留 10% 基础尾部位，剩余 80% 按牛/熊信号强度分配。
        """
        if bundle is None:
            # 无信号时：等权重牛/熊，基准占剩余，极端各占 10%
            probs = {
                "bull": 20,
                "base": 40,
                "bear": 20,
                "extreme_up": 10,
                "extreme_down": 10,
            }
        else:
            long_term_signals = bundle.by_dimension("long_term")
            bullish_score = sum(s.score for s in long_term_signals if s.direction == SignalDirection.BULLISH)
            bearish_score = abs(sum(s.score for s in long_term_signals if s.direction == SignalDirection.BEARISH))
            total = bullish_score + bearish_score

            if total < 0.1:
                probs = {
                    "bull": 20,
                    "base": 40,
                    "bear": 20,
                    "extreme_up": 10,
                    "extreme_down": 10,
                }
            else:
                bull_ratio = bullish_score / total
                bear_ratio = bearish_score / total
                # 普通牛/熊在 15-35% 之间，基准占剩余，极端各占 10%
                bull_pct = round(15 + bull_ratio * 20)
                bear_pct = round(15 + bear_ratio * 20)
                base_pct = 100 - bull_pct - bear_pct - 10 - 10

                # 兜底：基准概率不能为负
                if base_pct < 0:
                    base_pct = 0
                    total_bb = bull_pct + bear_pct
                    if total_bb > 0:
                        scale = 80 / total_bb  # 普通牛熊合计最多 80%
                        bull_pct = round(bull_pct * scale)
                        bear_pct = 80 - bull_pct

                prob_total = bull_pct + bear_pct + base_pct + 10 + 10
                if prob_total != 100:
                    base_pct += 100 - prob_total

                probs = {
                    "bull": bull_pct,
                    "base": base_pct,
                    "bear": bear_pct,
                    "extreme_up": 10,
                    "extreme_down": 10,
                }

        for s in matrix.scenarios:
            s.probability_pct = probs.get(s.name, 20)

        # 计算加权预期
        weighted = sum(
            s.probability_pct / 100 * s.gold_change_pct
            for s in matrix.scenarios
        )
        matrix.weighted_expected_change_pct = weighted
        matrix.expected_price = matrix.base_price * (1 + weighted / 100)

        return matrix

    def _fallback_estimate(
        self,
        name: str,
        description: str,
        base_price: float,
    ) -> ScenarioEstimate:
        """LLM 不可用时返回保守 fallback 估计."""
        fallbacks = {
            "bull": (15.0, 1.05, 1.25),
            "base": (5.0, 0.95, 1.10),
            "bear": (-10.0, 0.85, 0.98),
            "extreme_up": (40.0, 1.20, 1.80),
            "extreme_down": (-25.0, 0.60, 0.90),
        }
        change, low_mult, high_mult = fallbacks.get(name, (0.0, 0.95, 1.05))
        return ScenarioEstimate(
            name=name,
            description=description,
            probability_pct=33,
            gold_change_pct=change,
            gold_low=base_price * low_mult,
            gold_high=base_price * high_mult,
            reasoning="LLM未配置，使用历史经验区间 fallback",
        )
