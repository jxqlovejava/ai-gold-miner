"""ETF 资金流数据 — 黄金ETF + 比特币ETF流入流出追踪."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd
from loguru import logger

from gold_miner.data.base import DataFetcher, DataSourceMeta
from gold_miner.data.caching import TtlCache


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
        "159812": "黄金ETF前海开源",
    }

    # 已知黄金ETF的东财 secid (沪=1, 深=0) — 与 akshare fund_etf_spot_em 名称含"黄金ETF" 集合一致
    # (2026-08 实测 7 只). 用于定向查询, 替代全市场 15 页分页拉取 (~20s → ~0.4s)
    GOLD_ETF_SECIDS = ",".join([
        "1.518850", "0.159934", "0.159937", "1.518800",
        "1.518880", "0.159812", "1.518660",
    ])

    def __init__(self) -> None:
        super().__init__(
            DataSourceMeta(
                name="gold_etf_flow",
                source="AKShare fund_etf_spot_em",
                frequency="daily",
                description="国内黄金ETF成交量、成交额、净值变化",
            )
        )

    # 类级 TTL 缓存: 同进程内 _gold_etf_signals 与 _cross_asset_signals 重复拉取复用
    _fetch_cache = TtlCache(ttl_seconds=600)

    def fetch(self, **kwargs: Any) -> pd.DataFrame:
        """获取所有黄金ETF实时行情 (进程内 TTL 缓存去重)."""
        df = self._fetch_cache.get_or(self._fetch_impl)
        return df if df is not None else pd.DataFrame()

    def _fetch_impl(self) -> pd.DataFrame | None:
        """实际拉取; 失败/空返回 None (不缓存, 下次调用会重试).

        优先定向快查已知黄金ETF (ulist, ~0.4s), 失败回退 akshare 全市场分页 (~20s)。
        """
        df = self._fetch_gold_etf_fast()
        if df is None or df.empty:
            logger.warning("黄金ETF定向查询失败, 回退 akshare 全市场分页")
            df = self._fetch_gold_etf_akshare()
        return df if (df is not None and not df.empty) else None

    @staticmethod
    def _fetch_gold_etf_fast() -> pd.DataFrame | None:
        """东财 ulist 定向查询已知黄金ETF (替代 fund_etf_spot_em 全市场 15 页分页 ~20s→~0.4s).

        返回列与 akshare fund_etf_spot_em 黄金子集一致: 代码/名称/最新价/涨跌额/涨跌幅/成交量/成交额
        """
        try:
            import httpx

            url = "https://push2delay.eastmoney.com/api/qt/ulist.np/get"
            params = {
                "fltt": "2",
                "invt": "2",
                "secids": GoldEtfFlowFetcher.GOLD_ETF_SECIDS,
                "fields": "f12,f14,f2,f3,f4,f5,f6",
            }
            resp = httpx.get(
                url, params=params, timeout=15,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            diff = ((resp.json().get("data") or {}).get("diff")) or []
            if not diff:
                return None
            df = pd.DataFrame(diff)
            df = df.rename(columns={
                "f12": "代码", "f14": "名称", "f2": "最新价",
                "f3": "涨跌幅", "f4": "涨跌额", "f5": "成交量", "f6": "成交额",
            })
            df = df[["代码", "名称", "最新价", "涨跌额", "涨跌幅", "成交量", "成交额"]]
            for col in ["最新价", "涨跌额", "涨跌幅", "成交量", "成交额"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            return df
        except Exception as e:
            logger.debug(f"黄金ETF定向查询失败: {e}")
            return None

    @staticmethod
    def _fetch_gold_etf_akshare() -> pd.DataFrame | None:
        """回退: akshare fund_etf_spot_em 全市场分页 (慢但权威)."""
        try:
            import akshare as ak

            df = ak.fund_etf_spot_em()
            if df is None or df.empty:
                return None
            gold_df = df[df["名称"].str.contains("黄金ETF", na=False)].copy()
            return gold_df if not gold_df.empty else None
        except Exception as e:
            logger.warning(f"黄金ETF数据获取失败: {e}")
            return None

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

    # 类级 TTL 缓存: _btc_etf_signals 与 _cross_asset_signals 重复拉取复用
    _fetch_cache = TtlCache(ttl_seconds=600)

    def fetch(self, **kwargs: Any) -> pd.DataFrame:
        """获取比特币ETF行情 (进程内 TTL 缓存去重 + ticker 并行下载)."""
        df = self._fetch_cache.get_or(self._fetch_impl)
        return df if df is not None else pd.DataFrame()

    def _fetch_impl(self) -> pd.DataFrame | None:
        """实际拉取; 失败/空返回 None (不缓存, 下次调用会重试)."""
        try:
            import yfinance as yf

            def _fetch_one(symbol: str, name: str) -> dict[str, Any] | None:
                try:
                    ticker = yf.Ticker(symbol)
                    hist = ticker.history(period="5d")
                    if hist.empty:
                        return None
                    latest = hist.iloc[-1]
                    prev = hist.iloc[-2] if len(hist) >= 2 else latest
                    return {
                        "symbol": symbol,
                        "name": name,
                        "close": float(latest["Close"]),
                        "volume": int(latest["Volume"]),
                        "change_pct": float((latest["Close"] / prev["Close"] - 1) * 100) if len(hist) >= 2 else 0.0,
                        "volume_ratio": float(latest["Volume"] / hist["Volume"].mean()) if len(hist) >= 3 else 1.0,
                    }
                except Exception:
                    return None

            # 并行下载 ticker (原串行 + ticker 间 sleep 1.5s; 并发下自然间隔, 提速 ~3x)
            records: list[dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=3) as pool:
                futs = {
                    pool.submit(_fetch_one, symbol, name): symbol
                    for symbol, name in self.BTC_ETF_SYMBOLS.items()
                }
                for fut in as_completed(futs):
                    rec = fut.result()
                    if rec is not None:
                        records.append(rec)

            if not records:
                return None
            return pd.DataFrame(records)
        except Exception as e:
            logger.warning(f"比特币ETF数据获取失败: {e}")
            return None

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


class IntlGoldEtfFlowFetcher(DataFetcher):
    """国际黄金ETF资金流数据获取器.

    主信号: GLD 官方持仓(吨)日变化 — 真实资金流代理 (T0)
    辅信号: yfinance 成交量异动 — 仅作弱 proxy，不可当作资金流

    追踪:
    - GLD (SPDR Gold Shares) 持仓吨数 — 全球最大
    - IAU / GLDM / PHYS / SGOL 成交量 (secondary)
    """

    INTL_GOLD_ETFS = {
        "GLD": "SPDR Gold Shares",
        "IAU": "iShares Gold Trust",
        "GLDM": "SPDR Gold MiniShares",
        "PHYS": "Sprott Physical Gold Trust",
        "SGOL": "abrdn Physical Gold Shares",
    }

    # 成交量异动阈值（secondary proxy）
    VOLUME_SURGE_THRESHOLD = 1.5  # 成交量相对20日均值倍数

    # 持仓变化阈值（tonnes %）— 日频通常很小
    HOLDINGS_STRONG_PCT = 0.30  # ≥0.3% 视为大幅
    HOLDINGS_MODERATE_PCT = 0.05  # ≥0.05% 视为有方向

    def __init__(self) -> None:
        super().__init__(
            DataSourceMeta(
                name="intl_gold_etf_flow",
                source="SPDR GLD holdings (T0) + yfinance volume proxy",
                frequency="daily",
                description="国际黄金ETF资金流: GLD持仓(吨)为主，成交量异动为辅",
                source_tier="T0",
            )
        )
        self._holdings_fetcher = None  # lazy import GldHoldingsFetcher

    # 类级 TTL 缓存: etf 生成器与 smart_money 均会拉取国际ETF, 复用同一份
    _fetch_cache = TtlCache(ttl_seconds=600)

    def fetch(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """获取所有国际黄金ETF日频数据 (进程内 TTL 缓存去重 + ticker 并行下载)."""
        df = self._fetch_cache.get_or(self._fetch_impl)
        if df is None:
            return pd.DataFrame()
        # 缓存的是全量数据, 按需裁剪日期
        if start:
            df = df[df["timestamp"] >= pd.Timestamp(start)]
        if end:
            df = df[df["timestamp"] <= pd.Timestamp(end)]
        return df

    def _fetch_impl(self) -> pd.DataFrame | None:
        """实际拉取; 失败/空返回 None (不缓存, 下次调用会重试)."""
        try:
            import yfinance as yf

            def _fetch_one(symbol: str, name: str) -> dict[str, Any] | None:
                try:
                    ticker = yf.Ticker(symbol)
                    hist = ticker.history(period="30d")
                    if hist.empty or len(hist) < 5:
                        return None

                    latest = hist.iloc[-1]
                    prev = hist.iloc[-2]
                    vol_ma20 = hist["Volume"].tail(20).mean()
                    price_ma20 = hist["Close"].tail(20).mean()

                    return {
                        "timestamp": hist.index[-1].to_pydatetime(),
                        "symbol": symbol,
                        "name": name,
                        "close": float(latest["Close"]),
                        "volume": int(latest["Volume"]),
                        "change_pct": float((latest["Close"] / prev["Close"] - 1) * 100),
                        "volume_ratio": float(latest["Volume"] / vol_ma20) if vol_ma20 > 0 else 1.0,
                        "price_vs_ma20": float((latest["Close"] / price_ma20 - 1) * 100),
                        "open": float(latest["Open"]),
                        "high": float(latest["High"]),
                        "low": float(latest["Low"]),
                    }
                except Exception:
                    return None

            # 并行下载 ticker (原串行 + ticker 间 sleep 1.5s; 并发下自然间隔, 提速 ~3x)
            records: list[dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=3) as pool:
                futs = {
                    pool.submit(_fetch_one, symbol, name): symbol
                    for symbol, name in self.INTL_GOLD_ETFS.items()
                }
                for fut in as_completed(futs):
                    rec = fut.result()
                    if rec is not None:
                        records.append(rec)

            if not records:
                return None
            return pd.DataFrame(records)
        except Exception as e:
            logger.warning(f"国际黄金ETF数据获取失败: {e}")
            return None

    def fetch_latest(self) -> pd.DataFrame:
        """抓取最新数据."""
        return self.fetch()

    def _get_holdings_fetcher(self):
        """Lazy-load GldHoldingsFetcher to avoid circular imports at module load."""
        if self._holdings_fetcher is None:
            from gold_miner.data.gld_holdings import GldHoldingsFetcher
            self._holdings_fetcher = GldHoldingsFetcher()
        return self._holdings_fetcher

    def fetch_holdings_flow(self, holdings_df: pd.DataFrame | None = None) -> dict[str, Any]:
        """基于 GLD 官方持仓(吨)日变化计算真实资金流方向.

        Args:
            holdings_df: 可选预取的 GldHoldingsFetcher DataFrame
                         (列: timestamp, value 吨). 未提供则自行抓取.

        Returns:
            dict with status, flow_direction, flow_score, tonnes deltas, source_tier=T0
        """
        try:
            if holdings_df is None:
                holdings_df = self._get_holdings_fetcher().fetch()
            if holdings_df is None or holdings_df.empty or len(holdings_df) < 2:
                return {"status": "no_data"}

            df = holdings_df.sort_values("timestamp").reset_index(drop=True)
            latest = float(df["value"].iloc[-1])
            prev = float(df["value"].iloc[-2])
            if prev <= 0:
                return {"status": "no_data"}

            tonnes_delta = latest - prev
            pct_change = (tonnes_delta / prev) * 100.0

            # 分数与 % 变化成正比，|score|≤0.8
            # 日变化 0.4% ≈ 满分 0.8
            raw_score = pct_change * 2.0
            score = max(-0.8, min(0.8, raw_score))

            abs(pct_change)
            if pct_change >= self.HOLDINGS_STRONG_PCT:
                direction = "strong_inflow"
            elif pct_change >= self.HOLDINGS_MODERATE_PCT:
                direction = "inflow"
            elif pct_change <= -self.HOLDINGS_STRONG_PCT:
                direction = "strong_outflow"
            elif pct_change <= -self.HOLDINGS_MODERATE_PCT:
                direction = "outflow"
            else:
                direction = "neutral"
                score = 0.0

            latest_ts = df["timestamp"].iloc[-1]
            as_of = latest_ts.isoformat() if hasattr(latest_ts, "isoformat") else str(latest_ts)

            return {
                "status": "ok",
                "timestamp": datetime.now().isoformat(),
                "as_of": as_of,
                "holdings_tonnes": round(latest, 4),
                "prev_holdings_tonnes": round(prev, 4),
                "tonnes_delta": round(tonnes_delta, 4),
                "holdings_change_pct": round(pct_change, 4),
                "flow_direction": direction,
                "flow_score": round(score, 2),
                "source": "gld_holdings_tonnes",
                "source_tier": "T0",
            }
        except Exception as e:
            logger.warning(f"GLD 持仓资金流计算失败: {e}")
            return {"status": "error", "message": str(e)}

    def fetch_flow_summary(self) -> dict[str, Any]:
        """获取国际黄金ETF资金流摘要.

        主信号: GLD 持仓(吨)日变化 (T0)
        辅字段: yfinance 价格/成交量 proxy（不可当作真实资金流）

        Returns:
            dict with holdings-based flow_direction/flow_score, plus secondary
            volume/price fields labeled as proxy.
        """
        holdings = self.fetch_holdings_flow()
        if holdings.get("status") != "ok":
            # 持仓不可用时不回退到价格当资金流，只返回 no_data
            return {"status": "no_data", "reason": "gld_holdings_unavailable"}

        # Secondary: volume proxy (optional, never used as primary flow)
        vol_surge_count = 0
        gld_vol_ratio = 1.0
        gld_change = 0.0
        total_volume = 0
        avg_change = 0.0
        etf_count = 0
        try:
            df = self.fetch()
            if not df.empty:
                total_volume = int(df["volume"].sum())
                avg_change = float(df["change_pct"].mean())
                vol_surge_count = int((df["volume_ratio"] > self.VOLUME_SURGE_THRESHOLD).sum())
                gld_row = df[df["symbol"] == "GLD"]
                gld_change = float(gld_row["change_pct"].iloc[0]) if not gld_row.empty else avg_change
                gld_vol_ratio = float(gld_row["volume_ratio"].iloc[0]) if not gld_row.empty else 1.0
                etf_count = len(df)
        except Exception as e:
            logger.debug(f"国际ETF价格/成交量 proxy 获取失败(非致命): {e}")

        return {
            "status": "ok",
            "timestamp": datetime.now().isoformat(),
            "as_of": holdings.get("as_of"),
            "holdings_tonnes": holdings["holdings_tonnes"],
            "prev_holdings_tonnes": holdings["prev_holdings_tonnes"],
            "tonnes_delta": holdings["tonnes_delta"],
            "holdings_change_pct": holdings["holdings_change_pct"],
            "flow_direction": holdings["flow_direction"],
            "flow_score": holdings["flow_score"],
            "source": "gld_holdings_tonnes",
            "source_tier": "T0",
            # secondary price/volume proxy fields (NOT real flow)
            "total_volume": total_volume,
            "avg_change_pct": round(avg_change, 2),
            "gld_change_pct": round(gld_change, 2),
            "gld_volume_ratio": round(gld_vol_ratio, 2),
            "volume_surge_count": vol_surge_count,
            "etf_count": etf_count,
        }

    def fetch_weekly_trend(self, weeks: int = 4) -> dict[str, Any]:
        """获取近N周趋势.

        基于每周最后一个交易日的量价数据计算趋势。
        """
        try:
            import yfinance as yf

            # 获取GLD足够长的历史数据
            ticker = yf.Ticker("GLD")
            hist = ticker.history(period=f"{weeks + 2}w")
            if hist.empty or len(hist) < 10:
                return {"status": "no_data"}

            # 按周聚合
            hist = hist.reset_index()
            hist["week"] = hist["Date"].dt.isocalendar().week
            hist["year"] = hist["Date"].dt.isocalendar().year

            weekly = hist.groupby(["year", "week"]).agg({
                "Close": ["first", "last", "mean"],
                "Volume": "sum",
            }).reset_index()
            weekly.columns = ["year", "week", "open", "close", "avg", "volume"]
            weekly = weekly.tail(weeks)

            if len(weekly) < 2:
                return {"status": "no_data"}

            # 计算周变化
            weekly["change_pct"] = (weekly["close"] / weekly["open"] - 1) * 100
            avg_weekly_change = float(weekly["change_pct"].mean())
            latest_week = float(weekly["change_pct"].iloc[-1])

            trend = "up" if latest_week > 0 and avg_weekly_change > 0 else \
                    "down" if latest_week < 0 and avg_weekly_change < 0 else "mixed"

            return {
                "status": "ok",
                "weeks": len(weekly),
                "latest_week_change_pct": round(latest_week, 2),
                "avg_weekly_change_pct": round(avg_weekly_change, 2),
                "trend": trend,
                "total_volume_4w": int(weekly.tail(4)["volume"].sum()),
            }
        except Exception as e:
            logger.warning(f"周趋势获取失败: {e}")
            return {"status": "error", "message": str(e)}
