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
