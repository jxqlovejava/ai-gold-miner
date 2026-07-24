"""监控卡片排版测试 — 成本去重 + 反弹收复进度措辞."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import adaptive_gold_monitor as m  # noqa: E402


def _rebound_state(low=878.0, high=882.0):
    return {"trend_low": low, "trend_high": high}


def test_rebound_full_recovery_wording():
    # 回升超过跌幅起点 → 不说"已收复247%", 说"已收复全部跌幅"
    alert = m._check_rebound(888.0, _rebound_state(), 894.0)
    assert alert is not None
    assert "已收复全部跌幅" in alert["message"]
    assert "247%" not in alert["message"]


def test_rebound_partial_recovery_percent():
    # 回升未超过跌幅起点 → 保留百分比 (870→874 反弹0.46%≥0.3%阈值, 收复4/16=25%)
    alert = m._check_rebound(874.0, _rebound_state(low=870.0, high=886.0), None)
    assert alert is not None
    assert "已收复 25%" in alert["message"]


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
    assert "⚠️ 已破成本线" in cost_lines[0]  # 警示合并进 header 同行


def test_card_cost_clean_when_profitable():
    price_info = {"price": 910.0, "prev_close": 905.0, "change_pct": 0.55}
    card = m._format_card("NORMAL", "NORMAL", price_info, 894.0, [], {})
    assert "浮盈 1.8%" in card
    assert "已破成本线" not in card
