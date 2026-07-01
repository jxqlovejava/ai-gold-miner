"""测试经济数据持久化模块."""

from tempfile import TemporaryDirectory

import pytest

from gold_miner.data.economic_data import EconomicDataPoint, EconomicDataRecorder
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
