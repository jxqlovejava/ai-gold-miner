"""监控卡片排版测试 — 成本去重 + 反弹收复进度措辞."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import adaptive_gold_monitor as m  # noqa: E402


def _rebound_state(low=878.0, high=882.0):
    return {"trend_low": low, "trend_high": high}


def test_rebound_full_recovery_wording():
    # 真实下跌 (890→878 = 1.3%) 且回升超过起点 → "已收复全部跌幅"
    alert = m._check_rebound(890.0, _rebound_state(low=878.0, high=890.0), 894.0)
    assert alert is not None
    assert "已收复全部跌幅" in alert["message"]


def test_rebound_partial_recovery_percent():
    # 真实下跌 (886→870 = 1.8%) 回升未收复 → 保留百分比 (870→874 收复4/16=25%)
    alert = m._check_rebound(874.0, _rebound_state(low=870.0, high=886.0), None)
    assert alert is not None
    assert "已收复 25%" in alert["message"]


def test_rebound_micro_drop_no_context_line():
    # 分钟级微跌 (882→878 = 0.45% < 1%) → 只有主行, 不解释"本轮跌幅"
    alert = m._check_rebound(887.59, _rebound_state(), 894.0)
    assert alert is not None
    assert "本轮" not in alert["message"]
    assert "已收复" not in alert["message"]


def test_rebound_price_precision_matches_card():
    # 现价 2 位小数, 与卡片 💰 行一致 (不再出现 888 vs 887.59)
    alert = m._check_rebound(887.59, _rebound_state(), 894.0)
    assert "887.59" in alert["message"]
    assert "→ 888" not in alert["message"]


def test_rebound_no_cost_lines():
    # 成本信息由卡片header展示, 反弹消息内不重复
    alert = m._check_rebound(875.0, _rebound_state(low=870.0, high=880.0), 894.0)
    assert alert is not None
    assert "距成本线" not in alert["message"]
    assert "成本线上方" not in alert["message"]


def test_card_cost_mentioned_once_below_cost():
    price_info = {"price": 887.59, "prev_close": 888.4, "change_pct": -0.09}
    alerts = [
        {"type": "cost_below", "message": "❌ 跌破成本线", "severity": "CRITICAL"},
    ]
    card = m._format_card("NORMAL", "NORMAL", price_info, 894.0, alerts, {})
    # 成本信息只占一行 (合并后的 header 行)
    cost_lines = [ln for ln in card.splitlines() if "成本" in ln]
    assert len(cost_lines) == 1
    assert "浮亏 0.7%" in cost_lines[0]
    # r032 摩擦成本核算后为净保本线语义 (扣 0.4% 卖出费, 卖出即实亏), 不再是毛成本线
    assert "⚠️ 已破净保本线" in cost_lines[0]  # 警示合并进 header 同行
    assert "卖出即实亏" in cost_lines[0]


def test_card_cost_clean_when_profitable():
    price_info = {"price": 910.0, "prev_close": 905.0, "change_pct": 0.55}
    card = m._format_card("NORMAL", "NORMAL", price_info, 894.0, [], {})
    assert "浮盈 1.8%" in card
    assert "已破成本线" not in card


def test_card_breakout_approach_shown_once():
    """突破前兆置顶展示, 且不在 remaining 二次重复."""
    price_info = {"price": 947.5, "prev_close": 940.0, "change_pct": 0.80}
    alerts = [
        {"type": "breakout_approach", "message": "🚀 突破前兆 (变盘窗口开启) | 价格升入整数关口 950 带",
         "severity": "HIGH"},
    ]
    card = m._format_card("NORMAL", "NORMAL", price_info, 890.0, alerts, {})
    assert card.count("🚀 突破前兆") == 1
    assert "变盘窗口开启" in card


def _trend_state(prices, window=12):
    """跑一串价格过 _update_trend_bookkeeping, 返回最终 state."""
    state = dict(m.DEFAULT_STATE)
    prev = None
    for p in prices:
        m._update_trend_bookkeeping(p, prev, state, {"trend_high_window_polls": window})
        prev = p
    return state


def test_trend_high_uses_window_peak():
    # 下跌前冲高958, 下跌前最后一拍955 → 本轮高点取窗口内最高958, 而非955
    state = _trend_state([950, 952, 958, 955, 949, 946])
    assert state["trend_high"] == 958.0
    assert state["trend_low"] == 946.0


def test_trend_high_window_expiry():
    # 久远高点(超过窗口)过期后, 本轮高点取近期价而非久远峰
    state = _trend_state([958, 957, 957, 957, 957, 957, 957, 957, 957, 955, 952], window=6)
    assert state["trend_high"] == 957.0


def test_trend_reset_clears_recent_high():
    # 回升>2% → trend + recent_high 全部重置
    state = _trend_state([958, 956, 953, 950, 946, 948, 952, 958, 968])
    assert state["trend_high"] is None
    assert state["trend_low"] is None
    assert state["recent_high"] is None


def test_trend_first_poll_seeds_recent_high():
    # 重启后首拍播种 recent_high, 不判定趋势
    state = dict(m.DEFAULT_STATE)
    m._update_trend_bookkeeping(958.0, None, state, {})
    assert state["recent_high"] == 958.0
    assert state["trend_high"] is None
