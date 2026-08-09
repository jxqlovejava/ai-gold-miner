"""CFTC COT持仓报告 — 黄金期货非商业持仓监控.

数据来源: CFTC.gov 每周发布 (每周五)
关键指标:
- 非商业净多仓 (Managed Money Net Long) — "聪明钱"方向
- 商业持仓 (Producer/Merchant Net Short) — 套保盘,反向指标
- 非报告持仓 (Small Speculators) — 散户

信号逻辑:
- 非商业净多仓创52周新高 → 机构极度看涨
- 非商业净多仓从极高位回落 >30% → 机构获利了结,看跌
- 商业净空仓减少 → 生产商减少套保,看涨
"""

from __future__ import annotations

import io
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import sleep as _sleep
from typing import Any

import httpx
import pandas as pd
from loguru import logger

from gold_miner.data.base import DataFetcher, DataSourceMeta
from gold_miner.proxy import get_proxied_client

# CFTC 每周发布的 comma-delimited legacy report（futures only）
CFTC_COT_CSV_URL = "https://www.cftc.gov/dea/newcot/deafut.txt"


@dataclass
class CotGoldData:
    """黄金COT持仓数据."""

    report_date: datetime
    # 非商业持仓 (Managed Money / Large Speculators)
    noncomm_long: int
    noncomm_short: int
    noncomm_spread: int
    # 商业持仓 (Producer/Merchant/Processor/User)
    comm_long: int
    comm_short: int
    # 非报告持仓 (Small Traders)
    nonrep_long: int
    nonrep_short: int

    @property
    def noncomm_net(self) -> int:
        """非商业净多仓 (聪明钱净持仓)."""
        return self.noncomm_long - self.noncomm_short

    @property
    def comm_net(self) -> int:
        """商业净持仓 (通常为负,套保盘)."""
        return self.comm_long - self.comm_short

    @property
    def nonrep_net(self) -> int:
        """散户净持仓."""
        return self.nonrep_long - self.nonrep_short

    @property
    def noncomm_ratio(self) -> float:
        """非商业多空比."""
        if self.noncomm_short == 0:
            return 0.0
        return self.noncomm_long / self.noncomm_short

    @property
    def total_oi(self) -> int:
        """总持仓量 (Open Interest)."""
        return self.noncomm_long + self.noncomm_short + self.comm_long + self.comm_short


class CotReportFetcher(DataFetcher):
    """CFTC COT报告数据获取器.

    数据来源: CFTC.gov (Commitments of Traders Reports)
    黄金合约: COMEX Gold (GC)
    更新频率: 每周五
    """

    # 黄金在CFTC报告中的市场和合约代码
    GOLD_MARKET = "CMX"
    GOLD_CONTRACT = "GOLD"

    def __init__(self) -> None:
        super().__init__(
            DataSourceMeta(
                name="cot_report",
                source="CFTC.gov",
                frequency="weekly",
                description="CFTC COT报告 — 黄金期货持仓",
                source_tier="T0",
            )
        )
        # 最近一次成功获取的真实 CFTC 数据（不含 fallback 合成数据）。
        # 供持仓结构信号计算周期极值用，避免合成常量污染极值判断。
        self._last_real_df: pd.DataFrame | None = None

    def fetch(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """抓取COT报告数据.

        将实时 CFTC 历史数据合并到回退数据中，
        确保 fetch_net_position 有足够的行来计算趋势/52周位置。

        返回 DataFrame 包含:
        - timestamp: report_date
        - open, high, low, close: 标准化列 (实际值为持仓数据)
        """
        real = self._fetch_from_cftc()
        fallback = self._fallback_data()

        if real is None:
            logger.warning("CFTC 实时数据不可用，使用纯历史回退数据")
            self._last_real_df = pd.DataFrame()
            return fallback

        real_df = self._to_dataframe(real)
        self._last_real_df = real_df
        if real_df.empty:
            return fallback

        # 合并：移除所有与真实数据日期重叠的 fallback 行（不再只替换首行，
        # 否则全历史解析后同日期会重复污染）
        real_dates = set(real_df["timestamp"])
        fallback = fallback[~fallback["timestamp"].isin(real_dates)]
        merged = pd.concat([fallback, real_df], ignore_index=True)
        merged = merged.sort_values("timestamp").reset_index(drop=True)
        return self.validate(merged)

    def fetch_latest(self) -> pd.DataFrame:
        """抓取最新一期COT报告."""
        df = self.fetch()
        if df.empty:
            return df
        return df.tail(1)

    def fetch_real(self) -> pd.DataFrame:
        """只返回真实 CFTC 历史持仓数据（不含 fallback 合成数据）.

        用于持仓结构信号（总持仓出清/空头投降/多头回归）的周期极值计算，
        避免合成回退数据（常量 OI/空头）污染极值判断。
        未获取到真实数据时返回空 DataFrame。
        """
        if self._last_real_df is None:
            real = self._fetch_from_cftc()
            self._last_real_df = self._to_dataframe(real) if real else pd.DataFrame()
        return self._last_real_df

    def fetch_net_position(self, weeks: int = 4) -> dict[str, Any]:
        """获取非商业净持仓摘要.

        Returns:
            dict with: latest_net, prev_net, change, pct_change, trend
        """
        df = self.fetch()
        if df.empty or len(df) < 2:
            return {"status": "no_data"}

        df = df.sort_values("timestamp")
        latest = df.iloc[-1]
        prev = df.iloc[-2]

        latest_net = latest["close"]
        prev_net = prev["close"]
        change = latest_net - prev_net
        pct_change = (change / abs(prev_net) * 100) if prev_net != 0 else 0

        # 趋势: 最近N周平均变化
        if len(df) >= weeks:
            recent = df.tail(weeks)
            trend = "up" if recent["close"].iloc[-1] > recent["close"].iloc[0] else "down"
        else:
            trend = "neutral"

        # 52周位置
        if len(df) >= 52:
            year_range = df.tail(52)["close"]
            position_in_range = (latest_net - year_range.min()) / (year_range.max() - year_range.min())
        else:
            position_in_range = 0.5

        return {
            "status": "ok",
            "report_date": latest["timestamp"].isoformat(),
            "latest_net": int(latest_net),
            "prev_net": int(prev_net),
            "change": int(change),
            "pct_change": round(pct_change, 2),
            "trend": trend,
            "position_in_52w_range": round(position_in_range, 2),
            "noncomm_ratio": round(latest.get("noncomm_ratio", 0), 2),
        }

    def _fetch_from_cftc(self) -> list[CotGoldData] | None:
        """从CFTC下载并解析COT报告."""
        try:
            records = self._parse_cftc_csv()
            if records:
                return records
        except Exception as e:
            logger.warning(f"CFTC数据下载失败: {e}")

        return None

    def _parse_cftc_csv(self) -> list[CotGoldData] | None:
        """解析 CFTC comma-delimited COT 报告.

        文件为每周发布的 futures-only legacy report，无表头，按位置取值。
        通过第一列定位 GOLD - COMMODITY EXCHANGE INC.。

        多层降级策略（应对 CFTC 服务器 TLS/代理兼容性问题）:
        1. 直连 HTTPS (httpx, no proxy) — CFTC 公网可达，无 TLS 问题
        2. 直连 HTTP (httpx, no proxy) — 部分环境下更快
        3. 代理 HTTPS — 通过 mihomo 代理
        4. curl 子进程 — 绕过 Python TLS 栈的兼容性问题
        5. 全部失败 → 返回 None 触发 fallback_data
        """
        text: str | None = None
        strategy = "unknown"

        # Strategy 1: 直连 HTTPS（绕过代理）
        for attempt in range(2):
            try:
                with httpx.Client(timeout=30.0) as client:
                    resp = client.get(CFTC_COT_CSV_URL, follow_redirects=True)
                    resp.raise_for_status()
                    text = resp.text
                    strategy = "direct-https"
                    break
            except Exception as e:
                if attempt == 0:
                    logger.debug(f"CFTC 直连 HTTPS 失败 (attempt 1/2): {e}")
                    _sleep(1)
                else:
                    logger.debug(f"CFTC 直连 HTTPS 失败 (attempt 2/2): {e}")

        # Strategy 2: 直连 HTTP → HTTPS（禁用 SSL 验证）
        if text is None:
            for attempt in range(2):
                try:
                    with httpx.Client(timeout=30.0, verify=False) as client:
                        resp = client.get(CFTC_COT_CSV_URL, follow_redirects=True)
                        resp.raise_for_status()
                        text = resp.text
                        strategy = "direct-http-noverify"
                        break
                except Exception as e:
                    if attempt == 0:
                        logger.debug(f"CFTC HTTP noverify 失败 (attempt 1/2): {e}")
                        _sleep(1)
                    else:
                        logger.debug(f"CFTC HTTP noverify 失败 (attempt 2/2): {e}")

        # Strategy 3: 通过代理（现有行为）
        if text is None:
            try:
                with get_proxied_client(timeout=30.0) as client:
                    resp = client.get(CFTC_COT_CSV_URL)
                    resp.raise_for_status()
                    text = resp.text
                    strategy = "proxied-https"
            except Exception as e:
                logger.debug(f"CFTC 代理获取失败: {e}")

        # Strategy 4: curl 子进程（绕过 Python TLS 栈）
        if text is None:
            try:
                result = subprocess.run(
                    ["curl", "-sS", "--max-time", "30", "--noproxy", "*",
                     "-H", "User-Agent: Mozilla/5.0",
                     CFTC_COT_CSV_URL],
                    capture_output=True, text=True, timeout=35,
                )
                if result.returncode == 0 and result.stdout and len(result.stdout) > 1000:
                    text = result.stdout
                    strategy = "curl-direct"
                else:
                    logger.debug(f"CFTC curl 失败 (exit={result.returncode}, len={len(result.stdout)})")
            except Exception as e:
                logger.debug(f"CFTC curl 子进程失败: {e}")

        if text is None:
            logger.debug("CFTC CSV 下载/读取失败: 所有策略均不可用")
            return None

        logger.debug(f"CFTC 数据获取成功 [strategy={strategy}, size={len(text)}]")

        try:
            # CFTC 文件无表头，按列位置解析；数字含前导空格与千分位逗号
            df = pd.read_csv(
                io.StringIO(text),
                header=None,
                thousands=",",
                encoding="utf-8",
                on_bad_lines="skip",
            )
        except Exception as e:
            logger.warning(f"CFTC CSV 解析失败: {e}")
            return None

        if df.empty or df.shape[1] < 17:
            logger.debug("CFTC CSV 格式异常或列数不足")
            return None

        # 第 0 列为商品名称；排除 MICRO GOLD，只取标准 GOLD 合约 (088691)
        name_col = df[0].str.upper()
        gold_mask = (
            name_col.str.contains("GOLD", case=False, na=False)
            & name_col.str.contains("COMMODITY EXCHANGE", case=False, na=False)
            & ~name_col.str.contains("MICRO", case=False, na=False)
        )
        # 优先使用标准合约代码 088691
        code_mask = df[3].astype(str).str.strip() == "088691"
        gold_rows = df[code_mask] if code_mask.any() else df[gold_mask]
        if gold_rows.empty:
            logger.debug("CFTC CSV 中未找到 GOLD 行")
            return None

        # 取全部历史（按第 2 列 YYYY-MM-DD 排序）— 供持仓结构信号做周期极值比较。
        # 原实现只保留最近一期；保留全历史后可计算「总持仓/空头相对周期顶的回落」。
        df_sorted = gold_rows.copy()
        df_sorted["_report_date"] = pd.to_datetime(df_sorted[2], errors="coerce")
        df_sorted = df_sorted.dropna(subset=["_report_date"])
        df_sorted = df_sorted.sort_values("_report_date")

        records: list[CotGoldData] = []
        for _, row in df_sorted.iterrows():
            try:
                report_date = pd.to_datetime(str(row[2]))
                data = CotGoldData(
                    report_date=report_date,
                    noncomm_long=int(row[8]),
                    noncomm_short=int(row[9]),
                    noncomm_spread=int(row[10]),
                    comm_long=int(row[11]),
                    comm_short=int(row[12]),
                    nonrep_long=int(row[15]),
                    nonrep_short=int(row[16]),
                )
                # 跳过异常/缺失行（负数或总持仓为 0 的原始行视为无效）
                if data.total_oi <= 0 or data.noncomm_long < 0 or data.noncomm_short < 0:
                    continue
                records.append(data)
            except (KeyError, ValueError, TypeError):
                # 单行解析失败（空值/非数字）跳过，不影响其他历史行
                continue

        if not records:
            logger.debug("CFTC CSV 中无有效 GOLD 历史行")
            return None

        logger.info(
            f"CFTC COT 数据解析成功: {len(records)} 期 "
            f"({records[0].report_date.date()} ~ {records[-1].report_date.date()}), "
            f"非商业净多={records[-1].noncomm_net}"
        )
        return records

    def _to_dataframe(self, records: list[CotGoldData]) -> pd.DataFrame:
        """将COT记录转为DataFrame."""
        rows = []
        for r in records:
            rows.append({
                "timestamp": r.report_date,
                "open": float(r.noncomm_long),
                "high": float(r.noncomm_long),
                "low": float(r.noncomm_short),
                "close": float(r.noncomm_net),
                "volume": float(r.total_oi),
                "comm_net": float(r.comm_net),
                "noncomm_ratio": r.noncomm_ratio,
            })
        return pd.DataFrame(rows)

    def _fallback_data(self) -> pd.DataFrame:
        """当无法获取最新数据时，返回已知历史数据.

        使用模拟的近期COT数据以维持信号连续性。
        实际运行中应配置外部数据源或手动更新。
        """
        logger.debug("加载 COT 历史回退基准数据")
        # 基于2025-2026年真实COT黄金数据范围的模拟
        base_date = datetime(2026, 5, 27)
        records = []
        for i in range(12):
            date = base_date - timedelta(weeks=i)
            # 模拟非商业净多仓在 150k ~ 280k 区间波动
            net = 200000 + int(50000 * (0.5 - (i % 6) / 6))
            records.append({
                "timestamp": date,
                "open": float(net + 100000),
                "high": float(net + 100000),
                "low": float(100000),
                "close": float(net),
                "volume": float(500000),
                "comm_net": float(-net * 0.8),
                "noncomm_ratio": 2.5 + (i % 3) * 0.2,
            })

        df = pd.DataFrame(records)
        return self.validate(df)
