"""ATR 双轨移动止损/止盈模块测试."""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from gold_miner.strategy.trailing_stop import ATRTrailingStop, TrailingStopSignal


def _make_df(prices: list[float]) -> pd.DataFrame:
    """构造测试用 OHLC DataFrame."""
    base = datetime(2026, 6, 1)
    data = []
    for i, close in enumerate(prices):
        high = close + 2
        low = close - 2
        open_ = close - (1 if i % 2 == 0 else -1)
        data.append({
            "timestamp": base + timedelta(days=i),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
        })
    return pd.DataFrame(data)


def test_profit_track_not_triggered():
    """价格未从高点回撤触发距离, 应返回 hold."""
    prices = [100.0] * 13 + [110.0]  # 前13天横盘, 最后一天创新高
    df = _make_df(prices)

    ts = ATRTrailingStop(atr_period=14, profit_multiplier=2.5)
    signal = ts.calculate(df, entry_price=100.0)

    assert isinstance(signal, TrailingStopSignal)
    assert signal.triggered is False
    assert signal.action == "hold"
    assert signal.track == "profit"
    assert signal.highest_high == 112.0  # 110 + 2


def test_profit_track_triggered():
    """价格从高点回撤超过 profit_multiplier×ATR, 应触发减仓."""
    prices = [100.0] * 13 + [110.0, 90.0]  # 创新高后大跌
    df = _make_df(prices)

    ts = ATRTrailingStop(
        atr_period=14,
        profit_multiplier=2.5,
        profit_action="reduce_half",
    )
    signal = ts.calculate(df, entry_price=100.0)

    assert signal.triggered is True
    assert signal.track == "profit"
    assert signal.action == "reduce_half"


def test_cost_basis_protection_in_profit():
    """浮盈时止损位不应低于成本价."""
    prices = [1000.0] * 13 + [1070.0, 1040.0]  # 创新高后小幅回落
    df = _make_df(prices)

    ts = ATRTrailingStop(
        atr_period=14,
        profit_multiplier=2.5,
        cost_basis=1014.42,
        hard_stop_price=710.0,
    )
    signal = ts.calculate(df, entry_price=1014.42)

    # 当前价 1040 > 成本价 1014.42, 处于浮盈状态
    assert signal.cost_basis == 1014.42
    assert signal.stop_price >= signal.cost_basis
    assert signal.stop_price < signal.highest_high
    assert signal.track == "profit"


def test_loss_track_not_triggered_above_hard_stop():
    """浮亏但高于浮亏轨时, 不触发."""
    prices = [1000.0] * 13 + [1070.0, 980.0]
    df = _make_df(prices)

    ts = ATRTrailingStop(
        atr_period=14,
        profit_multiplier=2.5,
        loss_multiplier=3.0,
        cost_basis=1014.42,
        hard_stop_price=710.0,
    )
    signal = ts.calculate(df, entry_price=1014.42)

    # 当前价 980 < 成本价 1014.42, 处于浮亏状态, 但高于浮亏轨
    assert signal.current_price < signal.cost_basis
    assert signal.track == "loss"
    assert signal.triggered is False
    assert "浮亏轨" in signal.reason


def test_loss_track_triggered():
    """价格跌破浮亏轨, 应触发减仓."""
    prices = [1000.0] * 13 + [1070.0, 850.0]
    df = _make_df(prices)

    ts = ATRTrailingStop(
        atr_period=14,
        profit_multiplier=2.5,
        loss_multiplier=3.0,
        cost_basis=1014.42,
        hard_stop_price=710.0,
        loss_action="reduce_half",
    )
    signal = ts.calculate(df, entry_price=1014.42)

    assert signal.triggered is True
    assert signal.track == "loss"
    assert signal.action == "reduce_half"
    assert "浮亏止损位" in signal.reason


def test_hard_stop_triggered():
    """价格跌破硬止损, 无条件清仓."""
    prices = [1000.0] * 13 + [1070.0, 700.0]
    df = _make_df(prices)

    ts = ATRTrailingStop(
        atr_period=14,
        profit_multiplier=2.5,
        loss_multiplier=3.0,
        cost_basis=1014.42,
        hard_stop_price=710.0,
    )
    signal = ts.calculate(df, entry_price=1014.42)

    assert signal.triggered is True
    assert signal.track == "hard_stop"
    assert signal.action == "close_all"
    assert "硬止损" in signal.reason


def test_invalid_parameters():
    """非法参数应抛出异常."""
    with pytest.raises(ValueError, match="atr_period"):
        ATRTrailingStop(atr_period=0)

    with pytest.raises(ValueError, match="profit_multiplier"):
        ATRTrailingStop(profit_multiplier=-1)

    with pytest.raises(ValueError, match="loss_multiplier"):
        ATRTrailingStop(loss_multiplier=-1)


def test_missing_columns():
    """DataFrame 缺少必要列应抛出异常."""
    df = pd.DataFrame({"close": [100.0] * 20})
    ts = ATRTrailingStop()

    with pytest.raises(ValueError, match="缺少列"):
        ts.calculate(df)


def test_insufficient_data():
    """数据不足应抛出异常."""
    df = _make_df([100.0] * 5)
    ts = ATRTrailingStop(atr_period=14)

    with pytest.raises(ValueError, match="数据不足"):
        ts.calculate(df)
