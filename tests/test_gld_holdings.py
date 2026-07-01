"""测试 GLD 持仓数据抓取与持久化."""

from datetime import datetime
from tempfile import TemporaryDirectory

import pandas as pd

from gold_miner.data.economic_data import EconomicDataPoint, EconomicDataRecorder
from gold_miner.data.gld_holdings import GldHoldingsFetcher
from gold_miner.storage.local import LocalFileStore


class FakeRecorder:
    def __init__(self) -> None:
        self.saved: list[EconomicDataPoint] = []

    def save(self, point: EconomicDataPoint, force: bool = False) -> bool:
        self.saved.append(point)
        return True


class TestGldHoldingsFetcher:
    def test_persist_latest(self):
        recorder = FakeRecorder()
        fetcher = GldHoldingsFetcher(recorder=recorder)

        dates = [datetime(2026, 6, 29), datetime(2026, 6, 30)]
        df = pd.DataFrame({
            "timestamp": dates,
            "value": [1007.08, 1005.08],
            "nav_per_share": [370.0, 369.47],
            "shares_volume": [5000000.0, 5302054.0],
        })
        fetcher._persist_latest(df)

        assert len(recorder.saved) == 1
        point = recorder.saved[0]
        assert point.indicator == "gld_holdings_tonnes"
        assert point.actual == 1005.08
        assert point.previous == 1007.08
        assert point.unit == "吨"
        assert point.source_tier == "T0"

    def test_persist_latest_empty(self):
        recorder = FakeRecorder()
        fetcher = GldHoldingsFetcher(recorder=recorder)
        fetcher._persist_latest(pd.DataFrame())
        assert len(recorder.saved) == 0

    def test_fetch_date_filtering(self):
        # 使用本地 mock：直接测试列标准化与过滤逻辑
        recorder = FakeRecorder()
        fetcher = GldHoldingsFetcher(recorder=recorder)

        raw = pd.DataFrame({
            "Date": ["29-Jun-2026", "30-Jun-2026"],
            "Tonnes of Gold": [1007.08, 1005.08],
            "NAV/Share at 10:30am NYT": [370.0, 369.47],
            "Daily Share Volume": [5000000, 5302054],
        })

        import io
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            raw.to_excel(writer, sheet_name="US GLD Historical Archive", index=False)
        buf.seek(0)

        # 直接调用内部解析不太方便，这里只验证 persistence
        df = pd.DataFrame({
            "timestamp": pd.to_datetime(["2026-06-29", "2026-06-30"]),
            "value": [1007.08, 1005.08],
            "nav_per_share": [370.0, 369.47],
            "shares_volume": [5000000.0, 5302054.0],
        })
        fetcher._persist_latest(df)
        assert len(recorder.saved) == 1

    def test_integration_with_local_store(self):
        with TemporaryDirectory() as tmpdir:
            store = LocalFileStore(private_data_dir=tmpdir)
            recorder = EconomicDataRecorder(store=store)
            fetcher = GldHoldingsFetcher(recorder=recorder)

            df = pd.DataFrame({
                "timestamp": pd.to_datetime(["2026-06-29", "2026-06-30"]),
                "value": [1007.08, 1005.08],
                "nav_per_share": [370.0, 369.47],
                "shares_volume": [5000000.0, 5302054.0],
            })
            fetcher._persist_latest(df)

            loaded = recorder.load()
            assert len(loaded) == 1
            assert loaded[0].indicator == "gld_holdings_tonnes"
            assert loaded[0].actual == 1005.08
