"""京东金融积存金价格抓取测试."""

from datetime import datetime
from unittest.mock import MagicMock

import pandas as pd
import pytest

from gold_miner.data.jd_accumulation_gold import (
    JdAccumulationGoldFetcher,
    JdGoldPrice,
)


@pytest.fixture
def sample_api_response() -> dict:
    """默认模拟民生银行积存金接口返回."""
    return {
        "resultData": {
            "msg": "成功",
            "code": 0,
            "data": {
                "minimumPriceValue": "917.75",
                "type": 1010,
                "productName": "民生积存金",
                "rateValue": "+0.24%",
                "minimumPriceLabel": "参考金价",
                "productTypeName": "黄金",
                "rateLabel": "涨跌幅",
                "productId": "21001001000001",
            },
        }
    }


@pytest.fixture
def mock_client(sample_api_response):
    """构造一个模拟的 httpx Client."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = sample_api_response
    mock_resp.raise_for_status.return_value = None

    mock_client = MagicMock()
    mock_client.get.return_value = mock_resp
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    return mock_client


def test_fetch_price_success(mock_client, monkeypatch):
    """正常解析京东积存金价格（默认民生）."""
    monkeypatch.setattr(
        "gold_miner.data.jd_accumulation_gold.get_proxied_client",
        lambda **kwargs: mock_client,
    )

    fetcher = JdAccumulationGoldFetcher()
    price = fetcher.fetch_price()

    assert isinstance(price, JdGoldPrice)
    assert price.price == 917.75
    assert price.product_name == "民生积存金"
    assert price.change_pct == "+0.24%"
    assert isinstance(price.timestamp, datetime)


def test_fetch_latest_returns_dataframe(mock_client, monkeypatch):
    """fetch_latest 返回标准化 DataFrame."""
    monkeypatch.setattr(
        "gold_miner.data.jd_accumulation_gold.get_proxied_client",
        lambda **kwargs: mock_client,
    )

    fetcher = JdAccumulationGoldFetcher()
    df = fetcher.fetch_latest()

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert set(df.columns) >= {"timestamp", "open", "high", "low", "close"}
    assert df["close"].iloc[0] == 917.75


def test_fetch_uses_fetch_latest(mock_client, monkeypatch):
    """fetch 委托给 fetch_latest."""
    monkeypatch.setattr(
        "gold_miner.data.jd_accumulation_gold.get_proxied_client",
        lambda **kwargs: mock_client,
    )

    fetcher = JdAccumulationGoldFetcher()
    df = fetcher.fetch(start=datetime.now(), end=datetime.now())

    assert len(df) == 1
    assert df["close"].iloc[0] == 917.75


def test_fetch_price_failure(monkeypatch):
    """请求失败时返回 None 且 DataFrame 为空."""
    mock_client = MagicMock()
    mock_client.get.side_effect = Exception("network error")
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    monkeypatch.setattr(
        "gold_miner.data.jd_accumulation_gold.get_proxied_client",
        lambda **kwargs: mock_client,
    )

    fetcher = JdAccumulationGoldFetcher()
    assert fetcher.fetch_price() is None
    assert fetcher.fetch_latest().empty


def test_fetch_price_invalid_response(monkeypatch):
    """解析异常时返回 None."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"resultData": {"data": {}}}
    mock_resp.raise_for_status.return_value = None

    mock_client = MagicMock()
    mock_client.get.return_value = mock_resp
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    monkeypatch.setattr(
        "gold_miner.data.jd_accumulation_gold.get_proxied_client",
        lambda **kwargs: mock_client,
    )

    fetcher = JdAccumulationGoldFetcher()
    assert fetcher.fetch_price() is None


def test_bank_mapping_zheshang(mock_client, monkeypatch):
    """支持切换银行代码到对应 productId."""
    monkeypatch.setattr(
        "gold_miner.data.jd_accumulation_gold.get_proxied_client",
        lambda **kwargs: mock_client,
    )

    fetcher = JdAccumulationGoldFetcher(bank="ZS")
    fetcher.fetch_price()

    called_url = mock_client.get.call_args[0][0]
    assert "1961543816" in called_url


def test_custom_product_id(mock_client, monkeypatch):
    """支持自定义 product_id / circle_id."""
    monkeypatch.setattr(
        "gold_miner.data.jd_accumulation_gold.get_proxied_client",
        lambda **kwargs: mock_client,
    )

    fetcher = JdAccumulationGoldFetcher(
        product_id="12345", circle_id="67890"
    )
    fetcher.fetch_price()

    called_url = mock_client.get.call_args[0][0]
    assert "12345" in called_url
    assert "67890" in called_url


def test_invalid_bank_raises():
    """非法银行代码应抛出异常."""
    with pytest.raises(ValueError, match="不支持的银行代码"):
        JdAccumulationGoldFetcher(bank="UNKNOWN")
