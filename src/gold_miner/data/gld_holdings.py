"""SPDR Gold Shares (GLD) 持仓数据抓取.

GLD 是全球最大的黄金 ETF 之一，其每日持仓量（吨）是观察机构/散户黄金需求的
重要情绪指标。数据来源为 spdrgoldshares.com 官方历史归档 Excel。

多层降级策略 (macOS OpenSSL 兼容性):
1. 代理 HTTPS
2. 直连 HTTPS (绕过代理)
3. 直连 + verify=False + 自定义 SSL context
4. curl 子进程 (绕过 Python TLS 栈)
5. 全部失败 → 空 DataFrame (调用方使用 fallback)
"""

from __future__ import annotations

import ssl
import subprocess
from datetime import datetime
from io import BytesIO
from time import sleep as _sleep
from typing import Any

import pandas as pd
from loguru import logger

from gold_miner.data.base import DataFetcher, DataSourceMeta
from gold_miner.data.caching import DiskCache, TtlCache
from gold_miner.data.economic_data import EconomicDataPoint, EconomicDataRecorder
from gold_miner.proxy import get_proxied_client

# GLD 近期已知持仓量 (吨) — 2026-07 约 900 吨，用于不可恢复失败时的 fallback
_GLD_KNOWN_HOLDINGS_TONNES = 900.0

# GLD Excel 下载超时 (秒) — 健康网络 2-8s 即可完成; 60s 会让网络抖动时单策略空烧 60s
# (曾致管线 393s: 首次全策略失败~90s + 第二生成器重试又烧一轮). 25s 足够覆盖慢速下载, 同时限制最坏情况
_GLD_DOWNLOAD_TIMEOUT = 25.0


class GldHoldingsFetcher(DataFetcher):
    """GLD 每日黄金持仓量获取器."""

    ARCHIVE_URL = (
        "https://api.spdrgoldshares.com/api/v1/historical-archive"
        "?product=gld&exchange=NYSE&lang=en"
    )
    SHEET_NAME = "US GLD Historical Archive"

    # 类级 TTL 缓存: 同进程内 etf 与 smart_money 生成器并行抢拉同一份 GLD 持仓,
    # 通过 double-checked locking 保证并发冷启动只下载一次, 消除重复的慢速 SSL 降级重试
    _fetch_cache = TtlCache(ttl_seconds=600)

    # 跨进程磁盘缓存: 进程内缓存不跨 scan, 每次 scan 新进程数据库 miss 时
    # 都重新下载 SPDR Excel (多层降级 ~6s), 故用磁盘缓存跨 scan 复用.
    #
    # ⚠️ TTL 切不可假设「当天不变」——GLD 是每天新增一个数据点的日频序列,
    # 且发布时间落在盘中。事故 2026-09-17: 原 TTL=6h 下, 09:18 的 scan 缓存了
    # 截至 09-15 的快照 (彼时 SPDR 尚未发布 09-16), 9/16 数据随后发布, 但
    # 12:17 的 scan 因缓存未过期 (3h < 6h) 直接复用旧快照 —— 报告连续两轮显示
    # "+2.86吨 流入", 实为 09/14→09/15 的变化, 而真值已是 09/15→09/16 的
    # "+1.71吨 (+0.163%)"。陈旧值进入聪明钱维度仍拿满 +0.55 分 (真值应 +0.33),
    # 且被 BullAgent 引为第一论据。收紧到 20min 后盲区 ≤20min。
    DISK_CACHE_TTL_SECONDS = 1200
    _disk_cache = DiskCache(key="gld_holdings", ttl_seconds=DISK_CACHE_TTL_SECONDS)

    def __init__(self, recorder: EconomicDataRecorder | None = None) -> None:
        super().__init__(
            DataSourceMeta(
                name="gld_holdings",
                source="SPDR Gold Shares / World Gold Trust Services",
                frequency="daily",
                description="GLD 每日黄金持仓量（吨）",
                source_tier="T0",
            )
        )
        self._recorder = recorder or EconomicDataRecorder()

    def _download_content(self) -> bytes | None:
        """多层降级下载 GLD Excel 内容."""
        # Strategy 1: 代理 HTTPS
        for attempt in range(2):
            try:
                with get_proxied_client(timeout=_GLD_DOWNLOAD_TIMEOUT) as client:
                    resp = client.get(self.ARCHIVE_URL)
                    resp.raise_for_status()
                    logger.debug("GLD 数据获取成功 [strategy=proxied-https]")
                    return resp.content
            except Exception as e:
                if attempt == 0:
                    logger.debug(f"GLD 代理 HTTPS 失败 (attempt 1/2): {e}")
                    _sleep(1)

        # Strategy 2: 直连 HTTPS (绕过代理)
        try:
            import httpx
            with httpx.Client(timeout=_GLD_DOWNLOAD_TIMEOUT) as client:
                resp = client.get(self.ARCHIVE_URL, follow_redirects=True)
                resp.raise_for_status()
                logger.debug("GLD 数据获取成功 [strategy=direct-https]")
                return resp.content
        except Exception as e:
            logger.debug(f"GLD 直连 HTTPS 失败: {e}")

        # Strategy 3: 直连 + verify=False + 自定义 SSL context (绕过 macOS OpenSSL 问题)
        try:
            import httpx
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            # 禁用旧版本 TLS 避免 EOF 问题
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            with httpx.Client(timeout=_GLD_DOWNLOAD_TIMEOUT, verify=False) as client:
                resp = client.get(self.ARCHIVE_URL, follow_redirects=True)
                resp.raise_for_status()
                logger.debug("GLD 数据获取成功 [strategy=direct-http-noverify]")
                return resp.content
        except Exception as e:
            logger.debug(f"GLD 直连 HTTP noverify 失败: {e}")

        # Strategy 4: curl 子进程 (绕过 Python TLS 栈)
        try:
            result = subprocess.run(
                ["curl", "-sS", "--max-time", str(int(_GLD_DOWNLOAD_TIMEOUT)), "--noproxy", "*",
                 "-H", "User-Agent: Mozilla/5.0",
                 self.ARCHIVE_URL],
                capture_output=True, text=False, timeout=int(_GLD_DOWNLOAD_TIMEOUT) + 5,
            )
            if result.returncode == 0 and result.stdout and len(result.stdout) > 1000:
                logger.debug("GLD 数据获取成功 [strategy=curl-direct]")
                return result.stdout
            else:
                logger.debug(f"GLD curl 失败 (exit={result.returncode}, len={len(result.stdout)})")
        except Exception as e:
            logger.debug(f"GLD curl 子进程失败: {e}")

        return None

    def fetch(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        force_refresh: bool = False,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """下载并解析 GLD 历史持仓数据 (进程内 TTL 缓存 + 数据库优先复用).

        force_refresh=True (盘前预热 cron 用): 绕过全部缓存强制下载最新,
        成功后回填双层缓存; 下载失败回退 DB, 不返回空。

        返回 DataFrame 列：timestamp, value（吨）, nav_per_share, shares_volume
        """
        if force_refresh:
            # 注意: DataFrame 不能参与 or 短路 (真值歧义), 显式判 None
            full = self._download_and_parse()
            if full is None:
                full = self._from_db()
            if full is not None:
                self._fetch_cache.set(full)
                self._disk_cache.set(full)
        else:
            full = self._fetch_cache.get_or(
                lambda: self._disk_cache.get_or(self._load_holdings)
            )
        if full is None:
            logger.debug("GLD 持仓数据不可用")
            return pd.DataFrame(columns=["timestamp", "value", "nav_per_share", "shares_volume"])

        # 缓存的是全量数据, 按需裁剪日期
        df = full
        if start:
            df = df[df["timestamp"] >= pd.Timestamp(start)]
        if end:
            df = df[df["timestamp"] <= pd.Timestamp(end)]
        return df.reset_index(drop=True)

    def _load_holdings(self) -> pd.DataFrame | None:
        """优先读经济数据库 (当日已有则不重复下载, GLD 持仓每日仅更新一次), 否则走下载+解析."""
        db = self._from_db()
        if db is not None:
            logger.debug("GLD 持仓复用经济数据库 (免下载)")
            return db
        return self._download_and_parse()

    def _from_db(self) -> pd.DataFrame | None:
        """从经济数据库读取最近 GLD 持仓 (前值+最新两行), 用于流向计算.

        仅当观测日期在近 2h 内有效, 否则视为「可能已被新发布取代」并触发重新下载。

        注: 原为 48h —— 与 doctrine 时效衰减的「24h 权重 1.0」边界差两倍,
        允许最多漏掉两个发布日的数据点, 事故 2026-09-17 的陈旧值即由此放大。
        DB 层仅作「避免反复慢下载」的节流: 过期即重下 (~6s); 下载失败时
        fetch() 仍会回退到 DB, 不会返回空。
        """
        try:
            points = self._recorder.find(indicator="gld_holdings_tonnes")
            if not points:
                return None
            p = points[-1]
            if p.actual is None:
                return None
            obs = pd.Timestamp(p.observation_date)
            if obs < pd.Timestamp.now() - pd.Timedelta(hours=2):
                logger.info(f"GLD 持仓库观测超 2h ({obs.date()}), 触发重新下载")
                return None
            latest_val = float(p.actual)
            prev_val = float(p.previous) if p.previous is not None else latest_val
            return pd.DataFrame([
                {"timestamp": obs - pd.Timedelta(days=1), "value": prev_val},
                {"timestamp": obs, "value": latest_val},
            ])
        except Exception as e:
            logger.debug(f"读取 GLD 持仓数据库失败: {e}")
            return None

    def _download_and_parse(self) -> pd.DataFrame | None:
        """下载+解析 GLD 全量历史持仓; 失败/空返回 None (不缓存, 下次重试).

        含持久化最新值 (仅在真正下载时执行, 缓存命中跳过)。
        """
        content = self._download_content()
        if content is None:
            logger.debug("GLD 持仓数据下载失败: 所有策略不可用")
            return None

        try:
            df = pd.read_excel(BytesIO(content), sheet_name=self.SHEET_NAME)
        except Exception as e:
            logger.warning(f"GLD Excel 解析失败: {e}")
            return None

        # 标准化列名
        df = df.rename(
            columns={
                "Date": "date",
                "Tonnes of Gold": "value",
                "NAV/Share at 10:30am NYT": "nav_per_share",
                "Daily Share Volume": "shares_volume",
            }
        )

        required = {"date", "value"}
        if not required.issubset(df.columns):
            logger.warning(f"GLD 数据缺少必要列: {required - set(df.columns)}")
            return None

        df["timestamp"] = pd.to_datetime(df["date"], errors="coerce")
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df["nav_per_share"] = pd.to_numeric(df.get("nav_per_share"), errors="coerce")
        df["shares_volume"] = pd.to_numeric(df.get("shares_volume"), errors="coerce")

        df = df[["timestamp", "value", "nav_per_share", "shares_volume"]].dropna(
            subset=["timestamp", "value"]
        )
        df = df.sort_values("timestamp").reset_index(drop=True)

        if df.empty:
            return None

        self._persist_latest(df)
        return df

    def fetch_latest(self) -> pd.DataFrame:
        """获取最新一条 GLD 持仓数据."""
        df = self.fetch()
        if df.empty:
            return df
        return df.tail(1).reset_index(drop=True)

    def _persist_latest(self, df: pd.DataFrame) -> None:
        """将最新一条 GLD 持仓持久化到经济数据库."""
        if df.empty:
            return

        latest = df.iloc[-1]
        previous_value = df.iloc[-2]["value"] if len(df) >= 2 else None
        release_date = latest["timestamp"].strftime("%Y-%m-%d")

        try:
            point = EconomicDataPoint(
                indicator="gld_holdings_tonnes",
                release_date=release_date,
                observation_date=release_date,
                period=release_date[:7],
                actual=float(latest["value"]),
                previous=float(previous_value) if previous_value is not None else None,
                unit="吨",
                source="SPDR Gold Shares / World Gold Trust Services",
                source_tier="T0",
                impact="medium",
                notes=f"GLD 每日黄金持仓量，NAV/Share {latest.get('nav_per_share')}，成交量 {latest.get('shares_volume')}",
            )
            self._recorder.save(point)
        except Exception as e:
            logger.warning(f"持久化 GLD 持仓数据失败: {e}")
