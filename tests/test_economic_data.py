"""测试经济数据持久化模块."""

from tempfile import TemporaryDirectory

import pytest

from gold_miner.data.economic_data import (
    EconomicDataPoint,
    EconomicDataRecorder,
    MarketSnapshot,
)
from gold_miner.storage.local import LocalFileStore


@pytest.fixture
def temp_store():
    with TemporaryDirectory() as tmpdir:
        yield LocalFileStore(private_data_dir=tmpdir)


class TestEconomicDataPoint:
    def test_round_trip(self):
        point = EconomicDataPoint(
            indicator="jolts_job_openings",
            release_date="2026-06-30",
            period="2026-05",
            actual=759.4,
            forecast=730.0,
            previous=760.0,
            unit="万人",
            source="BLS",
            source_tier="T0",
        )
        data = point.to_dict()
        restored = EconomicDataPoint.from_dict(data)
        assert restored == point

    def test_from_dict_ignores_extra_fields(self):
        data = {
            "indicator": "unemployment_rate",
            "release_date": "2026-07-03",
            "actual": 4.3,
            "extra_field": "should_be_ignored",
        }
        point = EconomicDataPoint.from_dict(data)
        assert point.indicator == "unemployment_rate"
        assert point.actual == 4.3
        assert not hasattr(point, "extra_field")


class TestEconomicDataRecorder:
    def test_save_and_load(self, temp_store):
        recorder = EconomicDataRecorder(store=temp_store)
        point = EconomicDataPoint(
            indicator="jolts_job_openings",
            release_date="2026-06-30",
            period="2026-05",
            actual=759.4,
        )
        assert recorder.save(point) is True
        loaded = recorder.load()
        assert len(loaded) == 1
        assert loaded[0].actual == 759.4

    def test_dedup_by_default(self, temp_store):
        recorder = EconomicDataRecorder(store=temp_store)
        point = EconomicDataPoint(
            indicator="nfp",
            release_date="2026-07-03",
            period="2026-06",
            actual=150.0,
        )
        assert recorder.save(point) is True
        assert recorder.save(point) is False
        assert len(recorder.load()) == 1

    def test_force_overwrite(self, temp_store):
        recorder = EconomicDataRecorder(store=temp_store)
        point1 = EconomicDataPoint(
            indicator="nfp",
            release_date="2026-07-03",
            period="2026-06",
            actual=150.0,
        )
        point2 = EconomicDataPoint(
            indicator="nfp",
            release_date="2026-07-03",
            period="2026-06",
            actual=180.0,
        )
        recorder.save(point1)
        recorder.save(point2, force=True)
        loaded = recorder.load()
        assert len(loaded) == 1
        assert loaded[0].actual == 180.0

    def test_load_skips_malformed_records(self, temp_store):
        recorder = EconomicDataRecorder(store=temp_store)
        temp_store.append_economic_data({"indicator": "valid", "release_date": "2026-07-01", "actual": 1.0})
        temp_store.append_economic_data({"indicator": "broken"})  # missing required release_date
        loaded = recorder.load()
        assert len(loaded) == 1
        assert loaded[0].indicator == "valid"

    def test_find_by_indicator_and_date(self, temp_store):
        recorder = EconomicDataRecorder(store=temp_store)
        recorder.save(
            EconomicDataPoint(indicator="a", release_date="2026-06-01", actual=1.0)
        )
        recorder.save(
            EconomicDataPoint(indicator="a", release_date="2026-07-01", actual=2.0)
        )
        recorder.save(
            EconomicDataPoint(indicator="b", release_date="2026-07-01", actual=3.0)
        )
        results = recorder.find(indicator="a", start_date="2026-06-15")
        assert len(results) == 1
        assert results[0].actual == 2.0


class TestMarketSnapshot:
    def test_round_trip(self):
        snapshot = MarketSnapshot(
            captured_at="2026-07-02T20:30:00+08:00",
            spot_gold_usd=4110.0,
            dxy=101.38,
            us_10y_yield=4.48,
        )
        data = snapshot.to_dict()
        restored = MarketSnapshot.from_dict(data)
        assert restored.spot_gold_usd == 4110.0
        assert restored.dxy == 101.38

    def test_to_dict_skips_none(self):
        snapshot = MarketSnapshot(spot_gold_usd=4000.0)
        data = snapshot.to_dict()
        assert "dxy" not in data
        assert data["spot_gold_usd"] == 4000.0

    def test_from_dict_extra_fields(self):
        data = {"spot_gold_usd": 4000.0, "custom_field": "value", "dxy": 101.0}
        snapshot = MarketSnapshot.from_dict(data)
        assert snapshot.spot_gold_usd == 4000.0
        assert snapshot.dxy == 101.0
        assert "custom_field" in snapshot.extra


class TestEconomicDataPointWithSnapshot:
    def test_round_trip_with_snapshot(self):
        snapshot = MarketSnapshot(spot_gold_usd=4110.0, dxy=101.38)
        point = EconomicDataPoint(
            indicator="nonfarm_payrolls",
            release_date="2026-07-02",
            actual=57000,
            forecast=114000,
            period="2026-06",
            batch_id="nfp_20260702",
            market_snapshot=snapshot,
        )
        data = point.to_dict()
        restored = EconomicDataPoint.from_dict(data)
        assert restored.market_snapshot is not None
        assert restored.market_snapshot.spot_gold_usd == 4110.0
        assert restored.batch_id == "nfp_20260702"

    def test_from_dict_preserves_input(self):
        """from_dict 不应修改传入的 dict."""
        data = {"indicator": "nfp", "release_date": "2026-07-02",
                "actual": 57, "market_snapshot": {"spot_gold_usd": 4000.0}}
        original_keys = set(data.keys())
        EconomicDataPoint.from_dict(data)
        assert set(data.keys()) == original_keys


class TestEconomicDataRecorderBatch:
    def test_save_batch(self, temp_store):
        recorder = EconomicDataRecorder(store=temp_store)
        snapshot = MarketSnapshot(spot_gold_usd=4100.0)
        points = [
            EconomicDataPoint(indicator="a", release_date="2026-07-02",
                              actual=1.0, batch_id="test", market_snapshot=snapshot),
            EconomicDataPoint(indicator="b", release_date="2026-07-02",
                              actual=2.0, batch_id="test", market_snapshot=snapshot),
        ]
        saved = recorder.save_batch(points, batch_id="test")
        assert saved == 2
        assert len(recorder.load()) == 2

    def test_save_batch_auto_fill_batch_id(self, temp_store):
        recorder = EconomicDataRecorder(store=temp_store)
        points = [
            EconomicDataPoint(indicator="a", release_date="2026-07-02", actual=1.0),
            EconomicDataPoint(indicator="b", release_date="2026-07-02", actual=2.0),
        ]
        recorder.save_batch(points, batch_id="auto")
        loaded = recorder.load()
        assert all(p.batch_id == "auto" for p in loaded)

    def test_find_batch(self, temp_store):
        recorder = EconomicDataRecorder(store=temp_store)
        points = [
            EconomicDataPoint(indicator="a", release_date="2026-07-02",
                              actual=1.0, batch_id="B1"),
            EconomicDataPoint(indicator="b", release_date="2026-07-02",
                              actual=2.0, batch_id="B1"),
            EconomicDataPoint(indicator="c", release_date="2026-07-02",
                              actual=3.0, batch_id="B2"),
        ]
        recorder.save_batch(points)
        b1 = recorder.find_batch("B1")
        assert len(b1) == 2
        b2 = recorder.find_batch("B2")
        assert len(b2) == 1

    def test_list_batches(self, temp_store):
        recorder = EconomicDataRecorder(store=temp_store)
        snapshot = MarketSnapshot(spot_gold_usd=4100.0)
        points = [
            EconomicDataPoint(indicator="x", release_date="2026-07-01",
                              actual=1.0, batch_id="B1", market_snapshot=snapshot),
            EconomicDataPoint(indicator="y", release_date="2026-07-02",
                              actual=2.0, batch_id="B2"),
        ]
        recorder.save_batch(points)
        batches = recorder.list_batches()
        assert len(batches) == 2
        # B1 has snapshot, B2 doesn't
        b1 = next(b for b in batches if b["batch_id"] == "B1")
        b2 = next(b for b in batches if b["batch_id"] == "B2")
        assert b1["has_snapshot"] is True
        assert b2["has_snapshot"] is False
