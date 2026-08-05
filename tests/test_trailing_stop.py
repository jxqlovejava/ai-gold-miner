"""ATR 双轨移动止损/止盈模块测试."""
from __future__ import annotations

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
        cost_basis=1000.0,
        hard_stop_price=700.0,
    )
    signal = ts.calculate(df, entry_price=1000.0)

    # 当前价 1040 > 成本价 1000.0, 处于浮盈状态
    assert signal.cost_basis == 1000.0
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
        cost_basis=1000.0,
        hard_stop_price=700.0,
    )
    signal = ts.calculate(df, entry_price=1000.0)

    # 当前价 980 < 成本价 1000.0, 处于浮亏状态, 但高于浮亏轨
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
        cost_basis=1000.0,
        hard_stop_price=700.0,
        loss_action="reduce_half",
    )
    signal = ts.calculate(df, entry_price=1000.0)

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
        cost_basis=1000.0,
        hard_stop_price=700.0,
    )
    signal = ts.calculate(df, entry_price=1000.0)

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


def test_entry_date_filters_historical_high():
    """建仓日(entry_date)之后的数据中最高价才计入持仓期间最高价.

    回归场景: 全窗口存在建仓前的高价(如 6/15 的 940.69), 但建仓日之后
    实际高点更低, 移动止盈锚点应取建仓后的最高价.
    """
    # 前 3 天高价 150 (建仓前), 建仓日后横盘于 110, 最后创新高 112
    # 建仓日(索引3)之后须 >=14 条数据, 才能满足 ATR 计算不触发回退
    prices = [100.0, 150.0, 150.0] + [110.0] * 15 + [112.0]
    df = _make_df(prices)

    # 建仓日为第 4 天 (索引 3, 即 2026-06-04)
    entry_date = (datetime(2026, 6, 1) + timedelta(days=3)).strftime("%Y-%m-%d")
    ts = ATRTrailingStop(
        atr_period=14,
        profit_multiplier=2.5,
        entry_date=entry_date,
    )
    signal = ts.calculate(df)

    # 建仓前高价 152 (=150+2) 不应计入; 建仓后最高为 114 (=112+2)
    assert signal.highest_high == 114.0, (
        f"持仓期间最高价应从建仓日起算, 得到 {signal.highest_high} 而非 152.0"
    )


def test_entry_date_none_keeps_full_window():
    """entry_date 为 None 时行为不变, 使用全窗口最高价 (向后兼容)."""
    prices = [100.0, 150.0, 150.0] + [110.0] * 15 + [112.0]
    df = _make_df(prices)

    ts = ATRTrailingStop(atr_period=14, profit_multiplier=2.5)
    signal = ts.calculate(df)

    assert signal.highest_high == 152.0  # 150 + 2 全窗口最高


def test_sell_fee_percent_units_keep_profit_track():
    """sell_fee_pct 是小数 (0.004=0.4%) 时, 浮盈应走浮盈轨.

    回归场景: analysis.py 曾直接把 portfolio 的百分比数值 (0.4) 传入,
    导致净保本 = 成本/(1-0.4) 被严重高估, 当前价看似浮亏走错轨道.
    """
    prices = [1000.0] * 13 + [1070.0, 1040.0]  # 创新高后小幅回落, 浮盈
    df = _make_df(prices)

    # 正确: sell_fee_pct=0.004 (0.4%) → 净保本 1000/0.996=1004.02, 当前 1040>1004 浮盈
    ts = ATRTrailingStop(
        atr_period=14,
        profit_multiplier=2.5,
        cost_basis=1000.0,
        hard_stop_price=700.0,
        sell_fee_pct=0.004,
    )
    signal = ts.calculate(df, entry_price=1000.0)
    assert signal.track == "profit", (
        f"0.4% 手续费下浮盈应走浮盈轨, 得到 {signal.track}"
    )

    # 错误: sell_fee_pct=0.4 (本应是百分比数值 40% 的误解) → 净保本 1000/0.6=1666.7
    # 当前 1040 < 1666.7 被误判为浮亏, 走浮亏轨 — 这是分析管线曾出现的 bug 形态
    ts_bad = ATRTrailingStop(
        atr_period=14,
        profit_multiplier=2.5,
        cost_basis=1000.0,
        hard_stop_price=700.0,
        sell_fee_pct=0.4,
    )
    bad_signal = ts_bad.calculate(df, entry_price=1000.0)
    assert bad_signal.track == "loss"  # 固化 bug 形态, 防止误以为 0.4 是正确值


def test_entry_date_filters_insufficient_falls_back():
    """建仓日过滤后数据不足 ATR 周期时, 回退到全窗口计算, 不报错."""
    # 建仓日设得很晚, 过滤后只剩 3 条 < 14 周期
    prices = [100.0] * 20
    df = _make_df(prices)
    late_entry = (datetime(2026, 6, 1) + timedelta(days=25)).strftime("%Y-%m-%d")

    ts = ATRTrailingStop(
        atr_period=14,
        profit_multiplier=2.5,
        entry_date=late_entry,
    )
    # 不应抛异常
    signal = ts.calculate(df)
    assert isinstance(signal, TrailingStopSignal)


def test_insufficient_data():
    """数据不足应抛出异常."""
    df = _make_df([100.0] * 5)
    ts = ATRTrailingStop(atr_period=14)

    with pytest.raises(ValueError, match="数据不足"):
        ts.calculate(df)
