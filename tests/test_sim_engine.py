"""jdgold 模拟盘沙盒测试 (mock jdgold_client sim 函数)."""
from __future__ import annotations

import pandas as pd
import pytest

from gold_miner.backtest.sim_engine import SimSandboxEngine, _kline_to_df


def _kline_df(n: int = 30, last_price: float = 900.0) -> pd.DataFrame:
    import pandas as pd

    dates = pd.date_range("2026-07-01", periods=n, freq="D")
    closes = [last_price - (n - 1 - i) * 0.1 for i in range(n)]  # 缓升序列
    return pd.DataFrame({
        "timestamp": dates,
        "open": [c - 0.5 for c in closes],
        "high": [c + 1.0 for c in closes],
        "low": [c - 1.0 for c in closes],
        "close": closes,
        "volume": [1000.0] * n,
    })


def _kline_data(rows: list[dict]) -> dict:
    return {"items": rows}


@pytest.fixture
def mock_logged_in(monkeypatch):
    """模拟已登录 + 账户/K线可获取 (patch sim_engine 模块内绑定的名字)."""
    monkeypatch.setattr(
        "gold_miner.backtest.sim_engine.check_login",
        lambda: (True, {"remaining_human": "约 8 小时"}),
    )
    monkeypatch.setattr(
        "gold_miner.backtest.sim_engine.fetch_sim_account",
        lambda *a, **k: {
            "availableAmount": "990000", "currentHoldingGram": "11.5",
            "costAvgPerGram": "890.5", "totalAsset": "1000000",
        },
    )


def test_not_logged_in(monkeypatch):
    """未登录 → status not_logged_in."""
    monkeypatch.setattr(
        "gold_miner.backtest.sim_engine.check_login",
        lambda: (False, {"reason": "expired"}),
    )
    engine = SimSandboxEngine()
    result = engine.evaluate()
    assert result["status"] == "not_logged_in"


def test_hold_recommendation(mock_logged_in, monkeypatch):
    """平稳行情 → 建议 hold (不触发 ATR, 非超卖)."""
    df = _kline_df()
    monkeypatch.setattr(
        "gold_miner.backtest.sim_engine.fetch_sim_kline",
        lambda *a, **k: _kline_data([
            {"tradeDate": str(r["timestamp"].date()), "openPrice": r["open"],
             "highPrice": r["high"], "lowPrice": r["low"], "closePrice": r["close"]}
            for _, r in df.iterrows()
        ]),
    )

    engine = SimSandboxEngine()
    result = engine.evaluate()

    assert result["status"] == "ok"
    assert result["recommendation"] == "hold"
    assert result["current_price"] == pytest.approx(df["close"].iloc[-1], abs=0.1)


def test_execute_reduce_when_atr_triggered(mock_logged_in, monkeypatch):
    """跌破 ATR 止盈位 → 建议 reduce, execute=True 触发 sim_sell (按比例)."""
    import pandas as pd

    # 先升后大幅回落 → 触发 ATR 浮盈轨 (价格跌破 highest_high - 2.5*ATR)
    closes = list(pd.Series([900.0 + i * 2 for i in range(15)])) + \
             list(pd.Series([930.0 - i * 8 for i in range(15)]))
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-07-01", periods=30),
        "open": closes, "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes], "close": closes, "volume": [1000.0] * 30,
    })
    monkeypatch.setattr(
        "gold_miner.backtest.sim_engine.fetch_sim_kline",
        lambda *a, **k: _kline_data([
            {"tradeDate": str(r["timestamp"].date()), "openPrice": r["open"],
             "highPrice": r["high"], "lowPrice": r["low"], "closePrice": r["close"]}
            for _, r in df.iterrows()
        ]),
    )
    sell_calls = []
    monkeypatch.setattr(
        "gold_miner.backtest.sim_engine.sim_sell",
        lambda *a, **k: sell_calls.append((a, k)) or {"tradeNo": "T1"},
    )

    engine = SimSandboxEngine()
    result = engine.evaluate(execute=True)

    assert result["recommendation"] in ("reduce", "sell")
    assert result["executed"] is not None
    assert sell_calls  # sim_sell 被调用 (按比例卖出)


def test_decide_buy_when_oversold_below_ma():
    """_decide 纯函数: ATR 未触发 + 超卖 + 低于MA20 → buy."""
    from gold_miner.backtest.sim_engine import _decide

    action, reason = _decide(atr_triggered=False, atr_action="hold", rsi14=25.0, ma20=910.0, current=900.0)
    assert action == "buy"


def test_decide_reduce_when_atr_triggered():
    """_decide 纯函数: ATR 触发 → reduce."""
    from gold_miner.backtest.sim_engine import _decide

    action, _ = _decide(atr_triggered=True, atr_action="reduce_half", rsi14=None, ma20=None, current=900.0)
    assert action == "reduce"


def test_decide_sell_when_close_all():
    """_decide 纯函数: ATR close_all → sell."""
    from gold_miner.backtest.sim_engine import _decide

    action, _ = _decide(atr_triggered=True, atr_action="close_all", rsi14=None, ma20=None, current=900.0)
    assert action == "sell"


def test_decide_hold_otherwise():
    """_decide 纯函数: 非超卖 → hold."""
    from gold_miner.backtest.sim_engine import _decide

    action, _ = _decide(atr_triggered=False, atr_action="hold", rsi14=55.0, ma20=900.0, current=910.0)
    assert action == "hold"


def test_kline_to_df_parses():
    """_kline_to_df 防御式解析."""
    df = _kline_to_df({"items": [
        {"tradeDate": "2026-08-01", "openPrice": "900", "highPrice": "905",
         "lowPrice": "899", "closePrice": "903"},
        {"tradeDate": "2026-08-02", "openPrice": "903", "highPrice": "908",
         "lowPrice": "902", "closePrice": "906"},
    ]})
    assert df is not None
    assert len(df) == 2
    assert df["close"].iloc[-1] == 906.0


def test_kline_to_df_none_on_empty():
    assert _kline_to_df({"items": []}) is None
    assert _kline_to_df(None) is None
