"""共享价格工具函数 — True Range / ATR 计算.

供 ``technical.py`` 和 ``trailing_stop.py`` 共同引用，
避免重复计算逻辑。

模块以下划线前缀标记为内部工具模块，不对外暴露为公共 API。
"""
from __future__ import annotations

import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    """计算真实波幅 (True Range).

    TR = max(high - low, |high - prev_close|, |low - prev_close|)

    Args:
        df: 包含 open / high / low / close 列的 DataFrame，
            按时间升序排列。

    Returns:
        pd.Series: True Range 序列，第一行为 NaN。

    Raises:
        ValueError: 缺少必要列时抛出。
    """
    required = {"high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame 缺少列: {missing}")

    high_low = df["high"] - df["low"]
    high_close_prev = (df["high"] - df["close"].shift(1)).abs()
    low_close_prev = (df["low"] - df["close"].shift(1)).abs()
    return pd.concat([high_low, high_close_prev, low_close_prev], axis=1).max(axis=1)


def average_true_range(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """计算平均真实波幅 (Average True Range).

    ATR = TR 的 rolling mean，反映平均波动幅度。

    Args:
        df: 包含 open / high / low / close 列的 DataFrame。
        period: 滚动窗口 (默认 14，即常规 ATR(14))。

    Returns:
        pd.Series: ATR 序列，前 period 行为 NaN。
    """
    tr = true_range(df)
    return tr.rolling(window=period).mean()
