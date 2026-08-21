"""条件单接近提醒测试 — scripts/adaptive_gold_monitor.py.

验证 _check_order_proximity: 现价距活跃买入/卖出条件单触发价 ≤ order_near_pct 时提醒.
复用哨兵 check_order_proximity (含 OCO 双腿), 每单 60min 冷却去重.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import adaptive_gold_monitor as m  # noqa: E402

BEIJING = m.BEIJING


def _cfg(**over):
    cfg = dict(m.OPP_DEFAULTS)
    cfg.update(over)
    return cfg


def _write_orders(tmp_path, orders):
    p = tmp_path / "conditional_orders.jsonl"
    p.write_text(
        "\n".join(json.dumps(o, ensure_ascii=False) for o in orders) + "\n",
        encoding="utf-8",
    )
    return p


def _order(**over):
    base = {
        "id": "co_test_001",
        "status": "active",
        "type": "limit_buy",
        "direction": "买入",
        "trigger_price": 960.0,
        "quantity_g": 15.0,
    }
    base.update(over)
    return base


# ── 空/缺失 ──

def test_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "ORDERS_PATH", tmp_path / "nope.jsonl")
    assert m._check_order_proximity(970.0, {}, _cfg()) == []


def test_empty_orders_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "ORDERS_PATH", _write_orders(tmp_path, []))
    assert m._check_order_proximity(970.0, {}, _cfg()) == []


# ── 阈值判定 ──

def test_buy_order_within_band_alerts(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "ORDERS_PATH", _write_orders(tmp_path, [_order(trigger_price=960.0)]))
    # 970 → 距960 = 1.04% ≤ 1.5% → 提醒
    alerts = m._check_order_proximity(970.0, {}, _cfg())
    assert len(alerts) == 1
    a = alerts[0]
    assert a["type"] == "order_proximity"
    assert "限价买入" in a["message"]
    assert "960" in a["message"]
    assert "970" in a["message"]


def test_buy_order_outside_band_no_alert(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "ORDERS_PATH", _write_orders(tmp_path, [_order(trigger_price=960.0)]))
    # 1000 → 距960 = 4.2% > 1.5% → 不提醒
    assert m._check_order_proximity(1000.0, {}, _cfg()) == []


def test_inactive_order_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(
        m, "ORDERS_PATH",
        _write_orders(tmp_path, [_order(status="cancelled", trigger_price=960.0)]),
    )
    assert m._check_order_proximity(970.0, {}, _cfg()) == []


def test_sell_order_direction_in_message(tmp_path, monkeypatch):
    monkeypatch.setattr(
        m, "ORDERS_PATH",
        _write_orders(tmp_path, [_order(type="limit_sell", direction="卖出", trigger_price=1000.0)]),
    )
    # 990 → 距1000 = 1.0% → 提醒
    alerts = m._check_order_proximity(990.0, {}, _cfg())
    assert len(alerts) == 1
    assert "限价卖出" in alerts[0]["message"]
    assert "↓" in alerts[0]["message"]


# ── 冷却去重 ──

def test_cooldown_60min_suppresses_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "ORDERS_PATH", _write_orders(tmp_path, [_order(trigger_price=960.0)]))
    state: dict = {}
    assert len(m._check_order_proximity(970.0, state, _cfg())) == 1
    # 同一价位仍在带内, 冷却期内不重复提醒
    assert m._check_order_proximity(970.0, state, _cfg()) == []


def test_custom_cooldown_config_respected(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "ORDERS_PATH", _write_orders(tmp_path, [_order(trigger_price=960.0)]))
    state: dict = {}
    cfg = _cfg(cooldown_order_prox_min=0)  # 0 → 无冷却, 每次到位都提醒
    assert len(m._check_order_proximity(970.0, state, cfg)) == 1
    assert len(m._check_order_proximity(970.0, state, cfg)) == 1


# ── 数量上限 ──

def test_multi_order_caps_at_3(tmp_path, monkeypatch):
    orders = [
        _order(id=f"co_test_{i}", trigger_price=970.0 + i * 2)  # 970/972/974/976 全在带内
        for i in range(4)
    ]
    monkeypatch.setattr(m, "ORDERS_PATH", _write_orders(tmp_path, orders))
    alerts = m._check_order_proximity(970.0, {}, _cfg())
    assert len(alerts) == 3
