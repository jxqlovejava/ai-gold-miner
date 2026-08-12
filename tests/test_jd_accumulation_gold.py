"""京东金融积存金价格抓取测试."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pandas as pd
import pytest

from gold_miner.data.jd_accumulation_gold import (
    JdAccumulationGoldFetcher,
    JdGoldPrice,
)


@pytest.fixture(autouse=True)
def patch_jdgold_primary(monkeypatch):
    """屏蔽 jdgold 主源 (subprocess), 强制走 H5 getFirstRelatedProductInfo 兜底.

    jdgold 主源 2026-08-13 接入后 _fetch_price_info 先查 jdgold; 测试环境无真实
    subprocess 调用需求, 统一置 None → 落回被 mock 的 H5 路径 (保留原断言)。
    """
    monkeypatch.setattr(
        "gold_miner.data.jd_accumulation_gold._jdgold_fetch_price", lambda bank: None
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


def test_fetch_latest_returns_dataframe(mock_client, monkeypatch, tmp_path):
    """fetch 返回标准化 DataFrame（仅 JD，不依赖 SGE proxy 回退）."""
    monkeypatch.setattr(
        "gold_miner.data.jd_accumulation_gold.get_proxied_client",
        lambda **kwargs: mock_client,
    )

    fetcher = JdAccumulationGoldFetcher(history_path=tmp_path / "jd_test_history.csv")
    df = fetcher.fetch(days=5, fallback_to_sge=False)

    assert isinstance(df, pd.DataFrame)
    assert len(df) >= 1
    assert set(df.columns) >= {"timestamp", "open", "high", "low", "close"}
    assert df["close"].iloc[-1] == 917.75


def test_fetch_uses_fetch_latest(mock_client, monkeypatch, tmp_path):
    """fetch 返回 JD 最新价格."""
    monkeypatch.setattr(
        "gold_miner.data.jd_accumulation_gold.get_proxied_client",
        lambda **kwargs: mock_client,
    )

    fetcher = JdAccumulationGoldFetcher(history_path=tmp_path / "jd_test_history.csv")
    df = fetcher.fetch(days=5, fallback_to_sge=False)

    assert len(df) >= 1
    assert df["close"].iloc[-1] == 917.75


def test_fetch_price_failure(monkeypatch):
    """JD API 请求失败时 fetch_price 返回 None."""
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


def test_fetch_price_uses_jdgold_primary(monkeypatch, tmp_path):
    """jdgold 主源命中时优先返回 jdgold 价格 (不触发 H5)."""
    monkeypatch.setattr(
        "gold_miner.data.jd_accumulation_gold._jdgold_fetch_price",
        lambda bank: {
            "name": "民生积存金",
            "price": 958.97,
            "change_pct": "+0.04%",
            "change_amount": 0.42,
        },
    )

    fetcher = JdAccumulationGoldFetcher(history_path=tmp_path / "jd_test_history.csv")
    price = fetcher.fetch_price()

    assert isinstance(price, JdGoldPrice)
    assert price.price == 958.97
    assert price.change_pct == "+0.04%"
    assert price.source == "jdgold"
    assert price.product_name == "民生积存金"


def test_fetch_price_jdgold_fail_falls_back_to_h5(mock_client, monkeypatch, tmp_path):
    """jdgold 主源返回 None 时落回 H5 (mock 的 getFirstRelatedProductInfo)."""
    monkeypatch.setattr(
        "gold_miner.data.jd_accumulation_gold._jdgold_fetch_price", lambda bank: None
    )
    monkeypatch.setattr(
        "gold_miner.data.jd_accumulation_gold.get_proxied_client",
        lambda **kwargs: mock_client,
    )

    fetcher = JdAccumulationGoldFetcher(history_path=tmp_path / "jd_test_history.csv")
    price = fetcher.fetch_price()

    assert isinstance(price, JdGoldPrice)
    assert price.price == 917.75
    assert price.change_pct == "+0.24%"
    assert price.source == "jd.com"
