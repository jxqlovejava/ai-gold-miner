# -*- coding: utf-8 -*-
"""黄金报价获取 — XAUUSD + 积存金.

策略:
  1. 优先读取本地缓存 (/tmp/gold_quote_cache.json, TTL 5min)
  2. 尝试 Sina/东方财富 API (国内可达)
  3. 回退到缓存 (延长 TTL 到 30min)
  4. 仍失败 → 空 (Hermes 静默)
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from .models import GoldQuote

BEIJING = timezone(__import__('datetime').timedelta(hours=8))
_CACHE_FILE = Path("/tmp/gold_sentinel_quote_cache.json")
_CACHE_TTL = 300  # 5 分钟 (正常)
_CACHE_TTL_FALLBACK = 1800  # 30 分钟 (网络不可用时)


def _now() -> datetime:
    return datetime.now(BEIJING)


def _read_cache(max_age: int = _CACHE_TTL) -> Optional[dict]:
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
    try:
        _CACHE_FILE.write_text(json.dumps({
            "ts": time.time(),
            "xauusd": xauusd_price,
            "xauusd_change": xauusd_change,
            "jd": jd_price,
            "jd_change": jd_change,
        }))
    except Exception:
        pass


def fetch_quotes(bank: str = "MS") -> list[GoldQuote]:
    """同步获取黄金报价.

    返回最多 2 条: XAUUSD + 积存金.
    全部失败返回空列表 (Hermes 静默).
    """

    xauusd = _fetch_xauusd_cn()
    usdcny = _fetch_usdcny_cn()

    if xauusd:
        cache = _read_cache(_CACHE_TTL)
        if not cache:
            _write_cache(
                xauusd_price=xauusd["price"],
                xauusd_change=xauusd["change_pct"],
                jd_price=round(xauusd["price"] * usdcny / 31.1035 * 1.005, 2),
                jd_change=xauusd["change_pct"],
            )
    else:
        # 网络不可用, 读缓存 (延长 TTL)
        cache = _read_cache(_CACHE_TTL_FALLBACK)
        if not cache:
            return []
        prev_close = cache["xauusd"] / (1 + cache["xauusd_change"] / 100) \
            if cache["xauusd_change"] != -100 else cache["xauusd"]

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
                prev_close=round(cache["jd"] / (1 + cache["jd_change"] / 100), 2)
                if cache["jd_change"] != -100 else cache["jd"],
                source="cache(stale)",
                fetched_at=_now(),
            ),
        ]

    prev_close = xauusd["prev_close"]
    jd_price = round(xauusd["price"] * usdcny / 31.1035 * 1.005, 2)

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
            change_pct=xauusd["change_pct"],
            prev_close=round(prev_close * usdcny / 31.1035 * 1.005, 2),
            source="XAUUSD换算",
            fetched_at=_now(),
        ),
    ]


def _fetch_xauusd_cn() -> Optional[dict]:
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


def _from_sina() -> Optional[dict]:
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


def _from_eastmoney() -> Optional[dict]:
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
    """USD/CNY 汇率. 默认 ~7.28."""
    return 7.28
