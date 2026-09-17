"""派生风控线回归测试 — 事故 2026-09-17 固化.

事故：新增买入把 avg_cost 从 983.27 改成 959.49，但 portfolio.yaml 的
hard_stop/warn_line/secondary_stop 仍是旧成本派生值，无人重算 →
PM 判据 ``current_price <= secondary_stop`` 变成 ``930.50 <= 934.11`` →
输出假的「减仓」信号（正确值 911.52 下不应触发）。
"""

from __future__ import annotations

import pytest

from gold_miner.decision.risk_lines import (
    RISK_LINE_RATIOS,
    apply_derived_risk_lines,
    check_risk_line_drift,
    derive_risk_lines,
    resolve_ratios,
)


def _portfolio(avg_cost: float, **fields) -> dict:
    return {"positions": {"gold_jd": {"avg_cost": avg_cost, **fields}}}


class TestDeriveRiskLines:
    def test_derives_all_three_lines(self) -> None:
        d = derive_risk_lines(959.49)
        assert d == {"hard_stop": 671.64, "warn_line": 863.54, "secondary_stop": 911.52}

    def test_old_cost_would_have_triggered(self) -> None:
        """事故核心：旧成本派生的 934.11 会让 930.50 误判破位，新成本不会。"""
        old = derive_risk_lines(983.27)["secondary_stop"]
        new = derive_risk_lines(959.49)["secondary_stop"]
        price = 930.50
        assert price <= old, "旧值下应误判触发 — 这是本次事故"
        assert price > new, "新值下不应触发"

    @pytest.mark.parametrize("bad", [0, 0.0, -1, -959.49, None])
    def test_non_positive_cost_returns_empty(self, bad) -> None:
        assert derive_risk_lines(bad or 0) == {}

    def test_custom_ratios(self) -> None:
        d = derive_risk_lines(1000.0, {"secondary_stop": 0.90})
        assert d == {"secondary_stop": 900.0}


class TestApplyDerivedRiskLines:
    def test_overwrites_stale_values(self) -> None:
        stale = _portfolio(959.49, hard_stop=688.29, warn_line=884.94, secondary_stop=934.11)
        fixed = apply_derived_risk_lines(stale)
        gold = fixed["positions"]["gold_jd"]
        assert gold["secondary_stop"] == 911.52
        assert gold["warn_line"] == 863.54
        assert gold["hard_stop"] == 671.64

    def test_does_not_mutate_input(self) -> None:
        stale = _portfolio(959.49, secondary_stop=934.11)
        apply_derived_risk_lines(stale)
        assert stale["positions"]["gold_jd"]["secondary_stop"] == 934.11

    def test_preserves_unrelated_fields(self) -> None:
        pf = _portfolio(959.49, grams=49.1407, entry_date="2026-09-16")
        gold = apply_derived_risk_lines(pf)["positions"]["gold_jd"]
        assert gold["grams"] == 49.1407
        assert gold["entry_date"] == "2026-09-16"

    def test_fills_missing_fields(self) -> None:
        gold = apply_derived_risk_lines(_portfolio(1000.0))["positions"]["gold_jd"]
        assert gold["hard_stop"] == 700.0

    @pytest.mark.parametrize(
        "bad",
        [{}, None, {"positions": None}, {"positions": {}}, {"positions": {"x": 1}},
         {"positions": {"gold_jd": {}}}, {"positions": {"gold_jd": {"avg_cost": 0}}}],
    )
    def test_degenerate_structures_do_not_raise(self, bad) -> None:
        assert apply_derived_risk_lines(bad) == bad
        assert check_risk_line_drift(bad) == []

    def test_non_dict_position_passthrough(self) -> None:
        pf = {"positions": {"gold_jd": "oops"}}
        assert apply_derived_risk_lines(pf) == pf


class TestCheckRiskLineDrift:
    def test_detects_all_three_stale_lines(self) -> None:
        drift = check_risk_line_drift(
            _portfolio(959.49, hard_stop=688.29, warn_line=884.94, secondary_stop=934.11)
        )
        assert len(drift) == 3
        assert all("派生" in d for d in drift)

    def test_silent_when_consistent(self) -> None:
        assert check_risk_line_drift(
            _portfolio(959.49, hard_stop=671.64, warn_line=863.54, secondary_stop=911.52)
        ) == []

    def test_absent_fields_are_not_drift(self) -> None:
        assert check_risk_line_drift(_portfolio(959.49)) == []

    def test_within_tolerance_is_not_drift(self) -> None:
        assert check_risk_line_drift(_portfolio(959.49, secondary_stop=911.52 + 0.005)) == []


class TestResolveRatios:
    def test_defaults(self) -> None:
        assert resolve_ratios() == RISK_LINE_RATIOS
        assert resolve_ratios({}) == RISK_LINE_RATIOS

    def test_override_merges_with_defaults(self) -> None:
        ratios = resolve_ratios({"limits": {"risk_ratios": {"secondary_stop": 0.90}}})
        assert ratios["secondary_stop"] == 0.90
        assert ratios["hard_stop"] == RISK_LINE_RATIOS["hard_stop"]

    def test_unknown_keys_and_bad_types_ignored(self) -> None:
        ratios = resolve_ratios(
            {"limits": {"risk_ratios": {"nope": 0.5, "warn_line": "x", "hard_stop": 0.8}}}
        )
        assert "nope" not in ratios
        assert ratios["warn_line"] == RISK_LINE_RATIOS["warn_line"]
        assert ratios["hard_stop"] == 0.8


class TestLoaderWiring:
    """两个读取点都必须经过派生覆盖（事故里 sentinel 直接 read_text 绕过了 local.py）。"""

    def test_local_file_store_applies_derivation(self) -> None:
        from gold_miner.storage.local import LocalFileStore

        pf = LocalFileStore().load_portfolio()
        gold = (pf.get("positions") or {}).get("gold_jd")
        if not gold or not gold.get("avg_cost"):
            pytest.skip("未配置真实持仓，跳过")
        expected = derive_risk_lines(gold["avg_cost"])
        for field, value in expected.items():
            assert gold[field] == value, f"{field} 未按 avg_cost 派生"
