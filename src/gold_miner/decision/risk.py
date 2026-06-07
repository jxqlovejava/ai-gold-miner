"""风控审查模块."""

from dataclasses import dataclass
from typing import Any


@dataclass
class RiskCheck:
    name: str
    passed: bool
    message: str
    severity: str


class RiskManager:
    def __init__(
        self,
        max_position_pct: float = 0.8,
        max_single_loss_pct: float = 0.03,
        max_drawdown_pct: float = 0.15,
    ) -> None:
        self.max_position_pct = max_position_pct
        self.max_single_loss_pct = max_single_loss_pct
        self.max_drawdown_pct = max_drawdown_pct

    def check(
        self,
        decision: dict[str, Any],
        current_position_pct: float = 0.0,
        portfolio_value: float = 0.0,
    ) -> list[RiskCheck]:
        checks: list[RiskCheck] = []

        new_position = decision.get("position_pct", 0)
        if new_position > self.max_position_pct:
            checks.append(RiskCheck(
                name="仓位上限", passed=False,
                message=f"建议仓位 {new_position:.0%} 超过上限 {self.max_position_pct:.0%}",
                severity="block",
            ))
        else:
            checks.append(RiskCheck(
                name="仓位上限", passed=True,
                message=f"仓位 {new_position:.0%} 在允许范围内",
                severity="info",
            ))

        total = current_position_pct + new_position
        if total > self.max_position_pct:
            checks.append(RiskCheck(
                name="集中度风险", passed=False,
                message=f"总仓位 {total:.0%} 超过上限",
                severity="warn",
            ))
        else:
            checks.append(RiskCheck(name="集中度风险", passed=True, message="集中度正常", severity="info"))

        bull_conf = decision.get("bull_confidence", 0)
        bear_conf = decision.get("bear_confidence", 0)
        if bull_conf > 0.5 and bear_conf > 0.5:
            checks.append(RiskCheck(
                name="多空冲突", passed=False,
                message="多空Agent均高置信度，存在方向冲突，建议观望",
                severity="warn",
            ))
        else:
            checks.append(RiskCheck(name="多空冲突", passed=True, message="多空观点不冲突", severity="info"))

        composite = decision.get("composite_score", 0)
        if abs(composite) > 0.9:
            checks.append(RiskCheck(
                name="极端信号", passed=True,
                message="信号极强，需警惕反转风险，建议分批操作",
                severity="warn",
            ))

        return checks

    def apply_risk_controls(self, decision: dict[str, Any], checks: list[RiskCheck]) -> dict[str, Any]:
        adjusted = dict(decision)

        if any(c.severity == "block" and not c.passed for c in checks):
            adjusted["position_pct"] = 0.0
            adjusted["direction"] = "neutral"
            adjusted["risk_override"] = "风控拦截：存在阻断性风险"
            return adjusted

        warn_count = sum(1 for c in checks if c.severity == "warn" and not c.passed)
        if warn_count > 0:
            original = adjusted.get("position_pct", 0)
            adjusted["position_pct"] = round(original * (1 - warn_count * 0.3), 2)
            adjusted["risk_override"] = f"风控降仓：{warn_count}项警告"

        if "strategy_objective" in decision:
            strategy_checks = self.check_strategy(decision)
            adjusted = self._apply_with_checks(adjusted, strategy_checks)

        return adjusted

    def check_strategy(self, decision: dict[str, Any]) -> list[RiskCheck]:
        """策略目标相关的风控检查."""
        checks: list[RiskCheck] = []
        obj = decision.get("strategy_objective", "")
        pos = decision.get("position_pct", 0)

        if obj == "cost_recovery" and pos > 0.5:
            checks.append(RiskCheck(
                name="回本策略仓位", passed=False,
                message="回本优先策略下仓位不应超过50%",
                severity="warn",
            ))

        if obj == "take_profit" and pos > 0.7:
            checks.append(RiskCheck(
                name="落袋策略仓位", passed=False,
                message="落袋为安策略下仓位不应超过70%",
                severity="warn",
            ))

        return checks

    @staticmethod
    def _apply_with_checks(decision: dict[str, Any], checks: list[RiskCheck]) -> dict[str, Any]:
        adjusted = dict(decision)
        if any(c.severity == "block" and not c.passed for c in checks):
            adjusted["position_pct"] = 0.0
            adjusted["direction"] = "neutral"
            adjusted["risk_override"] = "风控拦截：存在阻断性风险"
            return adjusted

        warn_count = sum(1 for c in checks if c.severity == "warn" and not c.passed)
        if warn_count > 0:
            original = adjusted.get("position_pct", 0)
            adjusted["position_pct"] = round(original * (1 - warn_count * 0.3), 2)
            adjusted["risk_override"] = f"风控降仓：{warn_count}项警告"

        return adjusted
