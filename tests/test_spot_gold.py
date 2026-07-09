"""现货黄金实时报价测试."""
from __future__ import annotations

from datetime import datetime

import pytest

from gold_miner.data.jd_accumulation_gold import JdGoldPrice
from gold_miner.data.spot_gold import SpotGoldFetcher


@pytest.fixture
def patch_jinjia_quote(monkeypatch):
    """屏蔽 jinjia.com.cn 网络请求，返回固定国内报价."""
    monkeypatch.setattr(
        SpotGoldFetcher,
        "_fetch_jinjia_quote",
        staticmethod(
            lambda: {
                "last_price": 800.0,
                "change_pct": 0.001,
                "source": "jinjia.com.cn",
            }
        ),
    )
    monkeypatch.setattr(
        SpotGoldFetcher, "_fetch_jinjia_international", staticmethod(lambda: None)
    )


def test_fetch_realtime_quote_includes_minsheng_accumulation(
    patch_jinjia_quote, monkeypatch
):
    """实时报价应附带民生银行积存金参考价."""

    class FakeJdFetcher:
        def __init__(self, bank: str) -> None:
            self.bank = bank

        def fetch_price(self) -> JdGoldPrice:
            return JdGoldPrice(
                timestamp=datetime.now(),
                product_name="民生积存金",
                price=810.0,
                change_pct="+0.50%",
                source="jd.com",
            )

    monkeypatch.setattr(
        "gold_miner.data.spot_gold.JdAccumulationGoldFetcher", FakeJdFetcher
    )

    quote = SpotGoldFetcher().fetch_realtime_quote()

    assert quote["domestic_price"] == 800.0
    assert "accumulation_gold" in quote
    assert quote["accumulation_gold"]["bank"] == "民生银行"
    assert quote["accumulation_gold"]["price"] == 810.0
    assert quote["accumulation_gold"]["change_pct"] == "+0.50%"


def test_fetch_realtime_quote_accumulation_failure_graceful(
    patch_jinjia_quote, monkeypatch
):
    """积存金接口失败时不应影响现货黄金报价."""

    class FakeJdFetcher:
        def __init__(self, bank: str) -> None:
            pass

        def fetch_price(self) -> JdGoldPrice:
            raise RuntimeError("network")

    monkeypatch.setattr(
        "gold_miner.data.spot_gold.JdAccumulationGoldFetcher", FakeJdFetcher
    )

    quote = SpotGoldFetcher().fetch_realtime_quote()

    assert quote["domestic_price"] == 800.0
    assert "accumulation_gold" not in quote
