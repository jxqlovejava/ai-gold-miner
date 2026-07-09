"""测试 CFTC COT 持仓数据抓取与持久化."""
from __future__ import annotations

from datetime import datetime
from tempfile import TemporaryDirectory

import pandas as pd

from gold_miner.data.cot_data import CotDataFetcher
from gold_miner.data.economic_data import EconomicDataPoint, EconomicDataRecorder
from gold_miner.storage.local import LocalFileStore


class FakeRecorder:
    def __init__(self) -> None:
        self.saved: list[EconomicDataPoint] = []

    def save(self, point: EconomicDataPoint, force: bool = False) -> bool:
        self.saved.append(point)
        return True


class TestCotDataFetcher:
    def test_persist_saves_three_metrics(self):
        recorder = FakeRecorder()
        fetcher = CotDataFetcher(recorder=recorder)

        dates = [datetime(2026, 6, 16), datetime(2026, 6, 23)]
        df = pd.DataFrame({
            "timestamp": dates,
            "open_interest": [339330, 352167],
            "managed_money_long": [128043, 131102],
            "managed_money_short": [14322, 15707],
            "producer_long": [15000, 15839],
            "producer_short": [25000, 25175],
        })
        fetcher._persist_latest(df, "088691")

        assert len(recorder.saved) == 3
        indicators = {p.indicator for p in recorder.saved}
        assert "cot_GOLD_stock_managed_money_long" in indicators
        assert "cot_GOLD_stock_managed_money_short" in indicators
        assert "cot_GOLD_stock_open_interest" in indicators

        long_point = [p for p in recorder.saved if "managed_money_long" in p.indicator][0]
        assert long_point.actual == 131102.0
        assert long_point.previous == 128043.0
        assert long_point.source_tier == "T0"

    def test_persist_empty(self):
        recorder = FakeRecorder()
        fetcher = CotDataFetcher(recorder=recorder)
        fetcher._persist_latest(pd.DataFrame(), "088691")
        assert len(recorder.saved) == 0

    def test_metric_impact(self):
        fetcher = CotDataFetcher()
        assert fetcher._metric_impact("managed_money_long") == "high"
        assert fetcher._metric_impact("open_interest") == "medium"
        assert fetcher._metric_impact("producer_long") == "low"

    def test_integration_with_local_store(self):
        with TemporaryDirectory() as tmpdir:
            store = LocalFileStore(private_data_dir=tmpdir)
            recorder = EconomicDataRecorder(store=store)
            fetcher = CotDataFetcher(recorder=recorder)

            dates = [datetime(2026, 6, 16), datetime(2026, 6, 23)]
            df = pd.DataFrame({
                "timestamp": dates,
                "open_interest": [339330, 352167],
                "managed_money_long": [128043, 131102],
                "managed_money_short": [14322, 15707],
            })
            fetcher._persist_latest(df, "088691")

            loaded = recorder.load()
            assert len(loaded) == 3
            indicators = {r.indicator for r in loaded}
            assert "cot_GOLD_stock_managed_money_long" in indicators
