"""数据源抽象基类."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class DataSourceMeta:
    """数据源元信息."""

    name: str
    source: str
    frequency: str
    description: str = ""


class DataFetcher(ABC):
    """所有数据获取器的基类."""

    def __init__(self, meta: DataSourceMeta) -> None:
        self.meta = meta

    @abstractmethod
    def fetch(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """抓取数据，返回标准化DataFrame.

        DataFrame必须包含以下列：
        - timestamp: datetime64[ns]
        - open, high, low, close: float64
        - volume: float64 (可选)
        """
        ...

    @abstractmethod
    def fetch_latest(self) -> pd.DataFrame:
        """抓取最新一条数据."""
        ...

    def validate(self, df: pd.DataFrame) -> pd.DataFrame:
        """校验并标准化DataFrame."""
        required = {"timestamp", "open", "high", "low", "close"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"DataFrame缺少必需列: {missing}")

        df = df.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        return df.dropna(subset=["open", "high", "low", "close"])
