"""Tests for MaTrendGateSignal — MA50/MA100/MA200 长期趋势闸门.

用无噪音确定性数据构造，数值可精确断言。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from gold_miner.signals.base import SignalDirection
from gold_miner.signals.ma_trend_gate import MIN_MA200_BARS, MaTrendGateSignal


def _make_close_df(closes: np.ndarray) -> pd.DataFrame:
    """从收盘价序列构造 OHLCV DataFrame."""
    n = len(closes)
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "timestamp": dates,
        "open": closes,
        "high": closes + 1.0,
        "low": closes - 1.0,
        "close": closes,
        "volume": np.full(n, 1000.0),
    })


def _uptrend_closes(n: int = 260, start: float = 500.0, end: float = 780.0) -> np.ndarray:
    """平滑线性上升 — 多头排列."""
    return np.linspace(start, end, n)


def _downtrend_closes(n: int = 260, start: float = 780.0, end: float = 500.0) -> np.ndarray:
    """平滑线性下降 — 空头排列."""
    return np.linspace(start, end, n)


# ------------------------------------------------------------------
# analyze() 状态判定
# ------------------------------------------------------------------

class TestAnalyze:
    def test_bull_alignment_opens_gate(self):
        gate = MaTrendGateSignal(_make_close_df(_uptrend_closes())).analyze()
        assert gate["state"] == "bull"
        assert gate["gate_open"] is True
        assert gate["price_above_ma200"] is True
        assert gate["bull_alignment"] is True
        assert gate["bear_alignment"] is False
        # 多头排列数值: MA50 > MA100 > MA200
        assert gate["ma50"] > gate["ma100"] > gate["ma200"] > 0
        # 现价 > MA200
        assert gate["latest_close"] > gate["ma200"]

    def test_bear_alignment_closes_gate(self):
        gate = MaTrendGateSignal(_make_close_df(_downtrend_closes())).analyze()
        assert gate["state"] == "bear"
        assert gate["gate_open"] is False
        assert gate["price_above_ma200"] is False
        assert gate["bear_alignment"] is True
        assert gate["bull_alignment"] is False
        assert gate["ma50"] < gate["ma100"] < gate["ma200"]
        assert gate["latest_close"] < gate["ma200"]

    def test_break_below_ma200_closes_gate(self):
        # 长期上升后暴跌跌破 MA200 → 闸门关闭 (即使排列尚未完全空头)
        closes = np.concatenate([
            _uptrend_closes(230, 500, 780),
            np.linspace(780, 640, 30),
        ])
        gate = MaTrendGateSignal(_make_close_df(closes)).analyze()
        assert gate["state"] == "bear"
        assert gate["price_above_ma200"] is False
        assert gate["gate_open"] is False

    def test_mixed_alignment_is_neutral(self):
        # 横盘 → 暴跌 → V型反弹: 现价站上 MA200 但排列未确认 → mixed
        closes = np.concatenate([
            np.full(160, 500.0),
            np.linspace(500, 420, 40),
            np.linspace(420, 520, 60),
        ])
        gate = MaTrendGateSignal(_make_close_df(closes)).analyze()
        assert gate["state"] == "mixed"
        assert gate["price_above_ma200"] is True
        assert gate["bull_alignment"] is False
        assert gate["bear_alignment"] is False
        assert gate["gate_open"] is False

    def test_insufficient_data(self):
        closes = np.linspace(500, 600, MIN_MA200_BARS - 10)  # 不足 200 根
        gate = MaTrendGateSignal(_make_close_df(closes)).analyze()
        assert gate["state"] == "insufficient_data"
        assert gate["price_above_ma200"] is None
        assert gate["gate_open"] is False


# ------------------------------------------------------------------
# generate_signals() 输出
# ------------------------------------------------------------------

class TestGenerateSignals:
    def test_bull_signal(self):
        sigs = MaTrendGateSignal(_make_close_df(_uptrend_closes())).generate_signals()
        assert len(sigs) == 1
        s = sigs[0]
        assert s.direction == SignalDirection.BULLISH
        assert s.metadata.get("gate") is True
        assert s.metadata.get("ma200") > 0
        assert s.metadata.get("price_above_ma200") is True
        assert "开启" in s.name

    def test_bear_signal(self):
        sigs = MaTrendGateSignal(_make_close_df(_downtrend_closes())).generate_signals()
        assert len(sigs) == 1
        assert sigs[0].direction == SignalDirection.BEARISH
        assert "关闭" in sigs[0].name

    def test_mixed_neutral_signal(self):
        closes = np.concatenate([
            np.full(160, 500.0),
            np.linspace(500, 420, 40),
            np.linspace(420, 520, 60),
        ])
        sigs = MaTrendGateSignal(_make_close_df(closes)).generate_signals()
        assert len(sigs) == 1
        # NEUTRAL 不参与维度投票 (base.py 排除中性方向)
        assert sigs[0].direction == SignalDirection.NEUTRAL
        assert "中性" in sigs[0].name

    def test_insufficient_data_no_signal(self):
        closes = np.linspace(500, 600, 100)
        assert MaTrendGateSignal(_make_close_df(closes)).generate_signals() == []

    def test_metadata_carries_ma_values(self):
        gate = MaTrendGateSignal(_make_close_df(_uptrend_closes()))
        sigs = gate.generate_signals()
        assert sigs[0].metadata["ma50"] == gate.analyze()["ma50"]
        assert sigs[0].metadata["ma100"] == gate.analyze()["ma100"]
        assert sigs[0].metadata["ma200"] == gate.analyze()["ma200"]
