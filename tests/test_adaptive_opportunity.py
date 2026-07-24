"""止盈/抄底机会提醒测试 — scripts/adaptive_gold_monitor.py."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import adaptive_gold_monitor as m  # noqa: E402

BEIJING = m.BEIJING


def _cfg(**over):
    cfg = dict(m.OPP_DEFAULTS)
    cfg.update(over)
    return cfg


def _hist(closes):
    return [{"date": f"2026-06-{i+1:02d}", "close": c} for i, c in enumerate(closes)]


# ── surge 方向字段 ──

def test_surge_has_direction_up():
    state = {"last_price": 900.0}
    surge = m._check_surge(905.0, state)  # +0.56%
    assert surge is not None
    assert surge["direction"] == "up"
    assert surge["change_pct"] > 0


def test_surge_has_direction_down():
    state = {"last_price": 900.0}
    surge = m._check_surge(895.0, state)  # -0.56%
    assert surge is not None
    assert surge["direction"] == "down"


# ── RSI / MA ──

def test_rsi_all_gains_is_100():
    closes = [float(i) for i in range(1, 21)]
    assert m._rsi(closes) == 100.0


def test_rsi_all_losses_is_0():
    closes = [float(100 - i) for i in range(20)]
    assert m._rsi(closes) == 0.0


def test_rsi_insufficient_data():
    assert m._rsi([1.0, 2.0, 3.0]) is None


def test_ma_basic():
    assert m._ma([1.0, 2.0, 3.0, 4.0], 4) == 2.5
    assert m._ma([1.0, 2.0], 20) is None


# ── 配置覆盖 ──

def test_load_opp_config_defaults():
    cfg = m._load_opp_config()
    assert cfg["breakout_lookback_days"] == 20
    assert cfg["min_profit_pct"] == 0.05
    assert cfg["require_surge"] is True


# ── 止盈候选: 三条件同时 ──

def _surge_up():
    return {"type": "price_surge", "direction": "up", "change_pct": 0.6,
            "message": "x", "severity": "HIGH"}


def test_tp_all_conditions_trigger():
    hist = _hist([880.0] * 25)
    cand = m._check_take_profit_breakout(940.0, hist, 890.0, _cfg(), _surge_up())
    assert cand is not None
    assert cand["type"] == "take_profit_breakout"
    assert cand["high_n"] == 880.0
    assert cand["profit_pct"] == pytest.approx(50 / 890, rel=1e-3)


def test_tp_blocked_without_surge():
    hist = _hist([880.0] * 25)
    assert m._check_take_profit_breakout(940.0, hist, 890.0, _cfg(), None) is None
    down = {"type": "price_surge", "direction": "down", "change_pct": -0.6}
    assert m._check_take_profit_breakout(940.0, hist, 890.0, _cfg(), down) is None


def test_tp_blocked_without_new_high():
    hist = _hist([950.0] * 25)
    assert m._check_take_profit_breakout(940.0, hist, 890.0, _cfg(), _surge_up()) is None


def test_tp_blocked_without_profit():
    hist = _hist([880.0] * 25)
    # 现价885破新高, 但成本870 → 浮盈1.7% < 5%
    assert m._check_take_profit_breakout(885.0, hist, 870.0, _cfg(), _surge_up()) is None


def test_tp_blocked_without_cost_or_history():
    hist = _hist([880.0] * 25)
    assert m._check_take_profit_breakout(940.0, hist, None, _cfg(), _surge_up()) is None
    assert m._check_take_profit_breakout(940.0, _hist([880.0] * 5), 890.0, _cfg(), _surge_up()) is None


def test_tp_surge_optional_when_config_off():
    hist = _hist([880.0] * 25)
    cand = m._check_take_profit_breakout(940.0, hist, 890.0, _cfg(require_surge=False), None)
    assert cand is not None


# ── 抄底候选: 破低点 / 关键价位边沿触发 ──

def test_dip_broke_low_triggers():
    hist = _hist([880.0] * 25)
    state = {"in_band_levels": []}
    cand = m._check_dip_buy_opportunity(870.0, state, hist, _cfg())
    assert cand is not None
    assert cand["broke_low"] is True
    assert cand["low_n"] == 880.0
    assert cand["key_level"] is None


def test_dip_key_level_edge_trigger_once():
    hist = _hist([800.0] * 25)  # 低点远离, 不触发破低点
    state = {"in_band_levels": []}
    # 进入 921±1% 带 (911.79-930.21)
    cand = m._check_dip_buy_opportunity(920.0, state, hist, _cfg())
    assert cand is not None
    assert cand["key_level"] == 921.0
    assert cand["broke_low"] is False
    assert state["in_band_levels"] == [921.0]
    # 带内横盘 → 不再触发
    assert m._check_dip_buy_opportunity(921.0, state, hist, _cfg()) is None
    # 出带 → 重置
    assert m._check_dip_buy_opportunity(940.0, state, hist, _cfg()) is None
    assert state["in_band_levels"] == []
    # 再入带 → 再次触发
    cand2 = m._check_dip_buy_opportunity(919.0, state, hist, _cfg())
    assert cand2 is not None and cand2["key_level"] == 921.0


def test_dip_no_condition_no_trigger():
    hist = _hist([870.0] * 25)
    state = {"in_band_levels": []}
    assert m._check_dip_buy_opportunity(880.0, state, hist, _cfg()) is None


def test_dip_short_history_skipped():
    state = {"in_band_levels": []}
    assert m._check_dip_buy_opportunity(870.0, state, _hist([880.0] * 5), _cfg()) is None
