"""京东金融积存金价格抓取."""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger

from gold_miner.config import settings
from gold_miner.data.base import DataFetcher, DataSourceMeta
from gold_miner.data.jdgold_client import fetch_accumulation_price as _jdgold_fetch_price
from gold_miner.proxy import get_proxied_client

_JD_API_URL = (
    "https://ms.jr.jd.com/gw2/generic/CreatorSer/newh5/m/getFirstRelatedProductInfo"
)
# 京东金融积存金合作银行产品 ID（从 H5 页面 JS 提取）
_JD_PRODUCT_IDS: dict[str, str] = {
    "MS": "21001001000001",  # 民生积存金
    "ZS": "1961543816",      # 浙商积存金
    "ZX": "2045976593",      # 中信积存金
    "GS": "2005453243",      # 工行积存金
    "GF": "2024345112",      # 广发积存金
    "XY": "2039007297",      # 兴业积存金
}
_DEFAULT_BANK = "MS"
_DEFAULT_CIRCLE_ID = "13245"


@dataclass(frozen=True)
class JdGoldPrice:
    """京东金融积存金当前价格."""

    timestamp: datetime
    product_name: str
    price: float
    change_pct: str
    source: str

    @property
    def change_pct_float(self) -> float:
        """涨跌幅浮点数（如 '-0.96%' → -0.96）."""
        try:
            return float(self.change_pct.replace("%", "").strip())
        except (ValueError, AttributeError):
            return 0.0


class JdAccumulationGoldFetcher(DataFetcher):
    """京东金融积存金实时价格获取器.

    抓取京东金融 H5 接口返回的参考金价, 用于与 Au9999 现货价格交叉对照.
    默认抓取用户实际持有的 **民生银行积存金**.

    支持本地历史 CSV 缓存, 用于 ATR 等需要历史序列的计算.
    """

    def __init__(
        self,
        bank: str = _DEFAULT_BANK,
        product_id: str | None = None,
        circle_id: str = _DEFAULT_CIRCLE_ID,
        history_path: Path | str | None = None,
    ) -> None:
        if product_id is None:
            bank = bank.upper()
            if bank not in _JD_PRODUCT_IDS:
                raise ValueError(
                    f"不支持的银行代码: {bank}. "
                    f"支持的银行: {list(_JD_PRODUCT_IDS.keys())}"
                )
            product_id = _JD_PRODUCT_IDS[bank]

        super().__init__(
            DataSourceMeta(
                name="jd_accumulation_gold",
                source="jd.com",
                frequency="minute",
                description=f"京东金融积存金 人民币/克 ({bank})",
            )
        )
        self.bank = bank
        self.product_id = product_id
        self.circle_id = circle_id
        if history_path is None:
            # 历史 CSV 属私有持仓数据, 统一写入 data/private/ (与 storage/local.py 一致),
            # 避免散落到根目录 data/ 导致 git 跟踪混乱 (原 data_path 遗留).
            history_path = settings.private_data_path / f"jd_{bank.lower()}_gold_history.csv"
        self.history_path = Path(history_path)

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def fetch(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        days: int = 90,
        min_rows: int = 14,
        fallback_to_sge: bool = True,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """抓取积存金历史 OHLCV 数据.

        优先读取本地历史 CSV, 若数据不足且允许 fallback, 则用 SGE Au99.99 数据回填.
        """
        _ = kwargs
        end = end or datetime.now()
        start = start or (end - timedelta(days=days))

        # 1. 加载本地历史并尝试补充最新价
        #    当天已有记录也要刷新：积存金日线快照盘中多次变化（如早上 897 → 晚上 923），
        #    若只在本条缺失时补，ATR 移动止盈将基于过时价格计算，止损线无法随新高上移。
        df = self._load_history()
        if df.empty or df["timestamp"].max().date() <= end.date():
            df = self._backfill_with_latest(df)

        # 2. 如果本地历史条数仍不足 min_rows, 用 SGE 代理回填
        if (df.empty or len(df) < min_rows) and fallback_to_sge:
            logger.warning(
                f"本地京东历史仅 {len(df)} 条, 不足 {min_rows} 条, 使用 SGE Au99.99 代理回填"
            )
            sge_df = self._fetch_from_sge_proxy(start, end)
            if not sge_df.empty:
                if df.empty:
                    df = sge_df
                else:
                    # 本地历史覆盖 SGE 代理的同一天数据
                    sge_df = sge_df[
                        ~sge_df["timestamp"].dt.date.isin(df["timestamp"].dt.date)
                    ]
                    df = pd.concat([df, sge_df], ignore_index=True)

        if df.empty:
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume", "source"]
            )

        # 3. 时间过滤并排序
        df = df[(df["timestamp"] >= pd.Timestamp(start)) &
                (df["timestamp"] <= pd.Timestamp(end))]
        df = df.sort_values("timestamp").reset_index(drop=True)

        return self.validate(df)

    def fetch_latest(self) -> pd.DataFrame:
        """抓取最近 5 天积存金数据."""
        return self.fetch(days=5)

    def fetch_price(self) -> JdGoldPrice | None:
        """获取当前积存金价格对象."""
        return self._fetch_price_info()

    def update_history(self) -> JdGoldPrice | None:
        """抓取最新价格并追加到本地历史 CSV."""
        price_info = self._fetch_price_info()
        if price_info is None:
            return None

        self._append_history(price_info)
        return price_info

    # ------------------------------------------------------------------
    # 历史数据管理
    # ------------------------------------------------------------------

    def _load_history(self) -> pd.DataFrame:
        """从本地 CSV 加载历史数据."""
        if not self.history_path.exists():
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume", "source"]
            )

        try:
            df = pd.read_csv(self.history_path, parse_dates=["timestamp"])
            required = {"timestamp", "open", "high", "low", "close", "volume", "source"}
            if not required.issubset(df.columns):
                logger.warning(f"历史 CSV 列不完整: {self.history_path}")
                return pd.DataFrame(
                    columns=["timestamp", "open", "high", "low", "close", "volume", "source"]
                )
            return df
        except Exception as e:
            logger.warning(f"加载历史 CSV 失败: {e}")
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume", "source"]
            )

    def _append_history(self, price_info: JdGoldPrice) -> None:
        """将最新价格追加到历史 CSV."""
        df = self._load_history()

        ts = price_info.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        price = price_info.price

        # 去重: 同一天只保留一条
        if not df.empty and (df["timestamp"].dt.date == ts.date()).any():
            df = df[df["timestamp"].dt.date != ts.date()]

        new_row = pd.DataFrame([{
            "timestamp": ts,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": 0.0,
            "source": "jd",
        }])

        df = pd.concat([df, new_row], ignore_index=True)
        df = df.sort_values("timestamp").reset_index(drop=True)

        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(self.history_path, index=False)
        logger.info(f"已更新京东积存金历史数据: {self.history_path}, 共 {len(df)} 条")

    def _backfill_with_latest(self, df: pd.DataFrame) -> pd.DataFrame:
        """如果本地历史没有今天数据, 抓取最新价并追加."""
        try:
            price_info = self._fetch_price_info()
            if price_info is not None:
                self._append_history(price_info)
                return self._load_history()
        except Exception as e:
            logger.warning(f"抓取最新价失败: {e}")
        return df

    def _fetch_from_sge_proxy(
        self, start: datetime, end: datetime
    ) -> pd.DataFrame:
        """用 SGE Au99.99 数据作为代理, 标记 source='sge_proxy'.

        主源: jdgold 官方 SGE 日K (jdjr_query_stock kline, ~1年, 免登录);
        兜底: akshare spot_hist_sge。
        """
        # 1) jdgold 官方 SGE 日K (集成 2026-08-13)
        try:
            from gold_miner.data.jdgold_client import fetch_sge_kline

            kdf = fetch_sge_kline("day")
            if kdf is not None and not kdf.empty:
                df = kdf.copy()
                df["volume"] = df["volume"].fillna(0.0)
                df["source"] = "sge_proxy"
                return df
        except Exception as e:
            logger.warning(f"jdgold SGE 日K获取失败: {e}")

        # 2) akshare 兜底
        try:
            import akshare as ak
            df = ak.spot_hist_sge(symbol="Au99.99")
            if df.empty:
                return pd.DataFrame(
                    columns=["timestamp", "open", "high", "low", "close", "volume", "source"]
                )

            df = df.rename(
                columns={
                    "date": "timestamp",
                    "open": "open",
                    "close": "close",
                    "low": "low",
                    "high": "high",
                }
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df["volume"] = 0.0
            df["source"] = "sge_proxy"
            return df
        except Exception as e:
            logger.warning(f"SGE 代理数据获取失败: {e}")
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume", "source"]
            )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _fetch_price_info(self) -> JdGoldPrice | None:
        """获取积存金价格: jdgold 主源 (免登录) → H5 getFirstRelatedProductInfo 兜底.

        jdgold 仅支持 MS/ZS 银行; 其余银行 (ZX/GS/GF/XY) 直落 H5。
        """
        # 1) jdgold 主源 (query_gold_analysis 免登录, 数据层集成 2026-08-13)
        try:
            quote = _jdgold_fetch_price(self.bank)
            if quote:
                return JdGoldPrice(
                    timestamp=datetime.now(),
                    product_name=str(quote.get("name") or "京东积存金"),
                    price=float(quote["price"]),
                    change_pct=str(quote.get("change_pct") or ""),
                    source="jdgold",
                )
        except Exception as e:
            logger.warning(f"jdgold 积存金价格获取失败, 落 H5 兜底: {e}")

        # 2) H5 兜底 (getFirstRelatedProductInfo)
        req_data = {
            "circleId": self.circle_id,
            "invokeSource": 5,
            "productId": self.product_id,
        }
        url = f"{_JD_API_URL}?reqData={urllib.parse.quote(json.dumps(req_data))}"

        try:
            with get_proxied_client(timeout=30) as client:
                resp = client.get(url)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning(f"京东金融积存金价格获取失败: {e}")
            return None

        try:
            product = data["resultData"]["data"]
            return JdGoldPrice(
                timestamp=datetime.now(),
                product_name=str(product.get("productName", "京东积存金")),
                price=float(product["minimumPriceValue"]),
                change_pct=str(product.get("rateValue", "")),
                source=self.meta.source,
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"京东金融积存金价格解析失败: {e}")
            return None
