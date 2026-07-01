"""中国人民银行黄金储备数据抓取.

国家外汇管理局每月 7 号左右公布上月官方储备资产数据，包含黄金储备（万盎司）。
本模块优先从 SAFE 官网抓取，不可达时使用已知数据作为 fallback。
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from loguru import logger

from gold_miner.data.base import DataFetcher, DataSourceMeta
from gold_miner.data.economic_data import EconomicDataPoint, EconomicDataRecorder
from gold_miner.proxy import get_proxied_client


class PbocReservesFetcher(DataFetcher):
    """中国央行黄金储备获取器."""

    KNOWN_RESERVES: list[dict] = [
        {"period": "2026-01", "oz_10k": 7386, "change_oz_10k": +16},
        {"period": "2026-02", "oz_10k": 7422, "change_oz_10k": +36},
        {"period": "2026-03", "oz_10k": 7438, "change_oz_10k": +16},
        {"period": "2026-04", "oz_10k": 7464, "change_oz_10k": +26},
        {"period": "2026-05", "oz_10k": 7496, "change_oz_10k": +32},
    ]

    OZ_TO_TONNES = 32150.7  # 1 金衡盎司 = 31.1035g, 10000oz * 31.1035g / 1e6g ≈ 0.311035t

    def __init__(self, recorder: EconomicDataRecorder | None = None) -> None:
        super().__init__(
            DataSourceMeta(
                name="pboc_reserves",
                source="国家外汇管理局 / PBOC",
                frequency="monthly",
                description="中国央行黄金储备（万盎司）",
                source_tier="T0",
            )
        )
        self._recorder = recorder or EconomicDataRecorder()

    def fetch(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        **kwargs,
    ) -> pd.DataFrame:
        """获取 PBOC 黄金储备历史数据.

        优先从 SAFE 官网抓取，不可达时使用内置已知数据。
        """
        html = self._fetch_safe_html()
        if html:
            parsed = self._parse_safe_html(html)
            if parsed is not None:
                self._persist(parsed)
                records = self.KNOWN_RESERVES + [parsed]
                df = pd.DataFrame(records)
        df = pd.DataFrame(self.KNOWN_RESERVES)

        df["timestamp"] = pd.to_datetime(df["period"] + "-01")
        df["value"] = df["oz_10k"] * 10000 / self.OZ_TO_TONNES  # 换算为吨
        df = df.rename(columns={"oz_10k": "reserves_oz_10k", "change_oz_10k": "monthly_change_oz_10k"})

        result = df[["timestamp", "value", "reserves_oz_10k", "monthly_change_oz_10k", "period"]].copy()

        if not result.empty:
            latest = result.iloc[-1]
            try:
                point = EconomicDataPoint(
                    indicator="pboc_gold_reserves_tonnes",
                    release_date=datetime.now().strftime("%Y-%m-%d"),
                    observation_date=latest["timestamp"].strftime("%Y-%m-%d"),
                    period=str(latest["period"]),
                    actual=round(float(latest["value"]), 2),
                    unit="吨",
                    source="国家外汇管理局 (SAFE)",
                    source_tier="T0",
                    impact="high",
                    notes=f"中国央行黄金储备 {latest['reserves_oz_10k']} 万盎司，"
                          f"月增 {latest['monthly_change_oz_10k']} 万盎司",
                )
                self._recorder.save(point)
            except Exception as e:
                logger.warning(f"持久化 PBOC 储备数据失败: {e}")

        return result

    def fetch_latest(self) -> pd.DataFrame:
        df = self.fetch()
        if df.empty:
            return df
        return df.tail(1).reset_index(drop=True)

    def _fetch_safe_html(self) -> str | None:
        try:
            with get_proxied_client(timeout=15.0) as client:
                resp = client.get("http://m.safe.gov.cn/safe/whcb/index.html")
                resp.raise_for_status()
                return resp.content.decode(resp.encoding or "utf-8", errors="replace")
        except Exception:
            logger.debug("SAFE 官网不可达，使用内置已知数据")
            return None

    def _parse_safe_html(self, html: str) -> dict | None:
        import re
        match = re.search(r"黄金储备[^\d]*(\d{4})\s*万盎司", html)
        if not match:
            return None
        oz_10k = int(match.group(1))
        date_match = re.search(r"(\d{4})年(\d{1,2})月末", html)
        if date_match:
            period = f"{date_match.group(1)}-{int(date_match.group(2)):02d}"
        else:
            now = datetime.now()
            period = f"{now.year}-{now.month:02d}"

        prev = self.KNOWN_RESERVES[-1] if self.KNOWN_RESERVES else {"oz_10k": oz_10k}
        change = oz_10k - int(prev.get("oz_10k", oz_10k))

        return {"period": period, "oz_10k": oz_10k, "change_oz_10k": change}

    def _persist(self, parsed: dict) -> None:
        """将解析结果追加到已知数据列表中."""
        for i, record in enumerate(self.KNOWN_RESERVES):
            if record["period"] == parsed["period"]:
                self.KNOWN_RESERVES[i] = parsed
                return
        if parsed["period"] > self.KNOWN_RESERVES[-1]["period"]:
            self.KNOWN_RESERVES.append(parsed)
