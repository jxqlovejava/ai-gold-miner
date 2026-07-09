"""中长期信号生成器测试."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from gold_miner.signals.base import SignalDirection
from gold_miner.signals.long_term_fundamental import LongTermFundamentalSignal
from gold_miner.signals.long_term_scenario import LongTermScenarioSignal
from gold_miner.signals.long_term_trend import LongTermTrendSignal


def _make_gold_history(values: list[float]) -> pd.DataFrame:
    dates = [datetime.now() - timedelta(days=len(values) - i) for i in range(len(values))]
    return pd.DataFrame({
        "timestamp": dates,
        "open": values,
        "high": values,
        "low": values,
        "close": values,
        "volume": [100.0] * len(values),
    })


class TestLongTermTrendSignal:
    def test_generate_signals_returns_list(self):
        gen = LongTermTrendSignal()
        signals = gen.generate_signals()
        assert isinstance(signals, list)
        # 央行购金强劲信号在 fallback 数据下应出现
        names = [s.name for s in signals]
        assert "央行购金强劲" in names or "央行购金稳健" in names or "央行购金放缓" in names

    def test_central_bank_signal_direction(self):
        gen = LongTermTrendSignal()
        signals = gen.generate_signals()
        cb_signals = [s for s in signals if s.metadata.get("source") == "central_bank"]
        assert cb_signals
        assert cb_signals[0].direction in (SignalDirection.BULLISH, SignalDirection.BEARISH)


class TestLongTermFundamentalSignal:
    def test_fiscal_signals_with_fallback(self):
        gen = LongTermFundamentalSignal()
        signals = gen.generate_signals()
        assert signals
        names = [s.name for s in signals]
        assert any("债务" in n or "实际利率" in n or "美元储备" in n for n in names)

    def test_valuation_signals_with_history(self):
        # 价格持续高于 200 日均线
        values = [3000 + i * 10 for i in range(250)]
        history = _make_gold_history(values)
        gen = LongTermFundamentalSignal()
        signals = gen.generate_signals(gold_history=history)
        names = [s.name for s in signals]
        assert any("200日均线" in n for n in names)


class TestLongTermScenarioSignal:
    def test_generate_matrix_fallback(self, monkeypatch):
        # 禁用 LLM，确保测试不依赖网络
        monkeypatch.setattr("gold_miner.config.settings.llm_api_key", "")
        gen = LongTermScenarioSignal()
        signals, matrix = gen.generate_signals(base_price=3300, horizon_months=12)
        assert matrix is not None
        assert len(matrix.scenarios) == 5
        scenario_names = {s.name for s in matrix.scenarios}
        assert scenario_names == {"bull", "base", "bear", "extreme_up", "extreme_down"}

    def test_scenario_probabilities_sum_to_100(self, monkeypatch):
        monkeypatch.setattr("gold_miner.config.settings.llm_api_key", "")
        gen = LongTermScenarioSignal()
        _, matrix = gen.generate_signals(base_price=3300, horizon_months=12)
        total = sum(s.probability_pct for s in matrix.scenarios)
        assert total == 100

    def test_expected_price_within_range(self, monkeypatch):
        monkeypatch.setattr("gold_miner.config.settings.llm_api_key", "")
        gen = LongTermScenarioSignal()
        _, matrix = gen.generate_signals(base_price=3300, horizon_months=12)
        assert matrix.expected_price > 0
        assert matrix.weighted_expected_change_pct != 0 or matrix.expected_price == 3300
