"""测试新增的基本面信号 — 金银比 + 实际利率."""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from gold_miner.signals.base import Signal, SignalDirection, SignalStrength
from gold_miner.signals.fundamental import FundamentalAnalyzer


def _make_rate_df(values: list[float]) -> pd.DataFrame:
    dates = [datetime.now() - timedelta(days=i) for i in range(len(values), 0, -1)]
    return pd.DataFrame({"timestamp": dates, "value": values})


def _make_price_df(values: list[float]) -> pd.DataFrame:
    dates = [datetime.now() - timedelta(days=i) for i in range(len(values), 0, -1)]
    return pd.DataFrame({
        "timestamp": dates,
        "open": values,
        "high": values,
        "low": values,
        "close": values,
        "volume": [100] * len(values),
    })


def _make_silver_df(values: list[float]) -> pd.DataFrame:
    dates = [datetime.now() - timedelta(days=i) for i in range(len(values), 0, -1)]
    return pd.DataFrame({
        "timestamp": dates,
        "value": values,
    })


class TestRealRateSignal:
    def test_real_rate_falling_bullish(self):
        # 实际利率下降 → 利好黄金
        rates = _make_rate_df([2.0] * 15 + [1.5] * 10)  # recent values lower
        analyzer = FundamentalAnalyzer(gold_df=_make_price_df([2000] * 25), rate_df=rates)
        signals = analyzer.analyze_rates()
        assert len(signals) >= 1
        assert any(s.direction == "bullish" for s in signals)

    def test_real_rate_rising_bearish(self):
        rates = _make_rate_df([1.0] * 15 + [2.0] * 10)
        analyzer = FundamentalAnalyzer(gold_df=_make_price_df([2000] * 25), rate_df=rates)
        signals = analyzer.analyze_rates()
        assert any(s.direction == "bearish" for s in signals)

    def test_negative_real_rate_bullish(self):
        rates = _make_rate_df([-0.5] * 25)
        analyzer = FundamentalAnalyzer(gold_df=_make_price_df([2000] * 25), rate_df=rates)
        signals = analyzer.analyze_rates()
        assert any("实际利率为负" in s.name for s in signals)

    def test_insufficient_data(self):
        rates = _make_rate_df([1.0] * 10)
        analyzer = FundamentalAnalyzer(rate_df=rates)
        signals = analyzer.analyze_rates()
        assert signals == []


class TestGoldSilverRatio:
    def test_high_ratio_bullish(self):
        gold_df = _make_price_df([3000] * 25)
        silver_df = _make_silver_df([30] * 25)  # ratio = 100
        analyzer = FundamentalAnalyzer(gold_df=gold_df, silver_df=silver_df)
        signals = analyzer.analyze_gold_silver_ratio()
        assert len(signals) >= 1
        assert any("极高位" in s.name for s in signals)

    def test_low_ratio_bearish(self):
        gold_df = _make_price_df([2000] * 25)
        silver_df = _make_silver_df([40] * 25)  # ratio = 50
        analyzer = FundamentalAnalyzer(gold_df=gold_df, silver_df=silver_df)
        signals = analyzer.analyze_gold_silver_ratio()
        assert any(s.direction == "bearish" for s in signals)

    def test_no_silver_data(self):
        analyzer = FundamentalAnalyzer(gold_df=_make_price_df([2000] * 25))
        signals = analyzer.analyze_gold_silver_ratio()
        assert signals == []

    def test_ratio_trend_up(self):
        # 构建金银比趋势上行: gold上涨 + silver下跌
        gold = [2000 + i * 10 for i in range(25)]
        silver = [30 - i * 0.1 for i in range(25)]
        analyzer = FundamentalAnalyzer(
            gold_df=_make_price_df(gold),
            silver_df=_make_silver_df(silver),
        )
        signals = analyzer.analyze_gold_silver_ratio()
        assert any("趋势上行" in s.name for s in signals)

    def test_generate_signals_includes_new(self):
        gold_df = _make_price_df([3000] * 25)
        silver_df = _make_silver_df([30] * 25)
        analyzer = FundamentalAnalyzer(gold_df=gold_df, silver_df=silver_df)
        signals = analyzer.generate_signals()
        signal_names = [s.name for s in signals]
        assert any("金银比" in n for n in signal_names)


class TestCentralBankFamilyMerge:
    """央行购金信号族合并 — 同一事实(中国⊂月度⊂季度)不得重复计分."""

    @staticmethod
    def _cb(name: str, direction: SignalDirection, score: float, desc: str = "") -> Signal:
        return Signal(
            name=name,
            dimension="fundamental",
            direction=direction,
            strength=SignalStrength.MODERATE,
            score=score,
            description=desc or name,
        )

    def test_same_direction_merges_with_capped_bonus(self):
        signals = [
            self._cb("央行大规模购金", SignalDirection.BULLISH, 0.8),
            self._cb("央行购金占比高", SignalDirection.BULLISH, 0.4),
            self._cb("重点央行月度持续购金", SignalDirection.BULLISH, 0.35),
            self._cb("中国央行加大购金", SignalDirection.BULLISH, 0.3),
        ]
        merged = FundamentalAnalyzer._merge_central_bank_family(signals)
        assert len(merged) == 1
        assert merged[0].name == "央行大规模购金"  # 最强档为主信号
        assert merged[0].score == 1.0  # 0.8 + 0.1×3 = 1.1 → 封顶 1.0
        # 子信号明细并入描述，信息不丢
        for sub in ("央行购金占比高", "重点央行月度持续购金", "中国央行加大购金"):
            assert sub in merged[0].description
        assert merged[0].metadata["family_confirmations"] == 3

    def test_direction_conflict_not_merged(self):
        # 季度 bullish + 月度 selling (方向相反) → 各自独立保留
        signals = [
            self._cb("央行大规模购金", SignalDirection.BULLISH, 0.8),
            self._cb("重点央行月度净卖出", SignalDirection.BEARISH, -0.15),
        ]
        merged = FundamentalAnalyzer._merge_central_bank_family(signals)
        assert len(merged) == 2
        assert {s.score for s in merged} == {0.8, -0.15}

    def test_bearish_group_merges_downward(self):
        signals = [
            self._cb("央行净卖出", SignalDirection.BEARISH, -0.3),
            self._cb("重点央行月度净卖出", SignalDirection.BEARISH, -0.15),
        ]
        merged = FundamentalAnalyzer._merge_central_bank_family(signals)
        assert len(merged) == 1
        assert merged[0].score == -0.4  # -0.3 - 0.1×1

    def test_single_signal_unchanged(self):
        signals = [self._cb("央行持续购金", SignalDirection.BULLISH, 0.5)]
        merged = FundamentalAnalyzer._merge_central_bank_family(signals)
        assert len(merged) == 1
        assert merged[0].score == 0.5
        assert "family" not in merged[0].metadata
