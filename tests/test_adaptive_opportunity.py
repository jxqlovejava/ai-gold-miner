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


# ── 理由引擎 ──

def _snap(bull, bear, clarity):
    return {"bull": bull, "bear": bear, "clarity": clarity,
            "age_h": 2.0, "timestamp": "2026-07-24T09:30:00+08:00"}


def _ev(snapshot=None, rsi=55.0, events=None, orders=None):
    return {"rsi14": rsi, "ma20": 890.0, "ma60": 885.0,
            "snapshot": snapshot, "events": events or [],
            "active_orders": orders or []}


def test_reason_tp_mixed_is_strong():
    v = m._evaluate_reason("take_profit", {"lookback": 20, "high_n": 900.0, "profit_pct": 0.06},
                           _ev(snapshot=_snap(4, 4, "mixed")), _cfg())
    assert v["strength"] == "strong"
    assert any("方向不明" in r for r in v["reasons"])


def test_reason_tp_bearish_is_strong():
    v = m._evaluate_reason("take_profit", {"lookback": 20, "high_n": 900.0, "profit_pct": 0.06},
                           _ev(snapshot=_snap(2, 5, "bearish")), _cfg())
    assert v["strength"] == "strong"
    assert any("信号转空" in r for r in v["reasons"])


def test_reason_tp_bullish_veto():
    v = m._evaluate_reason("take_profit", {"lookback": 20, "high_n": 900.0, "profit_pct": 0.06},
                           _ev(snapshot=_snap(5, 2, "bullish"), rsi=55.0), _cfg())
    assert v["strength"] == "veto"
    assert "未触发止盈建议" in v["veto_note"]


def test_reason_tp_bullish_but_overbought_is_medium():
    v = m._evaluate_reason("take_profit", {"lookback": 20, "high_n": 900.0, "profit_pct": 0.06},
                           _ev(snapshot=_snap(5, 2, "bullish"), rsi=75.0), _cfg())
    assert v["strength"] == "medium"
    assert any("超买" in r for r in v["reasons"])


def test_reason_tp_missing_snapshot_is_weak():
    v = m._evaluate_reason("take_profit", {"lookback": 20, "high_n": 900.0, "profit_pct": 0.06},
                           _ev(snapshot=None), _cfg())
    assert v["strength"] == "weak"


def test_reason_dip_bearish_veto():
    cand = {"broke_low": True, "low_n": 880.0, "lookback": 20, "key_level": None}
    v = m._evaluate_reason("dip_buy", cand, _ev(snapshot=_snap(1, 5, "bearish")), _cfg())
    assert v["strength"] == "veto"
    assert "支撑未确认" in v["veto_note"]


def test_reason_dip_resonance_oversold_strong():
    cand = {"broke_low": True, "low_n": 880.0, "lookback": 20, "key_level": 921.0}
    v = m._evaluate_reason("dip_buy", cand, _ev(snapshot=_snap(4, 4, "mixed"), rsi=28.0), _cfg())
    assert v["strength"] == "strong"
    assert any("共振" in r for r in v["reasons"])
    assert any("超卖" in r for r in v["reasons"])


def test_reason_dip_single_condition_medium():
    cand = {"broke_low": True, "low_n": 880.0, "lookback": 20, "key_level": None}
    v = m._evaluate_reason("dip_buy", cand, _ev(snapshot=_snap(4, 4, "mixed"), rsi=45.0), _cfg())
    assert v["strength"] == "medium"


def test_reason_event_caution_on_dip():
    cand = {"broke_low": False, "low_n": 800.0, "lookback": 20, "key_level": 850.0}
    events = [{"name": "FOMC决议", "time": "07-25 02:00", "impact": "high"}]
    v = m._evaluate_reason("dip_buy", cand, _ev(snapshot=_snap(4, 4, "mixed"), events=events), _cfg())
    assert any("不接飞刀" in r for r in v["reasons"])


# ── 快照读取 ──

def test_load_snapshot_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "SIGNAL_SNAPSHOT_PATH", tmp_path / "nope.json")
    assert m._load_signal_snapshot(_cfg()) is None


def test_load_snapshot_stale(tmp_path, monkeypatch):
    import json as _json
    old = (datetime.now(BEIJING) - timedelta(hours=72)).isoformat()
    p = tmp_path / "snap.json"
    p.write_text(_json.dumps({"timestamp": old, "bull_dims": 4, "bear_dims": 4,
                              "direction_clarity": "mixed"}), encoding="utf-8")
    monkeypatch.setattr(m, "SIGNAL_SNAPSHOT_PATH", p)
    assert m._load_signal_snapshot(_cfg()) is None


def test_load_snapshot_fresh(tmp_path, monkeypatch):
    import json as _json
    fresh = (datetime.now(BEIJING) - timedelta(hours=2)).isoformat()
    p = tmp_path / "snap.json"
    p.write_text(_json.dumps({"timestamp": fresh, "bull_dims": 5, "bear_dims": 2,
                              "direction_clarity": "bullish"}), encoding="utf-8")
    monkeypatch.setattr(m, "SIGNAL_SNAPSHOT_PATH", p)
    snap = m._load_signal_snapshot(_cfg())
    assert snap is not None
    assert snap["clarity"] == "bullish"
    assert snap["bull"] == 5


def test_load_snapshot_zero_active_dims(tmp_path, monkeypatch):
    import json as _json
    fresh = (datetime.now(BEIJING) - timedelta(hours=1)).isoformat()
    p = tmp_path / "snap.json"
    p.write_text(_json.dumps({"timestamp": fresh, "bull_dims": 0, "bear_dims": 0,
                              "direction_clarity": "mixed"}), encoding="utf-8")
    monkeypatch.setattr(m, "SIGNAL_SNAPSHOT_PATH", p)
    assert m._load_signal_snapshot(_cfg()) is None


# ── 冷却/再提醒 ──

def test_cooldown_blocks_within_window():
    state = {"tp_alert_at": (datetime.now(BEIJING) - timedelta(minutes=30)).isoformat(),
             "tp_alert_price": 900.0}
    assert m._opp_cooldown_ok(state, "tp", 905.0, "up", _cfg()) is False


def test_cooldown_passes_after_window():
    state = {"tp_alert_at": (datetime.now(BEIJING) - timedelta(minutes=90)).isoformat(),
             "tp_alert_price": 900.0}
    assert m._opp_cooldown_ok(state, "tp", 905.0, "up", _cfg()) is True


def test_cooldown_realert_on_further_rise():
    state = {"tp_alert_at": (datetime.now(BEIJING) - timedelta(minutes=30)).isoformat(),
             "tp_alert_price": 900.0}
    # 900→910 = +1.11% ≥ 1% → 再提醒
    assert m._opp_cooldown_ok(state, "tp", 910.0, "up", _cfg()) is True


def test_cooldown_realert_on_further_drop():
    state = {"dip_alert_at": (datetime.now(BEIJING) - timedelta(minutes=30)).isoformat(),
             "dip_alert_price": 900.0}
    # 900→890 = -1.11% → 再提醒
    assert m._opp_cooldown_ok(state, "dip", 890.0, "down", _cfg()) is True


def test_cooldown_first_time_ok():
    assert m._opp_cooldown_ok({}, "tp", 900.0, "up", _cfg()) is True


# ── 告警构造 ──

def test_build_tp_alert_strong():
    cand = {"lookback": 20, "high_n": 900.0, "profit_pct": 0.062}
    verdict = {"strength": "strong", "reasons": ["信号快照：多4 空4，方向不明，落袋为安"],
               "veto_note": ""}
    alert = m._build_opp_alert("take_profit", cand, verdict, _ev(), 945.0, 890.0)
    assert alert["type"] == "take_profit_breakout"
    assert alert["severity"] == "HIGH"
    assert "机动仓15g" in alert["message"]
    assert "理由强度: 强" in alert["message"]


def test_build_tp_alert_veto():
    cand = {"lookback": 20, "high_n": 900.0, "profit_pct": 0.062}
    verdict = {"strength": "veto", "reasons": [], "veto_note": "信号快照：多5空2，方向仍偏多，未触发止盈建议"}
    alert = m._build_opp_alert("take_profit", cand, verdict, _ev(), 945.0, 890.0)
    assert alert["type"] == "take_profit_vetoed"
    assert "未触发止盈建议" in alert["message"]


def test_build_dip_alert_resonance():
    cand = {"broke_low": True, "low_n": 915.0, "lookback": 20, "key_level": 921.0}
    verdict = {"strength": "strong", "reasons": ["破20日低点与关键价位921共振"], "veto_note": ""}
    alert = m._build_opp_alert("dip_buy", cand, verdict, _ev(), 918.0, 894.25)
    assert alert["type"] == "dip_buy_opportunity"
    assert "共振" in alert["message"]
    assert "理由强度: 强" in alert["message"]


def test_build_dip_alert_key_level_only():
    cand = {"broke_low": False, "low_n": 800.0, "lookback": 20, "key_level": 850.0}
    verdict = {"strength": "medium", "reasons": ["信号快照：多4空4"], "veto_note": ""}
    alert = m._build_opp_alert("dip_buy", cand, verdict, _ev(), 852.0, 894.25)
    assert "关键价位 850" in alert["message"]
    assert "理由强度: 中" in alert["message"]


# ── 突破前兆 (Req1B 2026-08-11) ──

def test_breakout_enters_round_band():
    hist = _hist([880.0] * 25)  # 高点880, 远离 → 只有关口进带
    state = {"breakout_near_levels": []}
    cand = m._check_breakout_approach(947.5, state, hist, _cfg())
    assert cand is not None
    assert cand["type"] == "breakout_approach"
    assert cand["entered_level"] == 950.0
    assert cand["approach_high"] is False
    assert state["breakout_near_levels"] == [950.0]
    # 带内横盘 → 不再触发
    assert m._check_breakout_approach(948.0, state, hist, _cfg()) is None
    # 突破关口 → 出带重置
    assert m._check_breakout_approach(952.0, state, hist, _cfg()) is None
    assert state["breakout_near_levels"] == []


def test_breakout_approaches_high():
    hist = _hist([880.0] * 25)  # 高点880
    state = {"breakout_near_levels": []}
    # 879 距高点 880 = 0.11% ≤ 1.5%, 且未破高 → approach_high
    cand = m._check_breakout_approach(879.0, state, hist, _cfg())
    assert cand is not None
    assert cand["approach_high"] is True
    assert cand["high_n"] == 880.0


def test_breakout_no_trigger_far():
    hist = _hist([880.0] * 25)
    state = {"breakout_near_levels": []}
    # 900 距高点 880 = 2.3% > 1.5%, 且不在 950 带内
    assert m._check_breakout_approach(900.0, state, hist, _cfg()) is None


def test_breakout_no_trigger_after_broken_high():
    """已破高 → 交给 take_profit_breakout, 突破前兆不重复报."""
    hist = _hist([880.0] * 25)
    state = {"breakout_near_levels": []}
    # 895 > 880 已破高 → approach_high False; 950带外 → None
    assert m._check_breakout_approach(895.0, state, hist, _cfg()) is None


def test_breakout_short_history_skipped():
    state = {"breakout_near_levels": []}
    assert m._check_breakout_approach(947.0, state, _hist([880.0] * 5), _cfg()) is None


def test_breakout_cooldown_key_used():
    """_opp_cooldown_ok 对 breakout 前缀读 cooldown_breakout_min."""
    state = {"breakout_alert_at": (datetime.now(BEIJING) - timedelta(minutes=30)).isoformat(),
             "breakout_alert_price": 947.0}
    # 30min < 60min → 冷却内
    assert m._opp_cooldown_ok(state, "breakout", 947.5, "up", _cfg()) is False
    # 冷却内同向再走1% → 再提醒
    assert m._opp_cooldown_ok(state, "breakout", 958.0, "up", _cfg()) is True
    # 无记录 → 立即放行
    assert m._opp_cooldown_ok({}, "breakout", 947.0, "up", _cfg()) is True


def test_build_breakout_alert_message():
    cand = {"entered_level": 950.0, "approach_high": False, "high_n": 880.0,
            "lookback": 20, "current": 947.5}
    verdict = {"strength": "strong", "reasons": ["信号快照：多5空2"], "veto_note": ""}
    alert = m._build_opp_alert("breakout_approach", cand, verdict, _ev(), 947.5, 890.0)
    assert alert["type"] == "breakout_approach"
    assert alert["severity"] == "HIGH"
    assert "突破前兆" in alert["message"]
    assert "不自动挂单" in alert["message"]
    assert "理由强度: 强" in alert["message"]


def test_build_breakout_alert_veto():
    cand = {"entered_level": 950.0, "approach_high": False, "high_n": 880.0,
            "lookback": 20, "current": 947.5}
    verdict = {"strength": "veto", "reasons": [], "veto_note": "信号快照：多5空2，方向仍偏多"}
    alert = m._build_opp_alert("breakout_approach", cand, verdict, _ev(), 947.5, 890.0)
    assert alert["type"] == "breakout_approach_vetoed"
