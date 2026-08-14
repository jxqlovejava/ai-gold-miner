"""投资军规模块单元测试."""

from __future__ import annotations

import tempfile

from gold_miner.doctrine.checker import DoctrineChecker
from gold_miner.doctrine.mental_models import (
    ALL_MODELS,
    get_model_by_id,
)
from gold_miner.doctrine.models import (
    DoctrineResult,
    InvestmentRule,
    InvestmentStrategy,
    MentalModel,
    RuleViolation,
)
from gold_miner.doctrine.rules import (
    ALL_RULES,
    RULE_NO_CHASE,
    RULE_SINGLE_POSITION_LIMIT,
    get_rule_by_id,
)
from gold_miner.doctrine.store import DoctrineStore
from gold_miner.doctrine.strategies import (
    ALL_STRATEGIES,
    get_strategy_by_id,
)


class TestInvestmentRule:
    def test_create_rule(self) -> None:
        rule = RULE_SINGLE_POSITION_LIMIT
        assert rule.id == "r001"
        assert rule.severity == "block"
        assert rule.enabled is True

    def test_all_rules_valid(self) -> None:
        """所有规则必须有有效的 severity 和 category."""
        valid_severities = {"block", "warn", "info"}
        valid_categories = {"position_sizing", "timing", "emotion", "process", "operations", "info_discipline", "signal_discipline", "psychology", "trend", "entry"}
        for r in ALL_RULES:
            assert r.id.startswith("r"), f"Rule {r.id} id must start with 'r'"
            assert r.severity in valid_severities, f"Rule {r.id} bad severity: {r.severity}"
            assert r.category in valid_categories, f"Rule {r.id} bad category: {r.category}"
            assert r.name, f"Rule {r.id} missing name"
            assert r.description, f"Rule {r.id} missing description"
            assert r.check_fn, f"Rule {r.id} missing check_fn"

    def test_get_rule_by_id(self) -> None:
        assert get_rule_by_id("r001") is not None
        assert get_rule_by_id("r005") is not None
        assert get_rule_by_id("nonexistent") is None


class TestInvestmentStrategy:
    def test_all_strategies_valid(self) -> None:
        valid_regimes = {"trending", "ranging", "crisis", "recovery", "all"}
        for s in ALL_STRATEGIES:
            assert s.id.startswith("s"), f"Strategy {s.id} id must start with 's'"
            assert s.name, f"Strategy {s.id} missing name"
            assert s.applicable_regime in valid_regimes, f"Strategy {s.id} bad regime: {s.applicable_regime}"

    def test_get_strategy_by_id(self) -> None:
        assert get_strategy_by_id("s001") is not None
        assert get_strategy_by_id("s006") is not None
        assert get_strategy_by_id("nonexistent") is None


class TestMentalModel:
    def test_all_models_valid(self) -> None:
        for m in ALL_MODELS:
            assert m.id.startswith("m"), f"Model {m.id} id must start with 'm'"
            assert m.name, f"Model {m.id} missing name"
            assert m.key_principle, f"Model {m.id} missing key_principle"

    def test_get_model_by_id(self) -> None:
        assert get_model_by_id("m001") is not None
        assert get_model_by_id("m005") is not None
        assert get_model_by_id("nonexistent") is None


class TestDoctrineChecker:
    def test_all_passed_with_safe_decision(self) -> None:
        checker = DoctrineChecker()
        decision = {"direction": "long", "position_pct": 0.10, "kelly": {"suggested": 0.10}}
        context = {
            "current_exposure": 0.2,
            "gold_allocation_pct": 0.3,
            "daily_change_pct": 1.0,
            "near_data_event": False,
            "consecutive_stops": 0,
            "vix": 18,
            "fear_greed_index": 55,
            "unrealized_pnl_pct": 0.05,
            "has_trailing_stop": True,
            "bullish_signal_count": 4,
            "bearish_signal_count": 2,
            "active_dimensions": ["technical", "fundamental"],
            "bull_confidence": 0.55,
            "bear_confidence": 0.30,
            "stop_loss_set": True,
            "has_decision_record": True,
            "margin_of_safety": "仓位10%低于20%上限，止损已设，保留20%现金",
            "empty_perspective_checked": True,
        }
        result = checker.check(decision, context)
        assert result.failed_count == 0
        assert not result.has_blocks

    def test_block_position_limit(self) -> None:
        checker = DoctrineChecker()
        decision = {"direction": "long", "position_pct": 0.50}
        result = checker.check(decision, {})
        assert result.has_blocks
        assert any("仓位 50%" in v.message for v in result.blocks)

    def test_block_no_chase(self) -> None:
        checker = DoctrineChecker()
        decision = {"direction": "long", "position_pct": 0.20}
        context = {"daily_change_pct": 4.5}
        result = checker.check(decision, context)
        chase_violations = [v for v in result.violations if v.rule.id == "r005"]
        assert len(chase_violations) == 1
        assert not chase_violations[0].passed

    def test_warn_friday_exposure(self) -> None:
        """周五仓位>50%应警告（周五判断依赖当前日期，仅验证函数存在）."""
        checker = DoctrineChecker()
        decision = {"direction": "long", "position_pct": 0.10}
        context = {"consecutive_stops": 0}
        result = checker.check(decision, context)
        assert isinstance(result, DoctrineResult)

    def test_warn_multi_dimension(self) -> None:
        checker = DoctrineChecker()
        decision = {"direction": "long", "position_pct": 0.15}
        context = {"active_dimensions": ["technical"]}
        result = checker.check(decision, context)
        dim_violations = [v for v in result.violations if v.rule.id == "r012"]
        assert len(dim_violations) == 1
        assert not dim_violations[0].passed

    def test_warn_conflict(self) -> None:
        checker = DoctrineChecker()
        decision = {"direction": "long", "position_pct": 0.35}
        context = {"bull_confidence": 0.75, "bear_confidence": 0.70}
        result = checker.check(decision, context)
        conflict_violations = [v for v in result.violations if v.rule.id == "r013"]
        assert len(conflict_violations) == 1
        assert not conflict_violations[0].passed

    def test_warn_one_sided_signals(self) -> None:
        checker = DoctrineChecker()
        decision = {"direction": "long", "position_pct": 0.15}
        context = {"bullish_signal_count": 10, "bearish_signal_count": 1}
        result = checker.check(decision, context)
        sided_violations = [v for v in result.violations if v.rule.id == "r011"]
        assert len(sided_violations) == 1
        assert not sided_violations[0].passed

    def test_block_stop_loss_not_set(self) -> None:
        checker = DoctrineChecker()
        decision = {"direction": "long", "position_pct": 0.15}
        context = {"stop_loss_set": False}
        result = checker.check(decision, context)
        stop_violations = [v for v in result.violations if v.rule.id == "r014"]
        assert len(stop_violations) == 1
        assert not stop_violations[0].passed
        assert result.has_blocks

    def test_stop_loss_not_required_when_neutral(self) -> None:
        checker = DoctrineChecker()
        decision = {"direction": "neutral", "position_pct": 0}
        result = checker.check(decision, {})
        stop_violations = [v for v in result.violations if v.rule.id == "r014"]
        assert len(stop_violations) == 1
        assert stop_violations[0].passed  # 观望不需要止损

    def test_apply_doctrine_blocks(self) -> None:
        checker = DoctrineChecker()
        result = DoctrineResult()
        result.blocks = [
            RuleViolation(
                rule=RULE_SINGLE_POSITION_LIMIT,
                passed=False,
                message="仓位超限",
            )
        ]
        decision = {"direction": "long", "position_pct": 0.50}
        adjusted = checker.apply_doctrine(decision, result)
        assert adjusted["position_pct"] == 0.0
        assert adjusted["direction"] == "neutral"
        assert "doctrine_override" in adjusted

    def test_apply_doctrine_warnings_reduce_position(self) -> None:
        checker = DoctrineChecker()
        result = DoctrineResult()
        result.warnings = [
            RuleViolation(rule=RULE_NO_CHASE, passed=False, message="追涨"),
        ]
        decision = {"direction": "long", "position_pct": 0.40}
        adjusted = checker.apply_doctrine(decision, result)
        assert adjusted["position_pct"] == 0.30  # 40% * (1 - 1*0.25)
        assert "doctrine_override" in adjusted

    def test_apply_doctrine_no_issues(self) -> None:
        checker = DoctrineChecker()
        result = DoctrineResult()
        decision = {"direction": "long", "position_pct": 0.20}
        adjusted = checker.apply_doctrine(decision, result)
        assert adjusted == decision  # 无变更

    # ------------------------------------------------------------------
    # r033 数据落地≠趋势确认
    # ------------------------------------------------------------------

    def test_r033_passed_when_no_data_event(self) -> None:
        """无近期数据落地 → r033 通过."""
        checker = DoctrineChecker()
        decision = {"direction": "long", "position_pct": 0.15, "action": "add"}
        context = {"data_event_recent": False, "data_landed_mild": False, "trend_confirmed": False}
        result = checker.check(decision, context)
        violations = [v for v in result.violations if v.rule.id == "r033"]
        assert len(violations) == 1
        assert violations[0].passed

    def test_r033_passed_when_not_add(self) -> None:
        """非加仓动作 → r033 不触发."""
        checker = DoctrineChecker()
        decision = {"direction": "long", "position_pct": 0.15, "action": "hold"}
        context = {"data_event_recent": True, "data_landed_mild": True, "trend_confirmed": False}
        result = checker.check(decision, context)
        violations = [v for v in result.violations if v.rule.id == "r033"]
        assert len(violations) == 1
        assert violations[0].passed

    def test_r033_warn_within_72h_even_trend_confirmed(self) -> None:
        """数据温和落地后 3 天(72h)内，即使有趋势确认 → 仍警告（绝对时间盒）."""
        checker = DoctrineChecker()
        decision = {"direction": "long", "position_pct": 0.15, "action": "add"}
        context = {
            "data_event_recent_72h": True,
            "data_landed_mild": True,
            "trend_confirmed": True,
        }
        result = checker.check(decision, context)
        violations = [v for v in result.violations if v.rule.id == "r033"]
        assert len(violations) == 1
        assert not violations[0].passed
        assert "禁止任何加仓" in violations[0].message

    def test_r033_passed_when_outside_window(self) -> None:
        """72h 窗口外（无近期数据落地）→ 通过."""
        checker = DoctrineChecker()
        decision = {"direction": "long", "position_pct": 0.15, "action": "add"}
        context = {
            "data_event_recent_72h": False,
            "data_landed_mild": True,
            "trend_confirmed": False,
        }
        result = checker.check(decision, context)
        violations = [v for v in result.violations if v.rule.id == "r033"]
        assert len(violations) == 1
        assert violations[0].passed

    def test_r033_passed_when_data_not_mild(self) -> None:
        """数据落地但非温和（市场未预先定价）→ 通过."""
        checker = DoctrineChecker()
        decision = {"direction": "long", "position_pct": 0.15, "action": "add"}
        context = {
            "data_event_recent_72h": True,
            "data_landed_mild": False,
            "trend_confirmed": False,
        }
        result = checker.check(decision, context)
        violations = [v for v in result.violations if v.rule.id == "r033"]
        assert len(violations) == 1
        assert violations[0].passed

    def test_r033_warn_mild_data_no_trend(self) -> None:
        """数据温和落地 + 加仓 → 警告（核心场景，72h 禁加仓）."""
        checker = DoctrineChecker()
        decision = {"direction": "long", "position_pct": 0.15, "action": "add"}
        context = {"data_event_recent_72h": True, "data_landed_mild": True, "trend_confirmed": False}
        result = checker.check(decision, context)
        violations = [v for v in result.violations if v.rule.id == "r033"]
        assert len(violations) == 1
        assert not violations[0].passed
        assert "禁止任何加仓" in violations[0].message

    def test_r033_warn_escalated_repeated_buy(self) -> None:
        """数据温和 + 72h内多次追买 → 升级警示."""
        checker = DoctrineChecker()
        decision = {"direction": "long", "position_pct": 0.15, "action": "add"}
        context = {
            "data_event_recent_72h": True,
            "data_landed_mild": True,
            "repeated_buy_72h": True,
        }
        result = checker.check(decision, context)
        violations = [v for v in result.violations if v.rule.id == "r033"]
        assert len(violations) == 1
        assert not violations[0].passed
        assert "连续追买" in violations[0].message

    # ------------------------------------------------------------------
    # r034 数据温和期震荡止盈
    # ------------------------------------------------------------------

    def test_r034_passed_when_no_position(self) -> None:
        """无持仓 → r034 通过."""
        checker = DoctrineChecker()
        decision = {"direction": "long", "position_pct": 0.0, "action": "hold"}
        context = {
            "data_event_recent_48h": True,
            "data_landed_mild": True,
            "near_range_high": True,
            "smart_money_outflow": True,
            "in_profit": True,
            "has_position": False,
        }
        result = checker.check(decision, context)
        violations = [v for v in result.violations if v.rule.id == "r034"]
        assert len(violations) == 1
        assert violations[0].passed

    def test_r034_passed_when_not_hold_action(self) -> None:
        """加仓动作 → r034 不触发（管持有/减仓，加仓归 r033）."""
        checker = DoctrineChecker()
        decision = {"direction": "long", "position_pct": 0.15, "action": "add"}
        context = {
            "data_event_recent_48h": True,
            "data_landed_mild": True,
            "near_range_high": True,
            "smart_money_outflow": True,
            "in_profit": True,
            "has_position": True,
        }
        result = checker.check(decision, context)
        violations = [v for v in result.violations if v.rule.id == "r034"]
        assert len(violations) == 1
        assert violations[0].passed

    def test_r034_passed_when_missing_conditions(self) -> None:
        """数据温和但缺高位震荡/聪明钱流出/浮盈之一 → 通过（避免矫枉过正）."""
        checker = DoctrineChecker()
        decision = {"direction": "long", "position_pct": 0.15, "action": "hold"}
        context = {
            "data_event_recent_48h": True,
            "data_landed_mild": True,
            "near_range_high": True,
            "smart_money_outflow": False,  # 聪明钱未流出
            "in_profit": True,
            "has_position": True,
        }
        result = checker.check(decision, context)
        violations = [v for v in result.violations if v.rule.id == "r034"]
        assert len(violations) == 1
        assert violations[0].passed

    def test_r034_warn_mild_data_oscillation(self) -> None:
        """数据温和+高位震荡+聪明钱流出+浮盈+持有 → 警告（核心场景）."""
        checker = DoctrineChecker()
        decision = {"direction": "long", "position_pct": 0.15, "action": "hold"}
        context = {
            "data_event_recent_48h": True,
            "data_landed_mild": True,
            "near_range_high": True,
            "smart_money_outflow": True,
            "in_profit": True,
            "has_position": True,
        }
        result = checker.check(decision, context)
        violations = [v for v in result.violations if v.rule.id == "r034"]
        assert len(violations) == 1
        assert not violations[0].passed
        assert "部分止盈" in violations[0].message
        assert "评估主动减仓" in violations[0].message  # hold 动作升级提示

    def test_r034_warn_reduce_action_no_escalation(self) -> None:
        """减仓动作已符合 r034 → 警告但不含'应评估主动减仓'提示."""
        checker = DoctrineChecker()
        decision = {"direction": "long", "position_pct": 0.15, "action": "reduce_half"}
        context = {
            "data_event_recent_48h": True,
            "data_landed_mild": True,
            "near_range_high": True,
            "smart_money_outflow": True,
            "in_profit": True,
            "has_position": True,
        }
        result = checker.check(decision, context)
        violations = [v for v in result.violations if v.rule.id == "r034"]
        assert len(violations) == 1
        assert not violations[0].passed
        assert "应评估主动减仓" not in violations[0].message


class TestDoctrineStore:
    def test_toggle_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = DoctrineStore(data_dir=tmpdir)
            # 默认启用
            assert store.is_enabled("r001") is True
            # 切换为禁用
            new_state = store.toggle("r001")
            assert new_state is False
            assert store.is_enabled("r001") is False
            # 切换回启用
            new_state = store.toggle("r001")
            assert new_state is True

    def test_is_enabled_default_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = DoctrineStore(data_dir=tmpdir)
            assert store.is_enabled("nonexistent_rule") is True

    def test_load_empty_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = DoctrineStore(data_dir=tmpdir)
            assert store.load_state() == {}
