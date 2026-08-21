"""测试宏观数据抓取与自动持久化."""
from __future__ import annotations

from datetime import datetime, timedelta
from tempfile import TemporaryDirectory

import pandas as pd

import pytest

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


class TestMacroDxyVsTradeWeighted:
    """ICE DXY 与 FRED 贸易加权美元指数必须分开."""

    def test_series_maps_trade_weighted_not_dxy(self):
        assert MacroDataFetcher.SERIES.get("trade_weighted_usd") == "DTWEXBGS"
        assert "dxy" not in MacroDataFetcher.SERIES
        meta = MacroDataFetcher.SERIES_META["DTWEXBGS"]
        assert meta["indicator"] == "trade_weighted_usd"
        assert "贸易加权" in meta["name"]
        assert meta["name"] != "美元指数"

    def test_fetch_dxy_uses_yahoo_ice_symbol(self, monkeypatch):
        """fetch_dxy 必须走 Yahoo ICE DXY，而非 FRED DTWEXBGS."""
        # 隔离磁盘缓存 (真实运行会写 data/cache/dxy_cache.json, 快路径会跳过 yfinance)
        monkeypatch.setattr(MacroDataFetcher, "_read_dxy_disk_cache", staticmethod(lambda: None))
        idx = pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-03"])
        hist = pd.DataFrame(
            {"Close": [100.5, 101.0, 100.8], "Volume": [1, 1, 1]},
            index=idx,
        )
        hist.index.name = "Date"

        class FakeTicker:
            def __init__(self, symbol: str) -> None:
                assert symbol == "DX-Y.NYB"
                self.symbol = symbol

            def history(self, period: str = "1y") -> pd.DataFrame:
                return hist

        class FakeYf:
            @staticmethod
            def Ticker(symbol: str) -> FakeTicker:
                return FakeTicker(symbol)

        import sys
        monkeypatch.setitem(sys.modules, "yfinance", FakeYf)

        fetcher = MacroDataFetcher(recorder=FakeRecorder())
        called_fred: list[str] = []

        def fake_fetch(**kwargs):
            called_fred.append(kwargs.get("series_id", ""))
            return pd.DataFrame(columns=["timestamp", "value", "series_id"])

        monkeypatch.setattr(fetcher, "fetch", fake_fetch)
        df = fetcher.fetch_dxy()
        assert called_fred == []  # must not call FRED
        assert not df.empty
        assert list(df.columns) == ["timestamp", "value"]
        assert df["value"].iloc[-1] == pytest.approx(100.8)
        # ICE DXY 水平约 100，不是贸易加权约 120
        assert df["value"].mean() < 110

    def test_fetch_trade_weighted_usd_uses_fred(self, monkeypatch):
        fetcher = MacroDataFetcher(recorder=FakeRecorder())
        dates = [datetime(2026, 7, 1) - timedelta(days=i) for i in range(2, -1, -1)]
        fred_df = pd.DataFrame({
            "timestamp": dates,
            "value": [120.0, 121.0, 122.0],
            "series_id": ["DTWEXBGS"] * 3,
        })

        def fake_fetch(**kwargs):
            assert kwargs.get("series_id") == "DTWEXBGS"
            return fred_df

        monkeypatch.setattr(fetcher, "fetch", fake_fetch)
        df = fetcher.fetch_trade_weighted_usd()
        assert not df.empty
        assert list(df.columns) == ["timestamp", "value"]
        assert df["value"].iloc[-1] == 122.0


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
        # DTWEXBGS 是贸易加权美元指数，不是 ICE DXY
        assert point.indicator == "trade_weighted_usd"
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
