"""跨脚本空仓检测测试 — 清仓 (grams=0) 后各监控不得再展示持仓浮盈/ATR/止损位.

2026-08-21: 用户清仓后 adaptive_gold_monitor 仍推「成本¥933.62 | 浮盈 4.9%」+ ATR线.
根因: 各脚本读 portfolio.yaml 的 avg_cost 但从不检查 grams; 清仓后 avg_cost 保留作
历史参考 → 空仓被误判为有仓. adaptive_gold_monitor 的用例见 test_monitor_card.py,
本文件覆盖其余推送脚本: gold_plan_alert / profit_protection_monitor / overnight_news_scanner.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import gold_plan_alert as g  # noqa: E402
import overnight_news_scanner as o  # noqa: E402
import profit_protection_monitor as pp  # noqa: E402


EMPTY_POS = {
    "positions": {"gold_jd": {"grams": 0.0, "avg_cost": 933.62, "sell_fee_pct": 0.4}}
}
POS = {
    "positions": {"gold_jd": {"grams": 10.0, "avg_cost": 933.62, "sell_fee_pct": 0.4}},
    "long_term": {"s_protocol": {"fast_stop_pct": 8.0}},
}


def _write_portfolio(tmp_path, grams, avg_cost=933.62, sell_fee_pct=0.4):
    p = tmp_path / "portfolio.yaml"
    p.write_text(
        f"positions:\n  gold_jd:\n    grams: {grams}\n"
        f"    avg_cost: {avg_cost}\n    sell_fee_pct: {sell_fee_pct}\n",
        encoding="utf-8",
    )
    return p


# ── gold_plan_alert.py ──
def test_plan_alert_snapshot_shows_empty():
    s = g._portfolio_snapshot(EMPTY_POS)
    assert s is not None
    assert "空仓" in s
    assert "933.62" not in s  # 空仓不展示成本/净保本


def test_plan_alert_snapshot_normal_position():
    s = g._portfolio_snapshot(POS)
    assert "10.0g" in s


def test_plan_alert_profit_trail_skips_empty():
    # 空仓: 无浮盈可守护, r010 不触发
    assert g._profit_trail_check(EMPTY_POS, 1200.0) is None
    # 有仓: 浮盈 28.5% > 20% → 正常触发
    assert g._profit_trail_check(POS, 1200.0) is not None


def test_plan_alert_fast_stop_skips_empty():
    assert g._fast_stop_level(EMPTY_POS) is None
    assert g._fast_stop_level(POS) == pytest.approx(933.62 * 0.92, abs=0.01)


# ── profit_protection_monitor.py ──
def test_pp_cost_basis_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "PORTFOLIO_PATH", _write_portfolio(tmp_path, grams=0.0))
    assert pp._get_cost_basis() is None


def test_pp_cost_basis_normal(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "PORTFOLIO_PATH", _write_portfolio(tmp_path, grams=10.0))
    assert pp._get_cost_basis() == 933.62


# ── overnight_news_scanner.py ──
def test_overnight_portfolio_line_skips_empty(monkeypatch):
    import yaml as _yaml
    monkeypatch.setattr(_yaml, "safe_load", lambda *a, **k: EMPTY_POS)
    assert o._portfolio_line(980.0) is None


def test_overnight_portfolio_line_normal(monkeypatch):
    import yaml as _yaml
    monkeypatch.setattr(_yaml, "safe_load", lambda *a, **k: POS)
    line = o._portfolio_line(980.0)
    assert line is not None
    assert "持仓" in line
