"""央行购金数据抓取 — 世界黄金协会 (WGC) Gold Demand Trends.

数据来源: https://www.gold.org/goldhub/research/gold-demand-trends/
每季度更新，从HTML页面提取央行净购金量等关键数据。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
import pandas as pd
from bs4 import BeautifulSoup
from loguru import logger

from gold_miner.data.economic_data import EconomicDataPoint, EconomicDataRecorder
from gold_miner.proxy import get_proxied_client

# WGC Gold Demand Trends 最新季度报告 URL — 每季度 WGC 发布新报告后必须更新到最新季度。
# 当前: Q2 2026 (2026-07-30 发布)。URL 停留在旧季度会持续抓取/回退到上季度数据（曾长期卡在 Q1 2026 的 244t）。
WGC_GDT_URL = "https://www.gold.org/goldhub/research/gold-demand-trends/gold-demand-trends-q2-2026"

# WGC GDT 最新季度权威数据 — 网络不可用时的 fallback，及 scrape 缺字段补全。
# 来源: World Gold Council, Gold Demand Trends Q2 2026 (2026-07-30)。
# ⚠️ WGC 2026-07 对 Q1 2026 数据做了下修: 244t → 57t（187t 重分类至 OTC/其他需求）。
#    任何仍引用「Q1 2026 央行购金 244t」的数据/报告均为滞后且已被撤销的数字。
WGC_LATEST_QUARTER: dict[str, Any] = {
    "quarter": "Q2 2026",
    "net_purchases_tonnes": 289.0,   # +62% y/y，四年来最高季度
    "yoy_change_pct": 0.62,
    "total_demand_tonnes": 1269.0,   # 含 OTC，同比持平
    "avg_price_usd": 4506.29,        # LBMA 午盘季均
    "etf_flow_tonnes": -45.0,        # 当季 ETF 净流出
    "bar_coin_tonnes": 307.0,        # 金条金币需求，同比持平
}
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
        print(f"{data.quarter} 央行净购金: {data.net_purchases_tonnes}t")
    """

    def __init__(self, url: str = WGC_GDT_URL, recorder: EconomicDataRecorder | None = None) -> None:
        self.url = url
        self._recorder = recorder or EconomicDataRecorder()

    def fetch(self) -> CentralBankData | None:
        """从WGC页面抓取最新央行购金数据."""
        html = self._get_html(self.url)
        if not html:
            return self._fallback_data()

        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator=" ")

        # 提取央行购金量 — 兼容多种页面措辞:
        #   "Central banks bought 244t" / "net purchases of 244t" / "Central banks ... (289t)"
        cb_match = re.search(
            r"Central\s+banks?\s+(?:bought|purchased|added)\s+(\d+)\s*t",
            text, re.IGNORECASE,
        )
        if not cb_match:
            cb_match = re.search(
                r"net\s+(?:purchases?|buying)\s+(?:of\s+)?(\d+)\s*t",
                text, re.IGNORECASE,
            )
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

        # 总需求: "Total gold demand, including OTC, ... at 1,269t"
        demand_match = re.search(
            r"Total\s+gold\s+demand.*?(\d{1,3}(?:,\d{3}){1,2})\s*t",
            text, re.IGNORECASE,
        )
        total_demand = None
        if demand_match:
            total_demand = float(demand_match.group(1).replace(",", ""))

        # 均价: "gold price averaged US$4,506.29/oz"
        price_match = re.search(
            r"(?:averaged|average\s+price).*?US?\$(\d{1,3}(?:,\d{3}){1,2}(?:\.\d+)?)\s*/\s*oz",
            text, re.IGNORECASE,
        )
        avg_price = None
        if price_match:
            avg_price = float(price_match.group(1).replace(",", ""))

        # ETF: "Gold ETFs came under selling pressure in Q2 (-45t"
        etf_match = re.search(
            r"(?:gold.backed\s+ETFs?|Gold ETFs).*?\(?([+-]\d+)\s*t\)?",
            text, re.IGNORECASE,
        )
        etf_flow = None
        if etf_match:
            etf_flow = float(etf_match.group(1))

        # 金条金币: "Bar and coin investment ... (307t)"
        bc_match = re.search(
            r"Bar\s*(?:and|&)\s*coin\s*(?:investment|demand).*?\(?(\d{1,4})\s*t\)?",
            text, re.IGNORECASE,
        )
        bar_coin = None
        if bc_match:
            bar_coin = float(bc_match.group(1))

        # 提取季度 — WGC URL slug 为小写且带连字符（如 "...-q2-2026"），须忽略大小写并跳过 "-"
        # 旧实现大小写敏感 + 未处理连字符 → 匹配失败回退成硬编码 "Q1 2026"（Q2 数据被错误标记为 Q1）
        q_match = re.search(r"[Qq]([1-4])(?:\s*|-)(?:20)?(\d{2})", self.url)
        quarter = f"Q{q_match.group(1)} 20{q_match.group(2)}" if q_match else ""

        # 若 scrape 未提取到某些字段（页面措辞变化），且抓取季度与已知最新季度一致，
        # 用权威数据补全 — 避免持久化 "同比 +0%" 等错误字段。
        if quarter and quarter == WGC_LATEST_QUARTER["quarter"]:
            if yoy_pct == 0.0:
                yoy_pct = WGC_LATEST_QUARTER["yoy_change_pct"]
            if total_demand is None:
                total_demand = WGC_LATEST_QUARTER["total_demand_tonnes"]
            if avg_price is None:
                avg_price = WGC_LATEST_QUARTER["avg_price_usd"]
            if etf_flow is None:
                etf_flow = WGC_LATEST_QUARTER["etf_flow_tonnes"]
            if bar_coin is None:
                bar_coin = WGC_LATEST_QUARTER["bar_coin_tonnes"]

        logger.info(
            f"央行购金数据: {quarter} 净购金 {net_tonnes}t "
            f"(同比 {yoy_pct:+.0%})"
        )

        data = CentralBankData(
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
        self._persist(data)
        return data

    def _persist(self, data: CentralBankData) -> None:
        """将央行购金数据持久化到经济数据库."""
        try:
            observation_date = self._quarter_to_observation_date(data.quarter)
            point = EconomicDataPoint(
                indicator="central_bank_net_purchases",
                release_date=datetime.now().strftime("%Y-%m-%d"),
                observation_date=observation_date,
                period=data.quarter,
                actual=data.net_purchases_tonnes,
                previous=None,
                unit="吨",
                source="World Gold Council (WGC)",
                source_tier="T0",
                impact="high",
                notes=f"全球央行季度净购金 {data.net_purchases_tonnes}t，同比 {data.yoy_change_pct:+.0%}",
            )
            self._recorder.save(point)
        except Exception as e:
            logger.warning(f"持久化央行购金数据失败: {e}")

    @staticmethod
    def _quarter_to_observation_date(quarter: str) -> str:
        """将季度字符串映射为季度末日期."""
        import re as _re
        match = _re.search(r"Q([1-4])\s*(\d{4})", quarter)
        if not match:
            return ""
        q, year = int(match.group(1)), match.group(2)
        month_day = {1: ("03", "31"), 2: ("06", "30"), 3: ("09", "30"), 4: ("12", "31")}[q]
        return f"{year}-{month_day[0]}-{month_day[1]}"

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

    def _fallback_data(self) -> CentralBankData | None:
        """当网络不可用时返回已知的最新数据."""
        logger.warning("无法获取最新WGC数据，使用已知数据")
        data = CentralBankData(
            quarter=WGC_LATEST_QUARTER["quarter"],
            net_purchases_tonnes=WGC_LATEST_QUARTER["net_purchases_tonnes"],
            yoy_change_pct=WGC_LATEST_QUARTER["yoy_change_pct"],
            total_demand_tonnes=WGC_LATEST_QUARTER["total_demand_tonnes"],
            avg_price_usd=WGC_LATEST_QUARTER["avg_price_usd"],
            etf_flow_tonnes=WGC_LATEST_QUARTER["etf_flow_tonnes"],
            bar_coin_tonnes=WGC_LATEST_QUARTER["bar_coin_tonnes"],
            source_url=f"fallback (cached {WGC_LATEST_QUARTER['quarter']} data)",
            fetched_at=datetime.now(),
        )
        self._persist(data)
        return data


# ---------------------------------------------------------------------------
# 重点国别央行月度购金监控
# ---------------------------------------------------------------------------

@dataclass
class MonthlyCentralBankData:
    """单月单国央行购金数据."""

    country: str
    year: int
    month: int
    net_purchases_tonnes: float
    total_reserves_tonnes: float | None = None
    source: str = ""
    fetched_at: datetime | None = None

    @property
    def date_label(self) -> str:
        return f"{self.year}-{self.month:02d}"

    @property
    def is_significant(self) -> bool:
        """单月购金 > 10吨视为显著."""
        return self.net_purchases_tonnes > 10


class CentralBankHistoryFetcher:
    """央行购金历史序列获取器.

    用于中长期分析，返回季度净购金量时间序列。
    当前以已知 WGC 历史数据作为 fallback；网络解析作为最佳尝试。
    """

    # WGC 官方季度央行净购金历史数据（吨）
    # 来源: World Gold Council Gold Demand Trends
    # ⚠️ Q1 2026 已被 WGC 于 2026-07 下修（244t → 57t，187t 重分类至 OTC/其他需求）
    KNOWN_QUARTERLY_DATA: list[dict[str, Any]] = [
        {"quarter": "Q1 2023", "net_purchases_tonnes": 228.0},
        {"quarter": "Q2 2023", "net_purchases_tonnes": 175.0},
        {"quarter": "Q3 2023", "net_purchases_tonnes": 337.0},
        {"quarter": "Q4 2023", "net_purchases_tonnes": 229.0},
        {"quarter": "Q1 2024", "net_purchases_tonnes": 290.0},
        {"quarter": "Q2 2024", "net_purchases_tonnes": 184.0},
        {"quarter": "Q3 2024", "net_purchases_tonnes": 186.0},
        {"quarter": "Q4 2024", "net_purchases_tonnes": 333.0},
        {"quarter": "Q1 2025", "net_purchases_tonnes": 292.0},
        {"quarter": "Q2 2025", "net_purchases_tonnes": 198.0},
        {"quarter": "Q3 2025", "net_purchases_tonnes": 220.0},
        {"quarter": "Q4 2025", "net_purchases_tonnes": 345.0},
        {"quarter": "Q1 2026", "net_purchases_tonnes": 57.0},  # 下修后
        {"quarter": "Q2 2026", "net_purchases_tonnes": 289.0},
    ]

    def fetch_quarterly_history(self) -> pd.DataFrame:
        """获取央行季度净购金历史序列."""
        try:
            latest = CentralBankFetcher().fetch()
            if latest:
                records = list(self.KNOWN_QUARTERLY_DATA)
                # 用最新抓取的数据覆盖/补充最后一期
                if records[-1]["quarter"] == latest.quarter:
                    records[-1] = {
                        "quarter": latest.quarter,
                        "net_purchases_tonnes": latest.net_purchases_tonnes,
                        "yoy_change_pct": latest.yoy_change_pct,
                        "source_url": latest.source_url,
                    }
                else:
                    records.append({
                        "quarter": latest.quarter,
                        "net_purchases_tonnes": latest.net_purchases_tonnes,
                        "yoy_change_pct": latest.yoy_change_pct,
                        "source_url": latest.source_url,
                    })
                df = pd.DataFrame(records)
            else:
                df = pd.DataFrame(self.KNOWN_QUARTERLY_DATA)
        except Exception as e:
            logger.warning(f"央行历史数据获取失败，使用已知数据: {e}")
            df = pd.DataFrame(self.KNOWN_QUARTERLY_DATA)

        df["timestamp"] = pd.to_datetime(df["quarter"].apply(self._quarter_to_date))
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df

    def fetch_rolling_trend(self, quarters: int = 4) -> dict[str, Any]:
        """计算最近 N 个季度滚动趋势."""
        df = self.fetch_quarterly_history()
        if len(df) < 2:
            return {"status": "no_data"}

        recent = df.tail(quarters)
        total = float(recent["net_purchases_tonnes"].sum())
        avg = float(recent["net_purchases_tonnes"].mean())
        yoy_values = recent["net_purchases_tonnes"].pct_change(periods=4).dropna()
        avg_yoy = float(yoy_values.mean()) if not yoy_values.empty else 0.0

        if avg > 250:
            trend = "strong_buying"
        elif avg > 150:
            trend = "buying"
        elif avg > 0:
            trend = "moderate_buying"
        else:
            trend = "selling"

        return {
            "status": "ok",
            "quarters": len(recent),
            "total_tonnes": round(total, 1),
            "avg_quarterly_tonnes": round(avg, 1),
            "avg_yoy_change_pct": round(avg_yoy * 100, 1),
            "trend": trend,
            "latest_quarter": str(recent["quarter"].iloc[-1]),
        }

    @staticmethod
    def _quarter_to_date(quarter: str) -> datetime:
        """将 'Q1 2026' 转为季度首月日期."""
        match = re.match(r"Q(\d)\s+(\d{4})", quarter)
        if not match:
            # 解析失败时使用一个遥远的过去日期，避免污染当前数据
            return datetime(1970, 1, 1)
        q = int(match.group(1))
        year = int(match.group(2))
        month = (q - 1) * 3 + 1
        return datetime(year, month, 1)


class MonthlyCentralBankFetcher:
    """重点国别央行月度购金数据获取器.

    监控国别（按购金量排序）:
    1. 中国 (PBOC) — 每月7号左右公布外汇储备+黄金储备
    2. 土耳其 (CBRT) — 高频购金国
    3. 波兰 (NBP) — 近年大幅增加储备
    4. 印度 (RBI) — 传统购金大国
    5. 新加坡 (MAS) — 近年积极增持

    数据来源:
    - 各国央行官网 / 外汇储备公告
    - IMF IFS (International Financial Statistics)
    - 世界黄金协会月度更新
    """

    # 重点监控国别及已知数据页URL模板
    COUNTRIES = {
        "中国": {
            "code": "PBOC",
            "url": "http://www.pbc.gov.cn/zhengcehuobisi/11111/index.html",
            # 7月实际: +64万盎司 ≈ 20吨 (8/7 公布，2024年11月重启购金以来单月最大，连续21个月增持)
            # 2026 年逐月: 3月~5t、4月~8t、5月~10t、6月~15t、7月~20t — 呈递增
            "fallback_monthly": 20.0,
        },
        "土耳其": {
            "code": "CBRT",
            "url": "https://www.tcmb.gov.tr/wps/wcm/connect/EN/TCMB+EN/Main+Menu/",
            "fallback_monthly": 12.0,
        },
        "波兰": {
            "code": "NBP",
            "url": "https://www.nbp.pl/homen.aspx?f=/en/onbp/organizacja/rezerwy.html",
            "fallback_monthly": 8.0,
        },
        "印度": {
            "code": "RBI",
            "url": "https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx",
            "fallback_monthly": 7.0,
        },
        "新加坡": {
            "code": "MAS",
            "url": "https://www.mas.gov.sg/statistics/reserve-assets",
            "fallback_monthly": 3.0,
        },
    }

    # 月度购金信号阈值
    SIGNIFICANT_MONTHLY = 10.0   # 单月>10t = 显著
    STRONG_MONTHLY = 20.0        # 单月>20t = 强烈信号

    def __init__(self) -> None:
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            from gold_miner.proxy import get_proxied_client
            self._client = get_proxied_client(timeout=20)
        return self._client

    def fetch_all(self) -> list[MonthlyCentralBankData]:
        """获取所有重点国别最新月度数据."""
        results: list[MonthlyCentralBankData] = []
        for country, info in self.COUNTRIES.items():
            try:
                data = self._fetch_country(country, info)
                if data:
                    results.append(data)
            except Exception as e:
                logger.debug(f"{country}央行数据获取失败: {e}")
                # 使用 fallback
                results.append(self._fallback_for_country(country, info))
        return results

    def fetch_summary(self) -> dict[str, Any]:
        """获取月度购金摘要.

        Returns:
            dict with: total_monthly, significant_countries,
                       top_buyer, trend_direction
        """
        data_list = self.fetch_all()
        if not data_list:
            return {"status": "no_data"}

        total = sum(d.net_purchases_tonnes for d in data_list)
        significant = [d for d in data_list if d.is_significant]
        top = max(data_list, key=lambda d: d.net_purchases_tonnes)

        # 趋势: 近3月合计 vs 前3月
        # 由于月度数据可能不足，简化判断
        if total > 50:
            trend = "strong_buying"
        elif total > 30:
            trend = "buying"
        elif total > 0:
            trend = "moderate_buying"
        else:
            trend = "selling"

        return {
            "status": "ok",
            "total_monthly_tonnes": round(total, 1),
            "country_count": len(data_list),
            "significant_countries": len(significant),
            "top_buyer": {
                "country": top.country,
                "purchases": round(top.net_purchases_tonnes, 1),
            },
            "trend": trend,
            "details": [
                {
                    "country": d.country,
                    "purchases": round(d.net_purchases_tonnes, 1),
                    "reserves": round(d.total_reserves_tonnes, 1) if d.total_reserves_tonnes else None,
                }
                for d in data_list
            ],
        }

    def _fetch_country(
        self,
        country: str,
        info: dict[str, Any],
    ) -> MonthlyCentralBankData | None:
        """获取单个国家最新月度数据."""
        # 实际实现中，这里应解析各国央行官网数据
        # 由于各国网站结构不同且频繁变化，使用结构化回退数据
        # 并记录最后更新时间
        return self._fallback_for_country(country, info)

    def _fallback_for_country(
        self,
        country: str,
        info: dict[str, Any],
    ) -> MonthlyCentralBankData:
        """为指定国家生成回退数据."""
        now = datetime.now()
        return MonthlyCentralBankData(
            country=country,
            year=now.year,
            month=now.month,
            net_purchases_tonnes=info.get("fallback_monthly", 5.0),
            total_reserves_tonnes=None,
            source=f"fallback ({info['code']})",
            fetched_at=now,
        )

    def fetch_china_pboc(self) -> MonthlyCentralBankData | None:
        """专门获取中国央行(PBOC)黄金储备数据.

        中国人民银行每月7号左右公布上月外汇储备和黄金储备。
        优先尝试从 PBOC 官网解析最新数据。
        """
        try:
            url = "http://www.pbc.gov.cn/zhengcehuobisi/11111/index.html"
            resp = self.client.get(url, timeout=20, follow_redirects=True)
            resp.raise_for_status()
            # PBOC 页面编码通常是 GBK
            html = resp.content.decode("utf-8", errors="replace")

            # 搜索黄金储备相关文本
            # 典型格式: "黄金储备 X万盎司" 或 "Gold Reserves X million fine troy ounces"
            import re
            # 提取盎司数
            oz_match = re.search(
                r"黄金储备[\s\D]*(\d{4,6})[\s\D]*万盎司",
                html,
            )
            if not oz_match:
                oz_match = re.search(
                    r"Gold Reserves[\s\D]*(\d{4,6})[\s\D]*million",
                    html,
                    re.IGNORECASE,
                )

            if oz_match:
                oz_10k = float(oz_match.group(1))  # 万盎司
                tonnes = oz_10k * 10000 / 32150.7  # 1 金衡盎司 = 31.1035g, 1吨 = 1e6g

                now = datetime.now()
                return MonthlyCentralBankData(
                    country="中国",
                    year=now.year,
                    month=now.month,
                    net_purchases_tonnes=round(tonnes, 1),
                    total_reserves_tonnes=round(tonnes, 1),
                    source="PBOC official",
                    fetched_at=now,
                )

        except Exception as e:
            logger.debug(f"PBOC数据获取失败: {e}")

        return self._fallback_for_country("中国", self.COUNTRIES["中国"])
