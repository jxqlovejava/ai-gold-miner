"""京东金融积存金价格抓取."""

import json
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd
from loguru import logger

from gold_miner.data.base import DataFetcher, DataSourceMeta
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


class JdAccumulationGoldFetcher(DataFetcher):
    """京东金融积存金实时价格获取器.

    抓取京东金融 H5 接口返回的参考金价, 用于与 Au9999 现货价格交叉对照.
    默认抓取用户实际持有的 **民生银行积存金**.
    """

    def __init__(
        self,
        bank: str = _DEFAULT_BANK,
        product_id: str | None = None,
        circle_id: str = _DEFAULT_CIRCLE_ID,
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

    def fetch(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """抓取最新积存金价格.

        start/end 参数仅用于保持接口一致, 京东接口只返回最新报价.
        """
        _ = start, end, kwargs
        return self.fetch_latest()

    def fetch_latest(self) -> pd.DataFrame:
        """抓取最新一条积存金价格并返回标准化 DataFrame."""
        price_info = self._fetch_price_info()
        if price_info is None:
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )

        now = datetime.now()
        price = price_info.price
        return self.validate(
            pd.DataFrame(
                {
                    "timestamp": [now],
                    "open": [price],
                    "high": [price],
                    "low": [price],
                    "close": [price],
                    "volume": [0.0],
                }
            )
        )

    def fetch_price(self) -> JdGoldPrice | None:
        """获取当前积存金价格对象."""
        return self._fetch_price_info()

    def _fetch_price_info(self) -> JdGoldPrice | None:
        """请求京东金融 H5 接口并解析价格."""
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
