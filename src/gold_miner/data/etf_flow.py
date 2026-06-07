"""ETF 资金流数据 — 黄金ETF + 比特币ETF流入流出追踪."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
from loguru import logger

from gold_miner.data.base import DataFetcher, DataSourceMeta


@dataclass
class EtfFlowRecord:
    """单日ETF资金流记录."""

    date: datetime
    symbol: str
    name: str
    volume: float = 0.0  # 成交量
    turnover: float = 0.0  # 成交额
    nav_change_pct: float = 0.0  # 净值日涨跌
    flow_direction: str = "neutral"  # inflow / outflow / neutral


class GoldEtfFlowFetcher(DataFetcher):
    """黄金ETF资金流数据获取器 — 国内黄金ETF.

    数据源: AKShare 实时ETF行情
    追踪: 华安黄金ETF(518880)、易方达黄金ETF(159934)、博时黄金ETF(159937)等
    """

    GOLD_ETF_CODES = {
        "518880": "黄金ETF华安",
        "159934": "黄金ETF易方达",
        "159937": "黄金ETF博时",
        "518800": "黄金ETF国泰",
        "518660": "黄金ETF工银",
        "518850": "黄金ETF华夏",
    }

    def __init__(self) -> None:
        super().__init__(
            DataSourceMeta(
                name="gold_etf_flow",
                source="AKShare fund_etf_spot_em",
                frequency="daily",
                description="国内黄金ETF成交量、成交额、净值变化",
            )
        )

    def fetch(self, **kwargs: Any) -> pd.DataFrame:
        """获取所有黄金ETF实时行情."""
        try:
            import akshare as ak

            df = ak.fund_etf_spot_em()
            if df is None or df.empty:
                logger.warning("黄金ETF行情数据为空")
                return pd.DataFrame()

            gold_mask = df["名称"].str.contains("黄金ETF", na=False)
            gold_df = df[gold_mask].copy()
            return gold_df
        except Exception as e:
            logger.warning(f"黄金ETF数据获取失败: {e}")
            return pd.DataFrame()

    def fetch_latest(self) -> pd.DataFrame:
        """抓取最新黄金ETF数据."""
        return self.fetch()

    def fetch_flow_summary(self) -> dict[str, Any]:
        """获取黄金ETF资金流摘要.

        Returns:
            dict with: total_volume, total_turnover, flow_direction, flow_score
        """
        df = self.fetch()
        if df.empty:
            return {"status": "no_data"}

        total_vol = df["成交量"].sum()
        total_turnover = df["成交额"].sum()

        # 简单判断：成交量相对前一日的变化方向
        # 实际应用中需要对比历史数据
        return {
            "status": "ok",
            "timestamp": datetime.now().isoformat(),
            "total_volume": int(total_vol),
            "total_turnover": float(total_turnover),
            "etf_count": len(df),
            "top_etf": {
                "code": str(df.iloc[0].get("代码", "")),
                "name": str(df.iloc[0].get("名称", "")),
                "volume": int(df.iloc[0].get("成交量", 0)),
            },
        }

    def fetch_daily_change(self, lookback: int = 5) -> dict[str, Any]:
        """获取近N日黄金ETF成交量变化趋势."""
        df = self.fetch()
        if df.empty or len(df) < 1:
            return {"status": "no_data"}

        total_vol = int(df["成交量"].sum())
        total_turnover = float(df["成交额"].sum())

        # 加权平均涨跌
        if "日增长率" in df.columns:
            df["日增长率_num"] = pd.to_numeric(df.get("日增长率", 0), errors="coerce").fillna(0)
            # Weight by turnover
            weights = df["成交额"] / df["成交额"].sum() if df["成交额"].sum() > 0 else 1 / len(df)
            avg_change = float((df["日增长率_num"] * weights).sum())
        else:
            avg_change = 0.0

        direction = "inflow" if avg_change > 0.3 else "outflow" if avg_change < -0.3 else "neutral"

        return {
            "status": "ok",
            "total_volume": total_vol,
            "total_turnover": total_turnover,
            "avg_nav_change_pct": round(avg_change, 2),
            "flow_direction": direction,
            "etf_count": len(df),
        }


class BtcEtfFlowFetcher(DataFetcher):
    """比特币ETF资金流数据获取器.

    数据源优先级:
    1. CoinGlass API (免费, 无需认证)
    2. yfinance IBIT volume proxy (本地缓存)
    """

    # 主要比特币ETF代码 (yfinance)
    BTC_ETF_SYMBOLS = {
        "IBIT": "iShares Bitcoin Trust (BlackRock)",
        "FBTC": "Fidelity Wise Origin Bitcoin Fund",
        "GBTC": "Grayscale Bitcoin Trust",
        "ARKB": "ARK 21Shares Bitcoin ETF",
        "BITB": "Bitwise Bitcoin ETF",
        "HODL": "VanEck Bitcoin Trust",
        "BTCO": "Invesco Galaxy Bitcoin ETF",
        "EZBC": "Franklin Bitcoin ETF",
        "BRRR": "Valkyrie Bitcoin Fund",
    }

    def __init__(self) -> None:
        super().__init__(
            DataSourceMeta(
                name="btc_etf_flow",
                source="yfinance + CoinGlass",
                frequency="daily",
                description="比特币ETF成交量/价格变化追踪",
            )
        )

    def fetch(self, **kwargs: Any) -> pd.DataFrame:
        """获取比特币ETF行情（成交量+价格变化）."""
        try:
            import yfinance as yf

            records = []
            for symbol, name in self.BTC_ETF_SYMBOLS.items():
                try:
                    ticker = yf.Ticker(symbol)
                    hist = ticker.history(period="5d")
                    if hist.empty:
                        continue
                    latest = hist.iloc[-1]
                    prev = hist.iloc[-2] if len(hist) >= 2 else latest
                    records.append({
                        "symbol": symbol,
                        "name": name,
                        "close": float(latest["Close"]),
                        "volume": int(latest["Volume"]),
                        "change_pct": float((latest["Close"] / prev["Close"] - 1) * 100) if len(hist) >= 2 else 0.0,
                        "volume_ratio": float(latest["Volume"] / hist["Volume"].mean()) if len(hist) >= 3 else 1.0,
                    })
                except Exception:
                    continue

            return pd.DataFrame(records)
        except Exception as e:
            logger.warning(f"比特币ETF数据获取失败: {e}")
            return pd.DataFrame()

    def fetch_latest(self) -> pd.DataFrame:
        """抓取最新比特币ETF数据."""
        return self.fetch()

    def fetch_flow_signal(self) -> dict[str, Any]:
        """获取比特币ETF资金流信号摘要.

        Returns:
            dict with: direction, score, btc_etf_count, total_volume
        """
        df = self.fetch()
        if df.empty:
            return {"status": "no_data", "direction": "neutral", "score": 0.0}

        total_vol = int(df["volume"].sum())
        avg_change = float(df["change_pct"].mean())
        vol_surge_count = int((df["volume_ratio"] > 1.3).sum())

        # 综合评分: 量价配合
        if avg_change > 1.0 and vol_surge_count >= 3:
            direction = "strong_inflow"
            score = min(avg_change / 5 + vol_surge_count * 0.1, 1.0)
        elif avg_change > 0:
            direction = "inflow"
            score = min(avg_change / 5, 0.5)
        elif avg_change < -1.0 and vol_surge_count >= 3:
            direction = "strong_outflow"
            score = max(avg_change / 5 - vol_surge_count * 0.1, -1.0)
        elif avg_change < 0:
            direction = "outflow"
            score = max(avg_change / 5, -0.5)
        else:
            direction = "neutral"
            score = 0.0

        return {
            "status": "ok",
            "direction": direction,
            "score": round(score, 2),
            "avg_change_pct": round(avg_change, 2),
            "total_volume": total_vol,
            "volume_surge_etfs": vol_surge_count,
            "etf_count": len(df),
            "timestamp": datetime.now().isoformat(),
        }
