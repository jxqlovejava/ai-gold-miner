"""测试 PBOC 黄金储备数据."""

from gold_miner.data.pboc_reserves import PbocReservesFetcher


class TestPbocReservesFetcher:
    def test_known_reserves_not_empty(self):
        fetcher = PbocReservesFetcher()
        assert len(fetcher.KNOWN_RESERVES) >= 4
        for r in fetcher.KNOWN_RESERVES:
            assert "period" in r
            assert "oz_10k" in r

    def test_oz_to_tonnes_conversion(self):
        fetcher = PbocReservesFetcher()
        df = fetcher.fetch()
        assert not df.empty
        latest = df.iloc[-1]
        assert latest["value"] > 2000  # 中国黄金储备超 2000 吨
        assert latest["reserves_oz_10k"] == 7496
        assert latest["monthly_change_oz_10k"] == 32

    def test_persist_updates_known(self):
        fetcher = PbocReservesFetcher()
        parsed = {"period": "2026-06", "oz_10k": 7520, "change_oz_10k": 24}
        fetcher._persist(parsed)
        assert fetcher.KNOWN_RESERVES[-1]["oz_10k"] == 7520
        # cleanup
        fetcher.KNOWN_RESERVES.pop()
