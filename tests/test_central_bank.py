"""测试央行购金数据自动持久化."""

from datetime import datetime
from tempfile import TemporaryDirectory

from gold_miner.data.central_bank import CentralBankData, CentralBankFetcher
from gold_miner.data.economic_data import EconomicDataRecorder
from gold_miner.storage.local import LocalFileStore


class FakeRecorder:
    def __init__(self) -> None:
        self.saved: list[dict] = []

    def save(self, point, force: bool = False) -> bool:
        self.saved.append(point.to_dict())
        return True


class TestCentralBankFetcher:
    def test_persist_quarterly_data(self):
        recorder = FakeRecorder()
        fetcher = CentralBankFetcher(url="test-url", recorder=recorder)

        data = CentralBankData(
            quarter="Q1 2026",
            net_purchases_tonnes=244.0,
            yoy_change_pct=0.03,
            source_url="test",
            fetched_at=datetime.now(),
        )
        fetcher._persist(data)

        assert len(recorder.saved) == 1
        record = recorder.saved[0]
        assert record["indicator"] == "central_bank_net_purchases"
        assert record["period"] == "Q1 2026"
        assert record["observation_date"] == "2026-03-31"
        assert record["actual"] == 244.0
        assert record["unit"] == "吨"
        assert record["source_tier"] == "T0"

    def test_fallback_data_persisted(self):
        with TemporaryDirectory() as tmpdir:
            store = LocalFileStore(private_data_dir=tmpdir)
            recorder = EconomicDataRecorder(store=store)
            fetcher = CentralBankFetcher(recorder=recorder)
            result = fetcher._fallback_data()

            assert result is not None
            loaded = recorder.load()
            assert len(loaded) == 1
            assert loaded[0].indicator == "central_bank_net_purchases"
            assert loaded[0].actual == 244.0

    def test_quarter_to_observation_date(self):
        fetcher = CentralBankFetcher()
        assert fetcher._quarter_to_observation_date("Q1 2026") == "2026-03-31"
        assert fetcher._quarter_to_observation_date("Q2 2026") == "2026-06-30"
        assert fetcher._quarter_to_observation_date("Q3 2026") == "2026-09-30"
        assert fetcher._quarter_to_observation_date("Q4 2026") == "2026-12-31"
        assert fetcher._quarter_to_observation_date("invalid") == ""
