"""测试新增的基本面信号 — 金银比 + 实际利率."""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

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
