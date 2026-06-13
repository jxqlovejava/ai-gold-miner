"""极端情景预判与防御 — 提前应对黑天鹅/灰犀牛.

核心逻辑:
  1. 预设极端情景库
  2. 结合 ScenarioAnalyzer 评估当前组合在各情景下的表现
  3. 计算准备度，给出防御性配置建议
  4. 定期生成"极端情景体检报告"

使用方式:
    guard = ExtremeGuard()
    report = guard.stress_test()          # 全面压力测试
    report = guard.check_scenario("war")  # 特定情景检查
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from gold_miner.advisor.core import AdvisorReport, AlertLevel, ExtremeStressTest
from gold_miner.scenarios.analyzer import ScenarioAnalyzer
from gold_miner.scenarios.models import ScenarioReport


# ---------------------------------------------------------------------------
# 极端情景库
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExtremeScenario:
    """预设极端情景."""
    name: str
    description: str
    trigger_keywords: list[str]
    typical_drawdown_pct: float
    typical_duration_days: int
    probability_base: float       # 基础概率估计
    hedge_suggestions: list[str]


EXTREME_SCENARIOS: list[ExtremeScenario] = [
    ExtremeScenario(
        name="地缘战争升级",
        description="大规模军事冲突爆发，避险情绪急剧升温",
        trigger_keywords=["战争", "冲突", "导弹", "军事", "地缘"],
        typical_drawdown_pct=-0.08,
        typical_duration_days=30,
        probability_base=0.15,
        hedge_suggestions=["增持实物黄金", "买入黄金看涨期权", "减少股票敞口"],
    ),
    ExtremeScenario(
        name="美元信用危机",
        description="美元大幅贬值，全球储备货币地位动摇",
        trigger_keywords=["美元", "贬值", "债务", "违约", "信用评级"],
        typical_drawdown_pct=-0.05,  # 对黄金是利好，但对其他资产是利空
        typical_duration_days=60,
        probability_base=0.10,
        hedge_suggestions=["增持黄金至60%+", "配置非美货币", "买入黄金矿业股"],
    ),
    ExtremeScenario(
        name="流动性危机",
        description="信贷紧缩，被迫抛售一切资产换取流动性",
        trigger_keywords=["流动性", "抛售", "危机", "雷曼", "倒闭"],
        typical_drawdown_pct=-0.15,
        typical_duration_days=20,
        probability_base=0.08,
        hedge_suggestions=["保留30%+现金", "降低杠杆", "暂停新开仓"],
    ),
    ExtremeScenario(
        name="央行大规模抛售黄金",
        description="主要央行联合抛售黄金储备",
        trigger_keywords=["央行", "抛售", "储备", "IMF"],
        typical_drawdown_pct=-0.20,
        typical_duration_days=45,
        probability_base=0.03,
        hedge_suggestions=["立即减仓至20%以下", "设置移动止损", "关注央行公告"],
    ),
    ExtremeScenario(
        name="通胀失控",
        description="恶性通胀爆发，实际利率飙升",
        trigger_keywords=["通胀", "CPI", "恶性", "物价"],
        typical_drawdown_pct=-0.10,
        typical_duration_days=90,
        probability_base=0.12,
        hedge_suggestions=["黄金对冲通胀有效，但注意实际利率", "配置TIPS", "保留实物黄金"],
    ),
    ExtremeScenario(
        name="科技股崩盘引发的连锁反应",
        description="AI泡沫破裂，风险资产全面下跌",
        trigger_keywords=["科技", "泡沫", "AI", "崩盘", "纳斯达克"],
        typical_drawdown_pct=-0.12,
        typical_duration_days=40,
        probability_base=0.18,
        hedge_suggestions=["黄金避险属性显现，可逆势加仓", "减少风险资产", "增加现金"],
    ),
]


class ExtremeGuard:
    """极端情景防御守卫.

    定期运行压力测试，评估当前组合在各极端情景下的脆弱性.
    """

    def __init__(self, scenario_analyzer: ScenarioAnalyzer | None = None) -> None:
        self.analyzer = scenario_analyzer or ScenarioAnalyzer()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def stress_test(
        self,
        current_position_pct: float = 0.0,
    ) -> AdvisorReport:
        """运行全面压力测试.

        Args:
            current_position_pct: 当前黄金仓位比例

        Returns:
            AdvisorReport，report_type="extreme"
        """
        logger.info("[ExtremeGuard] 运行极端情景压力测试...")

        stress_tests: list[ExtremeStressTest] = []
        for scenario in EXTREME_SCENARIOS:
            test = self._run_scenario(scenario, current_position_pct)
            stress_tests.append(test)

        # 按危害程度排序
        stress_tests.sort(key=lambda t: t.max_drawdown_pct, reverse=False)

        # 计算总体准备度
        avg_preparedness = round(
            sum(t.preparedness_score for t in stress_tests) / len(stress_tests), 2
        ) if stress_tests else 0.0

        # 生成预警
        warnings = []
        vulnerable = [t for t in stress_tests if t.max_drawdown_pct < -0.10]
        if vulnerable:
            names = ", ".join(t.scenario_name for t in vulnerable[:3])
            warnings.append(
                f"⚠️ 当前仓位在以下情景中可能承受>10%回撤: {names}"
            )

        if avg_preparedness < 0.5:
            warnings.append(
                f"🔴 整体准备度仅{avg_preparedness:.0%}，建议加强防御配置"
            )

        return AdvisorReport(
            report_type="extreme",
            stress_tests=stress_tests,
            confidence=0.6,
            sources=["ScenarioAnalyzer", "HistoricalAnalog", "ExtremeScenarioDB"],
            warnings=warnings,
        )

    def check_scenario(self, keyword: str, current_position_pct: float = 0.0) -> AdvisorReport:
        """检查特定关键词相关的极端情景.

        Args:
            keyword: 情景关键词，如 "war", "inflation"
            current_position_pct: 当前仓位

        Returns:
            匹配的情景压力测试结果
        """
        keyword_lower = keyword.lower()
        matched = [
            s for s in EXTREME_SCENARIOS
            if keyword_lower in s.name.lower()
            or any(keyword_lower in k.lower() for k in s.trigger_keywords)
        ]

        if not matched:
            return AdvisorReport(
                report_type="extreme",
                confidence=1.0,
                warnings=[f"未找到与 '{keyword}' 匹配的极端情景"],
            )

        stress_tests = [self._run_scenario(s, current_position_pct) for s in matched]

        return AdvisorReport(
            report_type="extreme",
            stress_tests=stress_tests,
            confidence=0.6,
            sources=["ExtremeScenarioDB"],
            warnings=[
                f"🔍 情景 '{keyword}' 压力测试完成，"
                f"最大潜在回撤 {min(t.max_drawdown_pct for t in stress_tests):.1%}"
            ],
        )

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _run_scenario(
        self,
        scenario: ExtremeScenario,
        current_position_pct: float,
    ) -> ExtremeStressTest:
        """对单个情景运行压力测试."""
        # 调整回撤：仓位越高，回撤越大
        adjusted_drawdown = scenario.typical_drawdown_pct * current_position_pct

        # 计算准备度：现金比例越高，准备度越高
        cash_pct = 1.0 - current_position_pct
        preparedness = min(cash_pct + 0.2, 1.0)  # 保留一些基础分

        # 如果有对冲建议，稍微提高准备度
        if scenario.hedge_suggestions:
            preparedness = min(preparedness + 0.1, 1.0)

        # 动态调整概率（基于近期新闻/事件）
        probability = self._adjust_probability(scenario)

        return ExtremeStressTest(
            scenario_name=scenario.name,
            probability_estimate=probability,
            max_drawdown_pct=round(adjusted_drawdown, 3),
            impact_duration_days=scenario.typical_duration_days,
            hedge_recommendation="; ".join(scenario.hedge_suggestions[:3]),
            preparedness_score=round(preparedness, 2),
        )

    def _adjust_probability(self, scenario: ExtremeScenario) -> float:
        """根据当前环境动态调整情景概率."""
        base = scenario.probability_base

        # 尝试用 ScenarioAnalyzer 获取更精确的评估
        try:
            report = self.analyzer.analyze(
                scenario_description=scenario.description,
                target_asset="黄金",
                time_horizon="1个月",
            )
            if report and report.probability is not None:
                # 融合两种估计
                return round((base + report.probability) / 2, 2)
        except Exception as e:
            logger.debug(f"ScenarioAnalyzer 调用失败，使用基础概率: {e}")

        return round(base, 2)
