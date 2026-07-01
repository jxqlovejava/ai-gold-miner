"""测试宏观数据抓取与自动持久化."""

from datetime import datetime, timedelta
from tempfile import TemporaryDirectory

import pandas as pd

from gold_miner.data.economic_data import EconomicDataPoint, EconomicDataRecorder
from gold_miner.data.macro import MacroDataFetcher
from gold_miner.storage.local import LocalFileStore


class FakeRecorder:
    """可注入的 recorder，用于验证 _persist_series 行为."""

    def __init__(self) -> None:
        self.saved: list[EconomicDataPoint] = []

    def save(self, point: EconomicDataPoint, force: bool = False) -> bool:
        self.saved.append(point)
        return True


class TestMacroDataFetcher:
    def test_persist_series_daily(self):
        recorder = FakeRecorder()
        fetcher = MacroDataFetcher(recorder=recorder)

        dates = [datetime(2026, 7, 1) - timedelta(days=i) for i in range(3, -1, -1)]
        df = pd.DataFrame({
            "timestamp": dates,
            "value": [100.0, 101.0, 102.0, 103.0],
            "series_id": ["DTWEXBGS"] * 4,
        })

        fetcher._persist_series(df, "DTWEXBGS")
        assert len(recorder.saved) == 1
        point = recorder.saved[0]
        assert point.indicator == "dxy"
        assert point.observation_date == "2026-07-01"
        assert point.period == "2026-07-01"
        assert point.actual == 103.0
        assert point.previous == 102.0
        assert point.impact == "high"
        # release_date 是运行当天，格式校验即可
        assert len(point.release_date) == 10

    def test_persist_series_monthly(self):
        recorder = FakeRecorder()
        fetcher = MacroDataFetcher(recorder=recorder)

        dates = [datetime(2026, 5, 1), datetime(2026, 6, 1)]
        df = pd.DataFrame({
            "timestamp": dates,
            "value": [305.0, 310.0],
            "series_id": ["CPIAUCSL"] * 2,
        })

        fetcher._persist_series(df, "CPIAUCSL")
        assert len(recorder.saved) == 1
        point = recorder.saved[0]
        assert point.indicator == "cpi_index"
        assert point.observation_date == "2026-06-01"
        assert point.period == "2026-06"
        assert point.unit == "index"

    def test_persist_series_skips_empty(self):
        recorder = FakeRecorder()
        fetcher = MacroDataFetcher(recorder=recorder)
        fetcher._persist_series(pd.DataFrame(), "DTWEXBGS")
        assert len(recorder.saved) == 0

    def test_persist_series_skips_unknown_series(self):
        recorder = FakeRecorder()
        fetcher = MacroDataFetcher(recorder=recorder)
        df = pd.DataFrame({
            "timestamp": [datetime(2026, 7, 1)],
            "value": [1.0],
            "series_id": ["UNKNOWN"],
        })
        fetcher._persist_series(df, "UNKNOWN")
        assert len(recorder.saved) == 0

    def test_persist_series_integration_with_local_store(self):
        with TemporaryDirectory() as tmpdir:
            store = LocalFileStore(private_data_dir=tmpdir)
            recorder = EconomicDataRecorder(store=store)
            fetcher = MacroDataFetcher(recorder=recorder)

            dates = [datetime(2026, 7, 1) - timedelta(days=i) for i in range(2, -1, -1)]
            df = pd.DataFrame({
                "timestamp": dates,
                "value": [100.0, 101.0, 102.0],
                "series_id": ["DTWEXBGS"] * 3,
            })
            fetcher._persist_series(df, "DTWEXBGS")

            loaded = recorder.load()
            assert len(loaded) == 1
            assert loaded[0].actual == 102.0
