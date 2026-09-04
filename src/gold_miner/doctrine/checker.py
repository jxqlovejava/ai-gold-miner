"""投资军规检查器 — 运行规则并输出审查结果."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from gold_miner.doctrine.models import DoctrineResult, InvestmentRule, RuleViolation
from gold_miner.doctrine.rules import ALL_RULES

# 卖出类动作: position_pct 语义为「减仓比例」(0.5=减仓一半), 而非「目标仓位」.
# 与 position_state.resolve_position_state 的 reduce/stop 分支对齐.
SELL_ACTIONS = frozenset({
    "sell", "reduce", "reduce_half", "reduce_quarter",
    "close", "close_all", "take_profit", "stop",
})

# 「建仓/持仓仓位纪律」规则: 它们读 decision.position_pct 当目标仓位上限检查,
# 对卖出类动作语义失效 (减仓比例≠目标仓位), 应豁免以免误阻断风控减仓.
# 系统性修复 2026-09-02: r001 单笔上限曾把 reduce 0.5 判为「仓位50%>20%」block,
# 触发 apply_doctrine 清零 position_pct → 报告出现「减仓｜观望｜变动仓位0%」自相矛盾.
_POSITION_CAP_CHECK_FNS = frozenset({
    "check_position_limit",            # r001 单笔仓位上限
    "check_total_exposure",            # r002 总敞口上限
    "check_pre_data_heavy",            # r004 数据前不重仓
    "check_pre_data_adjustment",       # r016 数据前提前调整
    "check_no_chase",                  # r005 不追涨杀跌
    "check_friday_exposure",           # r006 周五减仓
    "check_holiday_exposure",          # r007 长假减仓
    "check_consecutive_stops",         # r008 连续止损休整 (position>0 即触发, 卖出时指减仓比例)
    "check_extreme_sentiment",         # r009 情绪极端暂停
    "check_conflict_cautious",         # r013 分歧过大观望
    "check_stop_loss_set",             # r014 必须设止损 (卖出本身是风控, 不再要求另设止损)
    "check_conditional_orders",        # r017 条件单代替盯盘
    "check_consecutive_high_volatility",   # r019 连续高波动暂停
    "check_retail_buy_institutional_sell", # r021 散户抄底机构出货
    "check_loss_decision_quality",     # r022 浮亏决策质量
    "check_empty_perspective",         # r023 空仓视角检验 (已决定减仓即已作答)
    "check_smart_money_flow",          # r024 聪明钱流向
    "check_atr_trailing_stop",         # r025 ATR移动止盈 (全清时 position_pct=1.0 不应被"建议减半"误阻断)
    "check_kelly_position",            # r031 凯利仓位
})


def _is_sell_action(decision: dict[str, Any]) -> bool:
    """判断决策是否为卖出/减仓类动作.

    卖出类动作的 position_pct 是减仓比例, 不是目标仓位, 不应走仓位上限类规则.
    """
    action = str(decision.get("action") or "").lower()
    return action in SELL_ACTIONS


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
        is_sell = _is_sell_action(decision)

        for rule in self.rules:
            checker_fn = getattr(self, rule.check_fn, None)
            if checker_fn is None:
                violations.append(RuleViolation(
                    rule=rule,
                    passed=True,
                    message=f"检查函数 {rule.check_fn} 未实现",
                ))
                continue

            # 卖出类动作: 仓位上限类规则豁免 (position_pct 是减仓比例, 非目标仓位)
            if is_sell and rule.check_fn in _POSITION_CAP_CHECK_FNS:
                violations.append(RuleViolation(
                    rule=rule,
                    passed=True,
                    message=(
                        f"卖出类动作 ({decision.get('action')})，"
                        f"position_pct={decision.get('position_pct', 0):.0%} 为减仓比例，"
                        f"非目标仓位，跳过「{rule.name}」上限检查"
                    ),
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
        """根据军规检查结果调整决策.

        卖出类动作的 position_pct 是减仓比例（如 reduce 0.5=减一半），不是目标仓位：
        block/warning 只记录不覆盖其减仓比例，否则会把「减仓一半」清零成「减仓0%」，
        与 position_state 风控止损冲突（2026-09-02 系统性修复）。
        """
        adjusted = dict(decision)
        is_sell = _is_sell_action(decision)

        if result.has_blocks:
            block_names = [v.rule.name for v in result.blocks]
            if is_sell:
                adjusted["doctrine_override"] = f"军规阻断(卖出豁免清零): {', '.join(block_names)}"
            else:
                adjusted["position_pct"] = 0.0
                adjusted["direction"] = "neutral"
                adjusted["doctrine_override"] = f"军规阻断: {', '.join(block_names)}"
            return adjusted

        if result.warnings:
            warn_count = len(result.warnings)
            warn_names = [v.rule.name for v in result.warnings]
            if is_sell:
                adjusted["doctrine_override"] = f"军规警告({warn_count}项): {', '.join(warn_names)}"
            else:
                original = adjusted.get("position_pct", 0)
                adjusted["position_pct"] = round(original * max(0.5, 1 - warn_count * 0.25), 2)
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

    def check_margin_of_safety(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_margin_of_safety")
        justification = ctx.get("margin_of_safety", "")
        passed = bool(justification)
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=(
                f"安全边际说明: {justification}"
                if passed
                else "未明确安全边际；每次决策须说明估值缓冲/仓位缓冲/止损保护/现金储备至少一项"
            ),
            details={"margin_of_safety": justification},
        )

    def check_friction_cost(self, decision: dict, ctx: dict) -> RuleViolation:
        """r032: 卖出类决策必须按扣除卖出手续费后的净收益核算."""
        rule = self._get_rule("check_friction_cost")
        action = str(decision.get("action", "")).lower()
        if action not in SELL_ACTIONS:
            return RuleViolation(rule=rule, passed=True, message="非卖出决策，无需核算摩擦成本")
        fee = float(ctx.get("sell_fee_pct", 0) or 0)
        considered = bool(ctx.get("friction_cost_considered", False)) or fee > 0
        return RuleViolation(
            rule=rule,
            passed=considered,
            message=(
                f"卖出费率 {fee:.1%} 已纳入净收益核算（净保本价=成本÷(1-费率)）"
                if considered
                else "卖出决策未考虑卖出手续费！净保本价=成本价÷(1-费率)，费率见 portfolio.yaml sell_fee_pct"
            ),
            details={"sell_fee_pct": fee, "action": action},
        )

    # ------------------------------------------------------------------
    # r016-r029 补全
    # ------------------------------------------------------------------

    def check_pre_data_adjustment(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_pre_data_adjustment")
        near_data_event = ctx.get("near_data_event", False)
        position = decision.get("position_pct", 0)
        passed = not (near_data_event and position > 0.30)
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=(
                "无重大数据临近" if not near_data_event
                else f"重大数据前仓位{position:.0%} {'≤30%可接受' if position <= 0.30 else '>30%建议提前调整'}"
            ),
            details={"near_data_event": near_data_event, "position_pct": position},
        )

    def check_conditional_orders(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_conditional_orders")
        has_conditional = ctx.get("has_conditional_orders", True)  # 默认假设已用条件单
        passed = has_conditional or decision.get("position_pct", 0) == 0
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=(
                "已使用条件单管理仓位" if has_conditional
                else "建议提前挂条件单，避免盘中情绪干扰"
            ),
            details={"has_conditional_orders": has_conditional},
        )

    def check_reduce_on_rally(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_reduce_on_rally")
        direction = decision.get("direction", "neutral")
        is_reduce = decision.get("action") == "reduce"
        daily_change = ctx.get("daily_change_pct", 0)
        passed = not (is_reduce and daily_change < -0.5)
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=(
                "当前非减仓操作" if not is_reduce
                else f"{'建议趁反弹时减仓，不在下跌中恐慌出手' if daily_change < -0.5 else '反弹减仓符合纪律'}"
            ),
            details={"direction": direction, "daily_change_pct": daily_change},
        )

    def check_consecutive_high_volatility(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_consecutive_high_volatility")
        consecutive_high_vol = ctx.get("consecutive_high_volatility_days", 0)
        position = decision.get("position_pct", 0)
        passed = not (consecutive_high_vol >= 2 and position > 0.10)
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=(
                f"连续高波动 {consecutive_high_vol} 天，"
                f"{'波动正常' if consecutive_high_vol < 2 else '建议暂停操作等波动收敛'}"
            ),
            details={"consecutive_days": consecutive_high_vol},
        )

    def check_etf_flow_priority(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_etf_flow_priority")
        etf_flow_available = ctx.get("etf_flow_available", False)
        return RuleViolation(
            rule=rule,
            passed=True,  # 信息级，不阻断
            message=(
                "已纳入ETF资金流向信号" if etf_flow_available
                else "建议关注ETF资金流向作为短期信号"
            ),
            details={"etf_flow_available": etf_flow_available},
        )

    def check_retail_buy_institutional_sell(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_retail_buy_institutional_sell")
        retail_buying = ctx.get("retail_buying", False)
        institutional_selling = ctx.get("institutional_selling", False)
        position = decision.get("position_pct", 0)
        is_trap = retail_buying and institutional_selling
        passed = not (is_trap and position > 0.05)
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=(
                "机构/散户流向正常" if not is_trap
                else f"散户抄底+机构出货信号检测到，{'仓位可控' if position <= 0.05 else '避免接飞刀'}"
            ),
            details={"retail_buying": retail_buying, "institutional_selling": institutional_selling},
        )

    def check_loss_decision_quality(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_loss_decision_quality")
        unrealized_pnl = ctx.get("unrealized_pnl_pct", 0)
        position = decision.get("position_pct", 0)
        passed = not (unrealized_pnl < -0.10 and position > 0.20)
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=(
                f"浮亏 {unrealized_pnl:.0%}，"
                f"{'正常范围' if unrealized_pnl > -0.10 else '决策质量下降预警：避免情绪化加仓/补仓'}"
            ),
            details={"unrealized_pnl_pct": unrealized_pnl},
        )

    def check_empty_perspective(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_empty_perspective")
        answered = ctx.get("empty_perspective_checked", False)
        position = decision.get("position_pct", 0)
        passed = answered or position == 0
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=(
                "已做空仓视角检验" if answered
                else "操作前请回答：如果空仓，会在这个价格买入吗？不会则考虑减仓"
            ),
            details={"empty_perspective_checked": answered},
        )

    def check_smart_money_flow(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_smart_money_flow")
        mm_long_change = ctx.get("managed_money_long_change", 0)
        mm_short_change = ctx.get("managed_money_short_change", 0)
        position = decision.get("position_pct", 0)
        price_up = ctx.get("daily_change_pct", 0) > 0.5
        mm_nett = mm_long_change - mm_short_change
        is_selloff = price_up and mm_nett < 0
        passed = not (is_selloff and position > 0.05)
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=(
                f"短期上涨+机构减仓风险: {'未检测到' if not is_selloff else '检测到！谨慎对待上涨'}"
            ),
            details={"mm_nett_change": mm_nett, "price_up": price_up},
        )

    def check_atr_trailing_stop(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_atr_trailing_stop")
        atr_active = ctx.get("atr_trailing_active", False)
        atr_triggered = ctx.get("atr_trailing_triggered", False)
        position = decision.get("position_pct", 0)
        passed = not (atr_triggered and position > 0.50)
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=(
                f"ATR移动止盈{'未激活' if not atr_active else '已触发' if atr_triggered else '正常跟踪'}，"
                f"{'仓位受控' if not atr_triggered or position <= 0.50 else '触发减仓信号！建议减仓一半'}"
            ),
            details={"atr_active": atr_active, "atr_triggered": atr_triggered},
        )

    def check_same_wave_reduce_guard(self, decision: dict, ctx: dict) -> RuleViolation:
        """r036: 同波二次破位护栏状态 (实际执行在 decision/position_state.resolve_position_state).

        恒通过 (warn), 仅披露状态; 阻断逻辑由 resolve_position_state 落地.
        """
        rule = self._get_rule("check_same_wave_reduce_guard")
        reduced = ctx.get("same_wave_reduced", None)
        action = decision.get("action") or decision.get("direction") or "unknown"
        if reduced:
            message = (
                "r036 生效: 同波已减仓后现价再破次级止损, 决策由「减仓」转「观察/低吸档」, "
                "防波段底二次割肉 (9/2 教训)"
            )
        else:
            message = "r036 未触发: 无同波二次破位场景 (或非减仓决策)"
        return RuleViolation(
            rule=rule,
            passed=True,
            message=message,
            details={"same_wave_reduced": reduced, "action": action},
        )

    def check_low_position_build_priority(self, decision: dict, ctx: dict) -> RuleViolation:
        """r037: 低仓位建仓优先 stance 状态 (实际执行在 strategy/low_buy_high_sell.Advisor).

        恒通过 (warn), 仅披露状态.
        """
        rule = self._get_rule("check_low_position_build_priority")
        stance = ctx.get("stance", "balance")
        if stance == "build":
            message = (
                "r037 build (建仓优先): 低仓期闸门降级 + 低吸带触发可执行档位 "
                "(单档≤5%总资金 / 分批 r028)"
            )
        elif stance == "defend":
            message = "r037 defend (防守): 仓位近上限, 保持严苛闸门, 不加仓"
        else:
            message = "r037 balance: 常规执行, 现行闸门不变"
        return RuleViolation(
            rule=rule,
            passed=True,
            message=message,
            details={"stance": stance},
        )

    def check_ma_trend_filter(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_ma_trend_filter")
        price_above_200ma = ctx.get("price_above_200ma", True)
        has_fundamental_confirm = ctx.get("has_fundamental_confirm", True)
        passed = True  # 不阻断，仅提示
        if not price_above_200ma:
            return RuleViolation(
                rule=rule,
                passed=passed,
                message="金价低于200日均线，需60日均线+基本面双重确认才可做多",
                details={"price_above_200ma": False, "has_fundamental_confirm": has_fundamental_confirm},
            )
        return RuleViolation(
            rule=rule,
            passed=passed,
            message="金价位于200日均线上方，趋势过滤通过",
            details={"price_above_200ma": True},
        )

    def check_gold_rebalance(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_gold_rebalance")
        gold_pct = ctx.get("gold_allocation_pct", 0)
        passed = gold_pct <= 0.60
        severity_text = "正常" if gold_pct <= 0.55 else "预警" if gold_pct <= 0.60 else "需减仓"
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=f"黄金占比 {gold_pct:.0%} ({severity_text})，{'超60%: 7日内减仓至50%以下' if not passed else '再平衡区间内'}",
            details={"gold_pct": gold_pct, "rebalance_threshold": 0.60},
        )

    def check_staggered_entry(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_staggered_entry")
        is_new_entry = decision.get("action") == "buy" or decision.get("is_new_position", False)
        batch_plan = ctx.get("batch_entry_plan", "")
        passed = not is_new_entry or bool(batch_plan)
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=(
                "非新建仓操作" if not is_new_entry
                else f"分批建仓计划: {batch_plan}" if batch_plan
                else "新建仓须制定分批计划（≥2批，间隔≥5个交易日）"
            ),
            details={"is_new_entry": is_new_entry, "batch_plan": batch_plan},
        )

    def check_valuation_margin(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_valuation_margin")
        is_adding = decision.get("action") in ("buy", "add") or decision.get("is_adding", False)
        has_valuation = ctx.get("valuation_range", "")
        passed = not is_adding or bool(has_valuation)
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=(
                "非加仓操作" if not is_adding
                else f"估值区间: {has_valuation}" if has_valuation
                else "加仓前须给出估值区间（DXY/实际利率/央行购金/金银比等多维度）"
            ),
            details={"is_adding": is_adding, "valuation_range": has_valuation},
        )

    def check_kelly_position(self, decision: dict, ctx: dict) -> RuleViolation:
        rule = self._get_rule("check_kelly_position")
        kelly_info = decision.get("kelly", {})
        kelly_suggested = kelly_info.get("suggested", 0)
        actual_position = decision.get("position_pct", 0)
        passed = actual_position <= max(kelly_suggested, 0.05)
        if kelly_suggested <= 0.01:
            return RuleViolation(
                rule=rule,
                passed=passed,
                message=f"Kelly 建议仓位 {kelly_suggested:.1%} — 信号不足以支持加仓，维持轻仓或观望",
                details={"kelly_suggested": kelly_suggested, "actual_position": actual_position},
            )
        return RuleViolation(
            rule=rule,
            passed=passed,
            message=(
                f"Kelly 建议 {kelly_suggested:.1%}，实际 {actual_position:.0%}，"
                f"{'仓位在 Kelly 范围内' if passed else '仓位超出 Kelly 建议，考虑缩仓'}"
            ),
            details={"kelly_suggested": kelly_suggested, "actual_position": actual_position},
        )

    def check_data_landing_trend(self, decision: dict, ctx: dict) -> RuleViolation:
        """r033: 重大数据温和落地后 3 天(72h)内禁止任何加仓；3 天后加仓仍须独立趋势确认.

        ctx 字段:
          - data_event_recent_72h: bool — 近 72h 内刚公布重大数据（回退 data_event_recent 24h）
          - data_landed_mild: bool — 数据结果温和/符合预期（市场已预先定价）
          - trend_confirmed: bool — 是否有独立趋势确认（关键点突破/均线多头/资金流同向）
          - repeated_buy_72h: bool — 72h 内是否已多次连续买入（回退 repeated_buy_24h）
        """
        rule = self._get_rule("check_data_landing_trend")
        add_actions = {"add", "buy", "increase"}
        action = str(decision.get("action", "")).lower()
        if action not in add_actions:
            return RuleViolation(rule=rule, passed=True, message="非加仓决策，无需校验数据落地趋势确认")

        data_event_recent = bool(
            ctx.get("data_event_recent_72h", ctx.get("data_event_recent", False))
        )
        data_landed_mild = bool(ctx.get("data_landed_mild", False))
        trend_confirmed = bool(ctx.get("trend_confirmed", False))
        repeated_buy = bool(
            ctx.get("repeated_buy_72h", ctx.get("repeated_buy_24h", False))
        )

        # 核心：数据温和落地后 3 天(72h)内禁止任何加仓（绝对时间盒，趋势确认不构成豁免）
        violated = data_event_recent and data_landed_mild
        # 严重化：72h 内已出现多次加仓动作 → 连续追买
        escalated = violated and repeated_buy

        passed = not violated
        if not violated:
            return RuleViolation(
                rule=rule,
                passed=True,
                message=(
                    "无'数据温和落地'场景，加仓不受 r033 限制"
                    if not data_event_recent
                    else "数据落地但非温和/已过 3 天窗口，加仓不受 r033 硬禁"
                ),
                details={
                    "data_event_recent": data_event_recent,
                    "data_landed_mild": data_landed_mild,
                    "trend_confirmed": trend_confirmed,
                    "repeated_buy": repeated_buy,
                },
            )
        hint = (
            "，且72h内已出现多次加仓（连续追买）"
            if escalated
            else "，3天(72h)内禁止任何加仓"
        )
        return RuleViolation(
            rule=rule,
            passed=False,
            message=(
                f"⚠️ 数据温和落地后72h内{hint}。即使有独立趋势确认也须等 3 天窗口消化（r033）；"
                "3 天后加仓仍须：关键点(950)有效突破回踩 / MA200上方 / 资金流同向"
            ),
            details={
                "data_event_recent": data_event_recent,
                "data_landed_mild": data_landed_mild,
                "trend_confirmed": trend_confirmed,
                "repeated_buy": repeated_buy,
            },
        )

    def check_data_landing_reduce(self, decision: dict, ctx: dict) -> RuleViolation:
        """r034: 数据温和落地后多空博弈震荡期，有浮盈应主动部分止盈，不'死扛等再涨'.

        ctx 字段:
          - data_event_recent_48h: bool — 近 48h 内刚公布重大数据（回退 72h/24h）
          - data_landed_mild: bool — 数据结果温和/符合预期
          - near_range_high: bool — 处于区间上沿（距 20 日高点 <3%）
          - smart_money_outflow: bool — 聪明钱流出（COT减仓/投行共识看空/大单空占优）
          - in_profit: bool — 已有浮盈（当前价 > 净保本价）
          - has_position: bool — 当前有持仓（默认取 decision.position_pct/current_gold_pct > 0）
        """
        rule = self._get_rule("check_data_landing_reduce")
        hold_actions = {"hold", "stand_aside", "reduce", "reduce_half", "reduce_quarter", "sell"}
        action = str(decision.get("action", "")).lower()
        has_position = bool(
            ctx.get(
                "has_position",
                float(decision.get("position_pct", 0)) > 0
                or float(decision.get("current_gold_pct", 0)) > 0,
            )
        )
        if action not in hold_actions or not has_position:
            return RuleViolation(
                rule=rule,
                passed=True,
                message="无持仓或非持有/减仓类决策，无需校验震荡止盈",
            )

        data_event_recent = bool(
            ctx.get(
                "data_event_recent_48h",
                ctx.get("data_event_recent_72h", ctx.get("data_event_recent", False)),
            )
        )
        data_landed_mild = bool(ctx.get("data_landed_mild", False))
        near_range_high = bool(ctx.get("near_range_high", False))
        smart_money_outflow = bool(ctx.get("smart_money_outflow", False))
        in_profit = bool(ctx.get("in_profit", False))

        # 核心：数据温和落地 + 高位震荡 + 聪明钱流出 + 有浮盈 → 主动部分止盈信号
        violated = (
            data_event_recent and data_landed_mild
            and near_range_high and smart_money_outflow and in_profit
        )

        passed = not violated
        if not violated:
            return RuleViolation(
                rule=rule,
                passed=True,
                message=(
                    "无'数据温和落地+高位震荡+聪明钱流出+浮盈'组合场景，持有不受 r034 限制"
                    if not (data_event_recent and data_landed_mild)
                    else "数据温和但未同时满足高位震荡/聪明钱流出/浮盈，暂无需主动止盈"
                ),
                details={
                    "data_event_recent": data_event_recent,
                    "data_landed_mild": data_landed_mild,
                    "near_range_high": near_range_high,
                    "smart_money_outflow": smart_money_outflow,
                    "in_profit": in_profit,
                },
            )
        hold_flag = action in {"hold", "stand_aside"}
        hint = "，应评估主动减仓而非'持有不动'" if hold_flag else ""
        return RuleViolation(
            rule=rule,
            passed=False,
            message=(
                f"⚠️ 数据温和落地+高位震荡+聪明钱流出+已有浮盈{hint}："
                "机动池主动部分止盈≥1/3（或p010减半）、核心池最多减1/4，不等ATR破位才动作（r034）"
            ),
            details={
                "data_event_recent": data_event_recent,
                "data_landed_mild": data_landed_mild,
                "near_range_high": near_range_high,
                "smart_money_outflow": smart_money_outflow,
                "in_profit": in_profit,
            },
        )

    def check_scenario_transmission(self, decision: dict, ctx: dict) -> RuleViolation:
        """r035: 情景预案须评估二阶传导（直接+二阶+时间尺度），单层传导=遗漏二阶效应.

        ctx 字段:
          - scenario_transmission_ok: bool — 三情景目标区间中, 地缘/油价驱动情景是否
            同时评估利多+利空传导并标注时间尺度分化（由 validate_scenario_transmissions 判定）
          - scenario_transmission_warnings: list[str] — 校验警告明细（可选，用于信息级展示）
        """
        rule = self._get_rule("check_scenario_transmission")
        ok = bool(ctx.get("scenario_transmission_ok", True))
        warnings = ctx.get("scenario_transmission_warnings", [])

        if ok:
            return RuleViolation(
                rule=rule,
                passed=True,
                message="情景预案传导链完整（直接+二阶+时间尺度）",
                details={"scenario_transmission_ok": True},
            )

        detail = f"；{'; '.join(warnings[:3])}" if warnings else ""
        return RuleViolation(
            rule=rule,
            passed=False,
            message=(
                "⚠️ 情景预案传导链不完整：地缘/油价驱动情景须同时评估利多+利空传导"
                "（油价→通胀→联储→实际利率）并标注时间尺度（短期脉冲 vs 中期回落）"
                f"{detail}（r035）"
            ),
            details={
                "scenario_transmission_ok": False,
                "warnings": warnings,
            },
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
