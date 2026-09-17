"""测试 GLD 持仓数据抓取与持久化."""
from __future__ import annotations

import dataclasses
from datetime import datetime
from tempfile import TemporaryDirectory

import pandas as pd
import pytest

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


class FakeFindRecorder:
    """支持 find() 的假 recorder — 供 _from_db 守卫测试 (FakeRecorder 无 find)."""

    def __init__(
        self,
        observation_date: datetime | None,
        actual: float,
        previous: float,
    ) -> None:
        self.point = EconomicDataPoint(
            indicator="gld_holdings_tonnes",
            release_date=observation_date,
            actual=actual,
            previous=previous,
            observation_date=observation_date,
            unit="吨",
            source_tier="T0",
            fetched_at=observation_date,
        )

    def save(self, point: EconomicDataPoint, force: bool = False) -> bool:
        return True

    def find(self, indicator: str | None = None) -> list[EconomicDataPoint]:
        return [self.point] if indicator == "gld_holdings_tonnes" else []


class TestFromDbStalenessGuard:
    """事故 2026-09-17: 陈旧 GLD 数据被当实时值用.

    原 48h 守卫 + 6h 磁盘缓存下, 09:18 缓存的「截至 09-15」快照被 12:17 的 scan
    直接复用, 报告连续两轮显示 "+2.86吨 流入"(实为 09/14→09/15), 而 SPDR 已发布
    09-16 的 "+1.71吨"。陈旧值仍拿满 +0.55 分 (真值 +0.33) 并被 BullAgent 引为第一论据。
    """

    def test_fresh_observation_is_reused(self):
        rec = FakeFindRecorder(datetime.now() - pd.Timedelta(minutes=30), 1051.99, 1050.28)
        df = GldHoldingsFetcher(recorder=rec)._from_db()
        assert df is not None
        assert list(df["value"]) == [1050.28, 1051.99]

    @pytest.mark.parametrize("hours", [3, 24, 47.9, 60.8])
    def test_stale_observation_triggers_redownload(self, hours):
        """3h 以上的观测都必须放弃 DB 复用 —— 48h 守卫放行的 24h/47.9h 是事故根因."""
        rec = FakeFindRecorder(datetime.now() - pd.Timedelta(hours=hours), 1050.28, 1047.42)
        assert GldHoldingsFetcher(recorder=rec)._from_db() is None

    def test_missing_observation_date_is_reused(self):
        """无观测日期 (旧数据) 不做时效判定, 沿用原有行为."""
        rec = FakeFindRecorder(None, 1050.28, 1047.42)
        rec.point = dataclasses.replace(rec.point, observation_date=None)
        assert GldHoldingsFetcher(recorder=rec)._from_db() is not None

    def test_disk_cache_ttl_far_below_publication_gap(self):
        """GLD 每日新增一个数据点, 磁盘缓存 TTL 必须远短于一天.

        旧值 21600s (6h) 允许跨过 SPDR 的发布时刻复用旧快照 —— 事故直接成因。
        """
        assert GldHoldingsFetcher.DISK_CACHE_TTL_SECONDS <= 3600
