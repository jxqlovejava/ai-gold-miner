"""央行购金数据抓取 — 世界黄金协会 (WGC) Gold Demand Trends.

数据来源: https://www.gold.org/goldhub/research/gold-demand-trends/
每季度更新，从HTML页面提取央行净购金量等关键数据。
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from gold_miner.proxy import get_proxied_client

WGC_GDT_URL = "https://www.gold.org/goldhub/research/gold-demand-trends/gold-demand-trends-q1-2026"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@dataclass
class CentralBankData:
    """央行购金数据."""

    quarter: str  # e.g. "Q1 2026"
    net_purchases_tonnes: float  # 净购金量 (吨)
    yoy_change_pct: float  # 同比变化 (%)
    total_demand_tonnes: float | None = None  # 全球总需求 (吨)
    avg_price_usd: float | None = None  # 季度均价 (USD/oz)
    etf_flow_tonnes: float | None = None  # ETF 流量 (吨)
    bar_coin_tonnes: float | None = None  # 金条金币需求 (吨)
    source_url: str = ""
    fetched_at: datetime | None = None

    @property
    def is_buying(self) -> bool:
        return self.net_purchases_tonnes > 0

    @property
    def is_significant(self) -> bool:
        """季度购金 > 100吨视为显著."""
        return self.net_purchases_tonnes > 100


class CentralBankFetcher:
    """央行购金数据获取器.

    用法:
        fetcher = CentralBankFetcher()
        data = fetcher.fetch()
        print(f"Q1 2026 央行净购金: {data.net_purchases_tonnes}t")
    """

    def __init__(self, url: str = WGC_GDT_URL) -> None:
        self.url = url

    def fetch(self) -> CentralBankData | None:
        """从WGC页面抓取最新央行购金数据."""
        html = self._get_html(self.url)
        if not html:
            return self._fallback_data()

        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator=" ")

        # 提取央行购金量: "Central banks bought 244t"
        cb_match = re.search(
            r"Central\s+banks?\s+(?:bought|purchased|added)\s+(\d+)\s*t",
            text, re.IGNORECASE,
        )
        # 也匹配 "net purchases of Xt"
        if not cb_match:
            cb_match = re.search(
                r"net\s+(?:purchases?|buying)\s+(?:of\s+)?(\d+)\s*t",
                text, re.IGNORECASE,
            )
        # 匹配 "central banks.*?(\d+)t"
        if not cb_match:
            cb_match = re.search(
                r"central\s+banks.*?(\d{3,4})\s*t",
                text, re.IGNORECASE,
            )

        net_tonnes = float(cb_match.group(1)) if cb_match else 0.0

        # 同比变化
        yoy_match = re.search(
            r"(?:central\s+banks?\s*(?:bought|purchased).*?|net\s+purchases.*?)"
            r"([+-]\d+%)\s*(?:y/y|yoy|year.on.year)",
            text, re.IGNORECASE,
        )
        yoy_pct = 0.0
        if yoy_match:
            yoy_str = yoy_match.group(1).replace("%", "")
            try:
                yoy_pct = float(yoy_str) / 100
            except ValueError:
                yoy_pct = 0.0

        # 总需求: "Total Q1 gold demand... was... 1,231t"
        demand_match = re.search(
            r"Total\s+Q\d\s+gold\s+demand.*?(\d{1,3}(?:,\d{3}){1,2})\s*t",
            text, re.IGNORECASE,
        )
        total_demand = None
        if demand_match:
            total_demand = float(demand_match.group(1).replace(",", ""))

        # 均价: "quarterly average record of US$4,873/oz"
        price_match = re.search(
            r"quarterly\s+average.*?US?\$(\d{1,3}(?:,\d{3}){1,2})\s*(?:/oz|per\s+ounce)",
            text, re.IGNORECASE,
        )
        avg_price = None
        if price_match:
            avg_price = float(price_match.group(1).replace(",", ""))

        # ETF: "gold-backed ETFs continued in Q1 (+62t)"
        etf_match = re.search(
            r"gold.backed\s+ETFs?.*?\(?([+-]\d+)\s*t\)?",
            text, re.IGNORECASE,
        )
        etf_flow = None
        if etf_match:
            etf_flow = float(etf_match.group(1))

        # 金条金币: "Bar and coin demand of 474t (+42%)"
        bc_match = re.search(
            r"Bar\s*(?:and|&)\s*coin\s*demand\s*(?:of\s*)?(\d{1,4})\s*t",
            text, re.IGNORECASE,
        )
        bar_coin = None
        if bc_match:
            bar_coin = float(bc_match.group(1))

        # 提取季度
        quarter = "Q1 2026"
        q_match = re.search(r"Q([1-4])\s*(?:20)?(\d{2})", self.url)
        if q_match:
            quarter = f"Q{q_match.group(1)} 20{q_match.group(2)}"

        logger.info(
            f"央行购金数据: {quarter} 净购金 {net_tonnes}t "
            f"(同比 {yoy_pct:+.0%})"
        )

        return CentralBankData(
            quarter=quarter,
            net_purchases_tonnes=net_tonnes,
            yoy_change_pct=yoy_pct,
            total_demand_tonnes=total_demand,
            avg_price_usd=avg_price,
            etf_flow_tonnes=etf_flow,
            bar_coin_tonnes=bar_coin,
            source_url=self.url,
            fetched_at=datetime.now(),
        )

    def _get_html(self, url: str) -> str | None:
        """获取页面HTML."""
        try:
            with get_proxied_client(timeout=20) as client:
                resp = client.get(url, headers=HEADERS, follow_redirects=True)
                resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning(f"WGC页面请求失败: {e}")
            return None

        for encoding in [resp.encoding, "utf-8"]:
            if encoding is None:
                continue
            try:
                return resp.content.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue

        return resp.content.decode("utf-8", errors="replace")

    @staticmethod
    def _fallback_data() -> CentralBankData | None:
        """当网络不可用时返回已知的最新数据."""
        logger.warning("无法获取最新WGC数据，使用已知数据")
        # Q1 2026 known data from WGC
        return CentralBankData(
            quarter="Q1 2026",
            net_purchases_tonnes=244.0,
            yoy_change_pct=0.03,
            total_demand_tonnes=1231.0,
            avg_price_usd=4873.0,
            etf_flow_tonnes=62.0,
            bar_coin_tonnes=474.0,
            source_url="fallback (cached Q1 2026 data)",
            fetched_at=datetime.now(),
        )
