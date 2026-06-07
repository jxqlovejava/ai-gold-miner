"""投资军规检查器 — 运行规则并输出审查结果."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from gold_miner.doctrine.models import DoctrineResult, InvestmentRule, RuleViolation
from gold_miner.doctrine.rules import ALL_RULES


class DoctrineChecker:
    """投资军规检查器.

    用法:
        checker = DoctrineChecker()
        result = checker.check(decision, context)
        for v in result.blocks:
            print(f"BLOCKED: {v.message}")
    """

    def __init__(self, rules: list[InvestmentRule] | None = None) -> None:
        self.rules = rules or [r for r in ALL_RULES if r.enabled]

    def check(
        self,
        decision: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> DoctrineResult:
        """对所有启用的规则运行检查."""
        ctx = context or {}
        violations: list[RuleViolation] = []

        for rule in self.rules:
            checker_fn = getattr(self, rule.check_fn, None)
            if checker_fn is None:
                violations.append(RuleViolation(
                    rule=rule,
                    passed=True,
                    message=f"检查函数 {rule.check_fn} 未实现",
                ))
                continue

            try:
                v = checker_fn(decision, ctx)
                violations.append(v)
            except Exception as e:
                violations.append(RuleViolation(
                    rule=rule,
                    passed=True,
                    message=f"规则检查异常: {e}",
                ))

        blocks = [v for v in violations if not v.passed and v.rule.severity == "block"]
        warnings = [v for v in violations if not v.passed and v.rule.severity == "warn"]
        infos = [v for v in violations if not v.passed and v.rule.severity == "info"]
        failed = [v for v in violations if not v.passed]

        return DoctrineResult(
            violations=violations,
            blocks=blocks,
            warnings=warnings,
            infos=infos,
            passed_count=len(violations) - len(failed),
            failed_count=len(failed),
        )

    def apply_doctrine(
        self,
        decision: dict[str, Any],
        result: DoctrineResult,
    ) -> dict[str, Any]:
        """根据军规检查结果调整决策."""
        adjusted = dict(decision)

        if result.has_blocks:
            adjusted["position_pct"] = 0.0
            adjusted["direction"] = "neutral"
            block_names = [v.rule.name for v in result.blocks]
            adjusted["doctrine_override"] = f"军规阻断: {', '.join(block_names)}"
            return adjusted

        if result.warnings:
            warn_count = len(result.warnings)
            original = adjusted.get("position_pct", 0)
            adjusted["position_pct"] = round(original * max(0.5, 1 - warn_count * 0.25), 2)
            warn_names = [v.rule.name for v in result.warnings]
            adjusted["doctrine_override"] = f"军规警告({warn_count}项): {', '.join(warn_names)}"

        return adjusted

    # ------------------------------------------------------------------
    # 仓位管理
    # ------------------------------------------------------------------

    def check_position_limit(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_position_limit")
        position = decision.get("position_pct", 0)
        limit = 0.20
        passed = position <= limit
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=f"仓位 {position:.0%} {'≤' if passed else '>'} 上限 {limit:.0%}",
            details={"position_pct": position, "limit": limit},
        )

    def check_total_exposure(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_total_exposure")
        existing = ctx.get("current_exposure", 0)
        new_position = decision.get("position_pct", 0)
        total = existing + new_position
        limit = 0.80
        passed = total <= limit
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=f"总敞口 {total:.0%} (现有{existing:.0%}+新增{new_position:.0%}) {'≤' if passed else '>'} 上限 {limit:.0%}",
            details={"total_exposure": total, "limit": limit},
        )

    def check_gold_overweight(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_gold_overweight")
        gold_pct = ctx.get("gold_allocation_pct", 0)
        threshold = 0.50
        passed = gold_pct <= threshold
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=f"黄金占比 {gold_pct:.0%} {'正常' if passed else '过重，建议分散'}",
            details={"gold_pct": gold_pct, "threshold": threshold},
        )

    # ------------------------------------------------------------------
    # 时机选择
    # ------------------------------------------------------------------

    def check_pre_data_heavy(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_pre_data_heavy")
        near_data_event = ctx.get("near_data_event", False)
        position = decision.get("position_pct", 0)
        passed = not (near_data_event and position > 0.10)
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=(
                "无重大数据事件临近" if not near_data_event
                else f"重大数据前仓位{position:.0%} {'≤10%可接受' if position <= 0.10 else '>10%建议减仓'}"
            ),
            details={"near_data_event": near_data_event, "position_pct": position},
        )

    def check_no_chase(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_no_chase")
        daily_change_pct = ctx.get("daily_change_pct", 0)
        position = decision.get("position_pct", 0)
        is_chasing = abs(daily_change_pct) > 3.0 and position > 0.05
        passed = not is_chasing
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=(
                f"日波动 {daily_change_pct:+.1f}% {'正常' if abs(daily_change_pct) <= 3.0 else '剧烈'}，"
                f"{'未追涨杀跌' if passed else '不建议在此波动下新建仓'}"
            ),
            details={"daily_change_pct": daily_change_pct, "position_pct": position},
        )

    def check_friday_exposure(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_friday_exposure")
        is_friday = datetime.now().weekday() == 4
        position = decision.get("position_pct", 0)
        passed = not (is_friday and position > 0.50)
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=(
                "非周五，无需特别减仓" if not is_friday
                else f"周五仓位{position:.0%} {'≤50%安全' if position <= 0.50 else '>50%建议减仓避周末风险'}"
            ),
            details={"is_friday": is_friday, "position_pct": position},
        )

    def check_holiday_exposure(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_holiday_exposure")
        near_holiday = ctx.get("near_holiday", False)
        position = decision.get("position_pct", 0)
        passed = not (near_holiday and position > 0.40)
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=(
                "无长假临近" if not near_holiday
                else f"长假前仓位{position:.0%} {'≤40%安全' if position <= 0.40 else '>40%建议减仓'}"
            ),
            details={"near_holiday": near_holiday, "position_pct": position},
        )

    # ------------------------------------------------------------------
    # 情绪纪律
    # ------------------------------------------------------------------

    def check_consecutive_stops(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_consecutive_stops")
        consecutive_stops = ctx.get("consecutive_stops", 0)
        position = decision.get("position_pct", 0)
        passed = not (consecutive_stops >= 3 and position > 0)
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=(
                f"连续止损 {consecutive_stops} 次，"
                f"{'未达休整阈值' if consecutive_stops < 3 else '强制休整3个交易日，不开新仓'}"
            ),
            details={"consecutive_stops": consecutive_stops},
        )

    def check_extreme_sentiment(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_extreme_sentiment")
        vix = ctx.get("vix", 0)
        fear_greed = ctx.get("fear_greed_index", 50)
        position = decision.get("position_pct", 0)
        is_extreme = vix > 40 or fear_greed > 90 or fear_greed < 10
        passed = not (is_extreme and position > 0.10)
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=(
                "市场情绪正常"
                if not is_extreme
                else f"情绪极端 (VIX={vix}, FG={fear_greed})，{'仓位可控' if position <= 0.10 else '建议暂缓新开仓'}"
            ),
            details={"vix": vix, "fear_greed_index": fear_greed},
        )

    def check_trailing_stop(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_trailing_stop")
        unrealized_pnl_pct = ctx.get("unrealized_pnl_pct", 0)
        has_trailing_stop = ctx.get("has_trailing_stop", False)
        # 浮盈>20%时检查是否上了移动止损
        passed = not (unrealized_pnl_pct > 0.20 and not has_trailing_stop)
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=(
                f"浮盈 {unrealized_pnl_pct:.0%}，"
                f"{'已上移止损 ✓' if has_trailing_stop else '必须上移止损至成本价以上！' if unrealized_pnl_pct > 0.20 else '未达强制上移阈值'}"
            ),
        )

    def check_one_sided_signals(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_one_sided_signals")
        bull_count = ctx.get("bullish_signal_count", 0)
        bear_count = ctx.get("bearish_signal_count", 0)
        total = bull_count + bear_count
        if total == 0:
            return RuleViolation(rule=rule, passed=True, message="无足够信号数据")
        bull_ratio = bull_count / total
        is_one_sided = bull_ratio >= 0.80 or bull_ratio <= 0.20
        passed = not is_one_sided
        direction = "看涨" if bull_ratio >= 0.80 else "看跌" if bull_ratio <= 0.20 else "均衡"
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=f"信号方向分布: {bull_count}看涨/{bear_count}看跌 ({bull_ratio:.0%}看涨)，{direction}{'，警惕反转' if is_one_sided else '，分布正常'}",
            details={"bull_count": bull_count, "bear_count": bear_count, "bull_ratio": bull_ratio},
        )

    # ------------------------------------------------------------------
    # 流程纪律
    # ------------------------------------------------------------------

    def check_multi_dimension(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_multi_dimension")
        active_dims = ctx.get("active_dimensions", [])
        passed = len(active_dims) >= 2
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=(
                f"活跃维度: {len(active_dims)}个 ({', '.join(active_dims) if active_dims else '无'})，"
                f"{'满足≥2要求' if passed else '不足2个维度，信号可靠性低'}"
            ),
            details={"active_dimensions": active_dims},
        )

    def check_conflict_cautious(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_conflict_cautious")
        bull_conf = ctx.get("bull_confidence", 0)
        bear_conf = ctx.get("bear_confidence", 0)
        position = decision.get("position_pct", 0)
        is_conflict = bull_conf > 0.6 and bear_conf > 0.6
        passed = not (is_conflict and position > 0.20)
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=(
                f"多头信心{bull_conf:.0%} 空头信心{bear_conf:.0%}，"
                f"{'多空分歧大，建议观望或小仓' if is_conflict else '分歧正常'}"
            ),
            details={"bull_confidence": bull_conf, "bear_confidence": bear_conf},
        )

    def check_stop_loss_set(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_stop_loss_set")
        has_stop = ctx.get("stop_loss_set", False)
        # 如果方向是neutral/观望，不强制要求止损
        direction = decision.get("direction", "neutral")
        if direction == "neutral" and decision.get("position_pct", 0) == 0:
            return RuleViolation(rule=rule, passed=True, message="当前观望，无需止损")
        passed = has_stop
        return RuleViolation(
            rule=rule,
            passed=passed,
            message="已设置止损 ✓" if passed else "未设置止损！必须预设止损再开仓",
            details={"has_stop": has_stop},
        )

    def check_decision_record(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_decision_record")
        has_record = ctx.get("has_decision_record", False)
        return RuleViolation(
            rule=rule,
            passed=True,  # 信息级别，不阻断
            message="建议记录本次决策理由与预期" if not has_record else "已有决策记录 ✓",
            details={"has_record": has_record},
        )

    # ------------------------------------------------------------------
    # helper
    # ------------------------------------------------------------------

    def _get_rule(self, check_fn: str) -> InvestmentRule:
        for r in ALL_RULES:
            if r.check_fn == check_fn:
                return r
        return InvestmentRule(
            id="unknown",
            name="Unknown",
            description="",
            severity="info",
            category="unknown",
            check_fn=check_fn,
        )
