"""测试央行购金数据自动持久化."""
from __future__ import annotations

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
            quarter="Q2 2026",
            net_purchases_tonnes=289.0,
            yoy_change_pct=0.62,
            source_url="test",
            fetched_at=datetime.now(),
        )
        fetcher._persist(data)

        assert len(recorder.saved) == 1
        record = recorder.saved[0]
        assert record["indicator"] == "central_bank_net_purchases"
        assert record["period"] == "Q2 2026"
        assert record["observation_date"] == "2026-06-30"
        assert record["actual"] == 289.0
        assert record["unit"] == "吨"
        assert record["source_tier"] == "T0"

    def test_fallback_data_persisted(self):
        with TemporaryDirectory() as tmpdir:
            store = LocalFileStore(private_data_dir=tmpdir)
            recorder = EconomicDataRecorder(store=store)
            fetcher = CentralBankFetcher(recorder=recorder)
            result = fetcher._fallback_data()

            assert result is not None
            assert result.quarter == "Q2 2026"
            assert result.net_purchases_tonnes == 289.0
            loaded = recorder.load()
            assert len(loaded) == 1
            assert loaded[0].indicator == "central_bank_net_purchases"
            assert loaded[0].actual == 289.0

    def test_fetch_parses_q2_url_and_merges_known_fields(self):
        """回归: WGC Q2 页(小写 q2-2026 URL)须解析为 Q2 2026，缺失字段用权威数据补全."""
        fetcher = CentralBankFetcher(
            url="https://www.gold.org/goldhub/research/gold-demand-trends/gold-demand-trends-q2-2026",
            recorder=FakeRecorder(),
        )
        fake_html = (
            "<html><body>"
            "<p>Central banks made significant gold purchases in Q2 (289t).</p>"
            "<p>Total gold demand, including OTC, was unchanged y/y at 1,269t in Q2.</p>"
            "<p>The gold price averaged US$4,506.29/oz in Q2.</p>"
            "<p>Gold ETFs came under selling pressure in Q2 (-45t).</p>"
            "<p>Bar and coin investment held steady y/y (307t) in Q2.</p>"
            "</body></html>"
        )
        fetcher._get_html = lambda url: fake_html  # type: ignore[method-assign]
        data = fetcher.fetch()

        assert data is not None
        assert data.quarter == "Q2 2026"  # 从"小写 + 连字符"URL slug 解析，非硬编码回退
        assert data.net_purchases_tonnes == 289.0
        assert data.yoy_change_pct == 0.62  # 页面无 prose y/y → 权威数据补全
        assert data.total_demand_tonnes == 1269.0
        assert data.avg_price_usd == 4506.29
        assert data.etf_flow_tonnes == -45.0
        assert data.bar_coin_tonnes == 307.0

    def test_quarter_to_observation_date(self):
        fetcher = CentralBankFetcher()
        assert fetcher._quarter_to_observation_date("Q1 2026") == "2026-03-31"
        assert fetcher._quarter_to_observation_date("Q2 2026") == "2026-06-30"
        assert fetcher._quarter_to_observation_date("Q3 2026") == "2026-09-30"
        assert fetcher._quarter_to_observation_date("Q4 2026") == "2026-12-31"
        assert fetcher._quarter_to_observation_date("invalid") == ""
