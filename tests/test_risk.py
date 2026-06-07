"""Tests for decision/risk.py — RiskManager."""

from gold_miner.decision.risk import RiskCheck, RiskManager


def _buy_decision(position_pct: float = 0.3, bull_conf: float = 0.7, bear_conf: float = 0.2,
                  composite_score: float = 0.5) -> dict:
    return {
        "direction": "long",
        "position_pct": position_pct,
        "signal_type": "中等信号",
        "risk_profile": "moderate",
        "bull_confidence": bull_conf,
        "bear_confidence": bear_conf,
        "composite_score": composite_score,
        "debate_summary": {"bull_args": [], "bear_args": []},
    }


class TestRiskManager:
    def test_check_within_limits_passes_all(self) -> None:
        rm = RiskManager()
        decision = _buy_decision(position_pct=0.3)
        checks = rm.check(decision, current_position_pct=0.2)

        assert all(c.passed for c in checks if c.severity != "warn")

    def test_check_position_over_limit_blocks(self) -> None:
        rm = RiskManager(max_position_pct=0.8)
        decision = _buy_decision(position_pct=0.9)
        checks = rm.check(decision, current_position_pct=0.0)

        position_check = [c for c in checks if c.name == "仓位上限"][0]
        assert not position_check.passed
        assert position_check.severity == "block"

    def test_check_concentration_risk_warns(self) -> None:
        rm = RiskManager(max_position_pct=0.8)
        decision = _buy_decision(position_pct=0.6)
        checks = rm.check(decision, current_position_pct=0.3)

        concentration_check = [c for c in checks if c.name == "集中度风险"][0]
        assert not concentration_check.passed
        assert concentration_check.severity == "warn"

    def test_check_bull_bear_conflict_warns(self) -> None:
        rm = RiskManager()
        decision = _buy_decision(bull_conf=0.7, bear_conf=0.6)
        checks = rm.check(decision)

        conflict_check = [c for c in checks if c.name == "多空冲突"][0]
        assert not conflict_check.passed
        assert conflict_check.severity == "warn"

    def test_check_extreme_signal_warns(self) -> None:
        rm = RiskManager()
        decision = _buy_decision(composite_score=0.95)
        checks = rm.check(decision)

        extreme_check = [c for c in checks if c.name == "极端信号"]
        assert len(extreme_check) == 1
        assert extreme_check[0].passed  # passed=True even though severity=warn

    def test_check_returns_riskcheck_objects(self) -> None:
        rm = RiskManager()
        decision = _buy_decision()
        checks = rm.check(decision)

        assert len(checks) > 0
        for c in checks:
            assert isinstance(c, RiskCheck)
            assert hasattr(c, "name")
            assert hasattr(c, "passed")
            assert hasattr(c, "message")
            assert hasattr(c, "severity")

    def test_apply_risk_controls_blocks_on_block_severity(self) -> None:
        rm = RiskManager()
        decision = _buy_decision(position_pct=0.9)
        checks = rm.check(decision)

        adjusted = rm.apply_risk_controls(decision, checks)
        assert adjusted["position_pct"] == 0.0
        assert adjusted["direction"] == "neutral"
        assert "风控拦截" in adjusted.get("risk_override", "")

    def test_apply_risk_controls_reduces_on_warning(self) -> None:
        """Warnings should reduce position proportionally."""
        rm = RiskManager(max_position_pct=0.8)
        decision = _buy_decision(position_pct=0.6)
        checks = rm.check(decision, current_position_pct=0.3)

        # Should have concentration warning: total = 0.6 + 0.3 = 0.9 > 0.8
        adjusted = rm.apply_risk_controls(decision, checks)
        expected = round(0.6 * (1 - 1 * 0.3), 2)  # 1 warning * 30% reduction
        assert adjusted["position_pct"] == expected
        assert "风控降仓" in adjusted.get("risk_override", "")

    def test_apply_risk_controls_no_change_when_all_pass(self) -> None:
        rm = RiskManager()
        decision = _buy_decision(position_pct=0.3)
        checks = rm.check(decision, current_position_pct=0.1)

        adjusted = rm.apply_risk_controls(decision, checks)
        assert adjusted["position_pct"] == decision["position_pct"]
        assert "risk_override" not in adjusted

    def test_apply_multiple_warnings_compound_reduction(self) -> None:
        """Multiple warnings should compound the position reduction."""
        rm = RiskManager(max_position_pct=0.8)
        decision = _buy_decision(position_pct=0.6, bull_conf=0.7, bear_conf=0.7)
        checks = rm.check(decision, current_position_pct=0.3)

        warn_count = sum(1 for c in checks if c.severity == "warn" and not c.passed)
        assert warn_count >= 1

        adjusted = rm.apply_risk_controls(decision, checks)
        expected = round(0.6 * (1 - warn_count * 0.3), 2)
        assert adjusted["position_pct"] == expected

    def test_risk_check_dataclass(self) -> None:
        check = RiskCheck(name="test", passed=True, message="ok", severity="info")
        assert check.name == "test"
        assert check.passed is True
        assert check.message == "ok"
        assert check.severity == "info"
