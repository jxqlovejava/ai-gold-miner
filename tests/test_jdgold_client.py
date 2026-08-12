"""jdgold_client 数据封装测试 (mock subprocess 输出).

jdgold_client 通过 subprocess(cwd=skill scripts/) 调用 jdgold 免登录脚本,
测试统一 mock _run_script 的返回值, 验证各 fetch 的解析逻辑。
"""
from __future__ import annotations

import pandas as pd
import pytest

from gold_miner.data import jdgold_client


@pytest.fixture(autouse=True)
def patch_scripts_dir(monkeypatch, tmp_path):
    """默认让 _scripts_dir 指向一个存在目录, 使 fetch 走 _run_script 分支."""
    monkeypatch.setattr(
        jdgold_client, "_scripts_dir", lambda: tmp_path / "scripts"
    )
    return tmp_path


# ── 积存金实时价 ────────────────────────────────────────────────

def test_fetch_accumulation_price_parses_quotes(monkeypatch):
    """正常解析 query_gold_analysis 的 quotes 输出."""
    monkeypatch.setattr(
        jdgold_client,
        "_run_script",
        lambda *a, **k: {
            "success": True,
            "data": {"route": "realtime_price", "quotes": [
                {
                    "uniqueCode": "CMBC-JCJ",
                    "name": "民生积存金",
                    "lastPrice": "958.97",
                    "raise": "+0.42",
                    "raisePercent": "+0.04%",
                    "tradeTime": "1786550538000",
                    "unit": "元/克",
                }
            ]},
        },
    )

    info = jdgold_client.fetch_accumulation_price("MS")

    assert info is not None
    assert info["price"] == 958.97
    assert info["change_pct"] == "+0.04%"
    assert info["change_amount"] == 0.42
    assert info["prev_close"] == pytest.approx(958.55)
    assert info["source"] == "jdgold"


def test_fetch_accumulation_price_unsupported_bank():
    """非 MS/ZS 银行 → 返回 None (调用方落 H5)."""
    assert jdgold_client.fetch_accumulation_price("ZX") is None


def test_fetch_accumulation_price_invalid_price(monkeypatch):
    """价格非法/非正 → None."""
    monkeypatch.setattr(
        jdgold_client, "_run_script",
        lambda *a, **k: {"success": True, "data": {"quotes": [
            {"lastPrice": "0.0", "raisePercent": "+0.0%"}
        ]}},
    )
    assert jdgold_client.fetch_accumulation_price("MS") is None


def test_fetch_accumulation_quote_compat_shape(monkeypatch):
    """fetch_accumulation_quote 返回 {price, prev_close, change_pct, source} 兼容形状."""
    monkeypatch.setattr(
        jdgold_client,
        "fetch_accumulation_price",
        lambda bank: {
            "name": "民生积存金", "price": 958.97, "change_pct": "+0.04%",
            "change_amount": 0.42, "prev_close": 958.55, "unit": "元/克",
            "source": "jdgold",
        },
    )

    quote = jdgold_client.fetch_accumulation_quote("MS")

    assert quote["price"] == 958.97
    assert quote["prev_close"] == 958.55
    assert quote["change_pct"] == 0.04  # float 百分数
    assert quote["source"] == "jdgold"


def test_fetch_accumulation_quote_falls_back_to_h5(monkeypatch):
    """jdgold 主源失败 → latestPrice H5 兜底."""
    monkeypatch.setattr(
        jdgold_client, "fetch_accumulation_price", lambda bank: None
    )
    monkeypatch.setattr(
        jdgold_client,
        "_h5_latest_price_fallback",
        lambda: {"price": 959.0, "prev_close": 958.0, "change_pct": 0.1, "source": "京东金融"},
    )

    quote = jdgold_client.fetch_accumulation_quote("MS")

    assert quote["price"] == 959.0
    assert quote["source"] == "京东金融"


# ── SGE 实时 + K 线 ─────────────────────────────────────────────

def test_fetch_sge_quote_parses(monkeypatch):
    """jdjr_query_stock quote 输出解析 (changeRatio 为小数)."""
    monkeypatch.setattr(
        jdgold_client,
        "_run_script",
        lambda *a, **k: {"success": True, "data": {
            "currentPrice": "960.00", "open": "955.00", "maxPrice": "963.00",
            "minPrice": "955.00", "closedYesterday": "955.75",
            "changePrice": "4.25", "changeRatio": "0.004447",
            "volume": "6932", "stockName": "黄金9999",
        }},
    )

    quote = jdgold_client.fetch_sge_quote()

    assert quote["price"] == 960.0
    assert quote["prev_close"] == 955.75
    assert quote["change_pct"] == pytest.approx(0.44)  # 0.004447*100
    assert quote["name"] == "黄金9999"


def test_fetch_sge_kline_builds_dataframe(monkeypatch):
    """jdjr_query_stock kline 输出组装为 OHLCV DataFrame."""
    monkeypatch.setattr(
        jdgold_client,
        "_run_script",
        lambda *a, **k: {"success": True, "data": {"kLineDtoList": [
            {"date": "2025-08-13", "open": "773.00", "high": "775.48",
             "low": "771.00", "close": "775.00", "volume": "651036"},
            {"date": "2025-08-14", "open": "774.45", "high": "776.98",
             "low": "774.00", "close": "775.03", "volume": "472512"},
        ]}},
    )

    df = jdgold_client.fetch_sge_kline("day")

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert set(df.columns) >= {"timestamp", "open", "high", "low", "close", "volume"}
    assert df["close"].iloc[-1] == 775.03


# ── 资讯 ────────────────────────────────────────────────────────

def test_fetch_news_parses(monkeypatch):
    """jdjr_query_news --no-flash 输出解析."""
    monkeypatch.setattr(
        jdgold_client,
        "_run_script",
        lambda *a, **k: {"success": True, "data": {"count": 1, "news": [
            {"time": "2026-08-13", "title": "CPI 快讯", "content": "摘要", "url": "https://x"}
        ]}},
    )

    news = jdgold_client.fetch_news("黄金", 1)

    assert news is not None
    assert news[0]["title"] == "CPI 快讯"


# ── 脚本缺失降级 ────────────────────────────────────────────────

def test_scripts_missing_returns_none(monkeypatch):
    """scripts 目录缺失 → 免登录数据返回 None (调用方落 H5)."""
    monkeypatch.setattr(jdgold_client, "_scripts_dir", lambda: None)
    monkeypatch.setattr(jdgold_client, "_h5_latest_price_fallback", lambda: None)

    assert jdgold_client.fetch_accumulation_price("MS") is None
    assert jdgold_client.fetch_sge_quote() is None
    assert jdgold_client.fetch_sge_kline("day") is None
    assert jdgold_client.fetch_news("黄金", 1) is None


# ── H5 兜底解析 ─────────────────────────────────────────────────

def test_h5_latest_price_fallback_parses(monkeypatch):
    """latestPrice H5 兜底解析 {price, yesterdayPrice} → 兼容形状."""
    import httpx

    class FakeResp:
        status_code = 200

        def json(self):
            return {"success": True, "resultData": {"datas": {
                "price": "959.00", "yesterdayPrice": "958.00",
            }}}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResp())

    quote = jdgold_client._h5_latest_price_fallback()

    assert quote is not None
    assert quote["price"] == 959.0
    assert quote["prev_close"] == 958.0
    assert quote["change_pct"] == pytest.approx((959 - 958) / 958 * 100, abs=0.01)
    assert quote["source"] == "京东金融"


def test_h5_latest_price_fallback_failure(monkeypatch):
    """H5 请求异常 → None."""
    import httpx

    def boom(*a, **k):
        raise OSError("network")

    monkeypatch.setattr(httpx, "get", boom)

    assert jdgold_client._h5_latest_price_fallback() is None
