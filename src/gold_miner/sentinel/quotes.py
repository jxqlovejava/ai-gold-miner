"""黄金报价获取 — XAUUSD + 积存金.

策略:
  1. 优先读取本地缓存 (/tmp/gold_quote_cache.json, TTL 5min)
  2. XAUUSD: 尝试 Sina/东方财富 API (国内可达)
  3. 积存金: 优先京东金融 H5 接口 (真实价格), 不可用时回退 XAUUSD 换算
  4. 回退到缓存 (延长 TTL 到 30min)
  5. 仍失败 → 空 (Hermes 静默)

🆕 2026-07-21: 积存金价格不再用 XAUUSD×汇率÷31.1035×溢价 的死公式估算，
改为直接调京东金融 H5 接口获取真实报价。根因：死公式 ¥956 vs 实际 ¥886，偏差 ~8%。
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .models import GoldQuote

BEIJING = timezone(__import__('datetime').timedelta(hours=8))
_CACHE_FILE = Path("/tmp/gold_sentinel_quote_cache.json")
_CACHE_TTL = 300  # 5 分钟 (正常)
_CACHE_TTL_FALLBACK = 1800  # 30 分钟 (网络不可用时)

# 积存金价格源已收口至 gold_miner.data.jdgold_client (jdgold 主源 → latestPrice H5 兜底)


def _now() -> datetime:
    return datetime.now(BEIJING)


def _read_cache(max_age: int = _CACHE_TTL) -> dict | None:
    """读取缓存."""
    if not _CACHE_FILE.exists():
        return None
    try:
        data = json.loads(_CACHE_FILE.read_text())
        age = time.time() - data.get("ts", 0)
        if age < max_age:
            return data
    except Exception:
        pass
    return None


def _write_cache(xauusd_price: float, xauusd_change: float,
                 jd_price: float, jd_change: float) -> None:
    """写入缓存."""
    with contextlib.suppress(Exception):
        _CACHE_FILE.write_text(json.dumps({
            "ts": time.time(),
            "xauusd": xauusd_price,
            "xauusd_change": xauusd_change,
            "jd": jd_price,
            "jd_change": jd_change,
        }))


def fetch_quotes(bank: str = "MS") -> list[GoldQuote]:
    """同步获取黄金报价.

    返回最多 2 条: XAUUSD + 积存金.
    积存金优先使用京东金融真实价格，不可用时回退 XAUUSD 换算.
    全部失败返回空列表 (Hermes 静默).
    """

    xauusd = _fetch_xauusd_cn()
    usdcny = _fetch_usdcny_cn()
    jd_quote = _fetch_jd_gold()

    if xauusd:
        # 积存金: 优先真实价格，不可用则 XAUUSD 换算
        if jd_quote:
            jd_price = jd_quote["price"]
            jd_change = jd_quote["change_pct"]
            jd_source = jd_quote["source"]
            jd_prev = jd_quote["prev_close"]
        else:
            jd_price = round(xauusd["price"] * usdcny / 31.1035 * 1.005, 2)
            jd_change = xauusd["change_pct"]
            jd_source = "XAUUSD换算(JD不可用)"
            jd_prev = round(xauusd["prev_close"] * usdcny / 31.1035 * 1.005, 2)

        cache = _read_cache(_CACHE_TTL)
        if not cache:
            _write_cache(
                xauusd_price=xauusd["price"],
                xauusd_change=xauusd["change_pct"],
                jd_price=jd_price,
                jd_change=jd_change,
            )
    else:
        # 网络不可用, 读缓存 (延长 TTL)
        cache = _read_cache(_CACHE_TTL_FALLBACK)
        if not cache:
            return []
        prev_close = cache["xauusd"] / (1 + cache["xauusd_change"] / 100) \
            if cache["xauusd_change"] != -100 else cache["xauusd"]
        jd_prev = cache["jd"] / (1 + cache["jd_change"] / 100) \
            if cache["jd_change"] != -100 else cache["jd"]

        return [
            GoldQuote(
                symbol="XAUUSD",
                price=cache["xauusd"],
                currency="USD",
                change_pct=cache["xauusd_change"],
                prev_close=round(prev_close, 2),
                source="cache(stale)",
                fetched_at=_now(),
            ),
            GoldQuote(
                symbol=f"积存金({bank})",
                price=cache["jd"],
                currency="CNY",
                change_pct=cache["jd_change"],
                prev_close=round(jd_prev, 2),
                source="cache(stale)",
                fetched_at=_now(),
            ),
        ]

    prev_close = xauusd["prev_close"]

    return [
        GoldQuote(
            symbol="XAUUSD",
            price=xauusd["price"],
            currency="USD",
            change_pct=xauusd["change_pct"],
            prev_close=prev_close,
            source=xauusd["source"],
            fetched_at=_now(),
        ),
        GoldQuote(
            symbol=f"积存金({bank})",
            price=jd_price,
            currency="CNY",
            change_pct=jd_change,
            prev_close=jd_prev,
            source=jd_source,
            fetched_at=_now(),
        ),
    ]


def _fetch_jd_gold() -> dict | None:
    """积存金当前价 — jdgold 主源 (免登录) → latestPrice H5 兜底.

    返回 {price, prev_close, change_pct, source} (与下游 fetch_quotes 约定一致)。
    集成背景: docs/analysis/jdgold-integration-analysis-2026-08-12.md
    """
    from gold_miner.data.jdgold_client import fetch_accumulation_quote

    return fetch_accumulation_quote("MS")


def _fetch_xauusd_cn() -> dict | None:
    """国内可访问的 XAUUSD 数据源.

    依次尝试:
      1. 新浪财经 (hf_XAU)
      2. 金投网 API
    """

    # 1. 新浪财经
    result = _from_sina()
    if result:
        return result

    # 2. 东方财富现货黄金
    result = _from_eastmoney()
    if result:
        return result

    return None


def _from_sina() -> dict | None:
    """新浪财经国际黄金 (hf_XAU).

    格式: var hq_str_hf_XAU="最新价,今开盘,昨收盘?,最高,最低,时间,昨结算,涨跌额,...";
    实际字段: 0=最新价, 1=今开盘, 2=(?), 3=(?), 4=最高, 5=最低, 6=时间, 7=昨结算
    """
    try:
        resp = httpx.get(
            "https://hq.sinajs.cn/list=hf_XAU",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn/",
            },
            timeout=8.0,
        )
        if resp.status_code != 200:
            return None
        text = resp.text
        # 提取引号内数据
        match = re.search(r'"([^"]+)"', text)
        if not match:
            return None
        fields = match.group(1).split(",")
        if len(fields) < 8:
            return None

        price = float(fields[0])  # 最新价
        # 昨结算 = field[7], 若不可用则回退到 field[1] (今开盘)
        prev = float(fields[7]) if fields[7] and float(fields[7]) > 0 else float(fields[1])

        if price <= 0 or prev <= 0:
            return None

        change_pct = (price - prev) / prev * 100
        return {
            "price": round(price, 2),
            "prev_close": round(prev, 2),
            "change_pct": round(change_pct, 2),
            "source": "新浪财经",
        }
    except Exception:
        pass
    return None


def _from_eastmoney() -> dict | None:
    """东方财富黄金现货."""
    try:
        # 东方财富 Au99.99 → 上海金, 需换算成 XAUUSD
        # 直接用国际金行情接口
        resp = httpx.get(
            "https://push2.eastmoney.com/api/qt/stock/get"
            "?secid=113.1&fields=f43,f44,f45,f46,f57,f58,f60,f169,f170",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8.0,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("rc") != 0:
            return None
        d = data.get("data", {})
        price = d.get("f43", 0) / 100  # 分的单位
        prev_close = d.get("f60", 0) / 100
        if price <= 0:
            return None
        change_pct = (price - prev_close) / prev_close * 100 if prev_close > 0 else 0
        return {
            "price": round(price, 2),
            "prev_close": round(prev_close, 2),
            "change_pct": round(change_pct, 2),
            "source": "东方财富",
        }
    except Exception:
        pass
    return None


def _fetch_usdcny_cn() -> float:
    """USD/CNY 实时汇率 (国内可访问).

    依次尝试:
      1. 腾讯外汇 whUSDCNY — 字段 [3]=最新价, [7]=昨收
      2. 新浪外汇 fx_susdcny — 字段 [1]=最新价 (在岸人民币)
    全部失败返回默认 7.28 (过时, 仅兜底).
    """
    # 1. 腾讯外汇
    try:
        resp = httpx.get(
            "https://qt.gtimg.cn/q=whUSDCNY",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8.0,
        )
        if resp.status_code == 200:
            fields = resp.text.split("~")
            if len(fields) > 3:
                rate = float(fields[3])
                if 5.0 < rate < 9.0:
                    return rate
    except Exception:
        pass

    # 2. 新浪外汇 (在岸人民币)
    try:
        resp = httpx.get(
            "https://hq.sinajs.cn/list=fx_susdcny",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn/",
            },
            timeout=8.0,
        )
        if resp.status_code == 200:
            match = re.search(r'"([^"]+)"', resp.text)
            if match:
                fields = match.group(1).split(",")
                if len(fields) > 1:
                    rate = float(fields[1])
                    if 5.0 < rate < 9.0:
                        return rate
    except Exception:
        pass

    return 7.28
