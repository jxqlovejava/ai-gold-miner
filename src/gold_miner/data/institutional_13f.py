"""13F机构持仓监控 — 追踪顶级机构黄金相关持仓季度变化.

监控机构:
- Bridgewater (桥水) — 全球最大对冲基金
- Renaissance Technologies — 量化巨头
- Soros Fund Management — 索罗斯
- ARK Investment — Cathie Wood
- Berkshire Hathaway — 巴菲特

重点追踪标的:
- GLD, IAU, GLDM — 黄金ETF
- GDX, GDXJ — 黄金矿商ETF
- NEM, GOLD, AEM — 大型金矿股

数据来源:
- SEC EDGAR 13F filings
- whalewisdom.com / fintel.io (聚合解析)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from loguru import logger


@dataclass
class InstitutionPosition:
    """单机构单标持仓记录."""

    institution: str
    ticker: str
    shares: int
    value_usd: float
    quarter: str  # e.g. "Q1 2026"
    position_change_pct: float = 0.0  # 环比变化
    is_new: bool = False
    is_closed: bool = False


@dataclass
class InstitutionalSummary:
    """机构持仓汇总."""

    quarter: str
    total_institutions: int
    net_gold_bullish: int  # 增持或新开的机构数
    net_gold_bearish: int  # 减持或清仓的机构数
    top_buyers: list[InstitutionPosition] = field(default_factory=list)
    top_sellers: list[InstitutionPosition] = field(default_factory=list)
    gold_etf_total_shares: int = 0
    gold_miners_total_shares: int = 0
    # True = 来自 _fallback_summary 的占位数据 (非真实 13F filing)。
    # 消费方必须据此降权/披露, 不得当作机构真实持仓引用 (2026-09-17)。
    is_placeholder: bool = False


class Institutional13FFetcher:
    """13F机构持仓数据获取器.

    由于SEC EDGAR原始13F数据解析复杂，采用以下策略:
    1. 从 whalewisdom/fintel 等聚合站点抓取已解析数据
    2. 回退到搜索引擎获取最新季度变化信息
    """

    # 重点监控机构及其CIK编号(SEC EDGAR)
    TRACKED_INSTITUTIONS: dict[str, dict[str, Any]] = {
        "Bridgewater Associates": {
            "cik": "0001350694",
            "alias": ["Bridgewater", "桥水"],
            "weight": 1.5,  # 信号权重
        },
        "Renaissance Technologies": {
            "cik": "0001037389",
            "alias": ["Renaissance", "Rentech"],
            "weight": 1.3,
        },
        "Soros Fund Management": {
            "cik": "0001029160",
            "alias": ["Soros", "索罗斯"],
            "weight": 1.2,
        },
        "ARK Investment Management": {
            "cik": "0001697748",
            "alias": ["ARK", "Cathie Wood"],
            "weight": 1.0,
        },
        "Berkshire Hathaway": {
            "cik": "0001067983",
            "alias": ["Buffett", "巴菲特", "Berkshire"],
            "weight": 1.4,
        },
        "Two Sigma Investments": {
            "cik": "0001179392",
            "alias": ["Two Sigma"],
            "weight": 1.1,
        },
        "Citadel Advisors": {
            "cik": "0001423053",
            "alias": ["Citadel", "格里芬"],
            "weight": 1.2,
        },
    }

    # 重点黄金相关标的
    GOLD_TICKERS: set[str] = {
        # 黄金ETF
        "GLD", "IAU", "GLDM", "PHYS", "SGOL", "AAAU",
        # 黄金矿商ETF
        "GDX", "GDXJ", "RING", "SGDM",
        # 主要金矿股
        "NEM", "GOLD", "AEM", "KL", "DUST", "NUGT",
        # 白银相关
        "SLV", "SIL", "SILJ", "PSLV",
    }

    def __init__(self) -> None:
        pass

    def fetch_latest_quarter(self) -> InstitutionalSummary | None:
        """获取最新季度机构持仓汇总.

        由于实时13F数据获取复杂，使用已知最近季度数据 + 增量更新策略。
        """
        try:
            # 尝试从聚合站点获取
            positions = self._fetch_from_aggregators()
            if positions:
                return self._summarize(positions)
        except Exception as e:
            logger.debug(f"13F聚合站点获取失败: {e}")

        # 回退到已知数据
        return self._fallback_summary()

    def fetch_institution_changes(
        self,
        institution: str,
    ) -> list[InstitutionPosition]:
        """获取单个机构最新季度持仓变化."""
        # 实际实现中应解析该机构的13F filing
        # 这里返回结构化回退数据
        return []

    def _fetch_from_aggregators(self) -> list[InstitutionPosition] | None:
        """从聚合站点抓取已解析的13F数据."""
        positions: list[InstitutionPosition] = []

        # 尝试 whalewisdom
        try:
            ww_positions = self._fetch_whalewisdom()
            positions.extend(ww_positions)
        except Exception as e:
            logger.debug(f"whalewisdom获取失败: {e}")

        return positions if positions else None

    def _fetch_whalewisdom(self) -> list[InstitutionPosition]:
        """从 whalewisdom 获取数据."""
        # whalewisdom 需要登录，这里作为最佳尝试
        return []

    def _summarize(self, positions: list[InstitutionPosition]) -> InstitutionalSummary:
        """汇总机构持仓数据."""
        gold_positions = [p for p in positions if p.ticker in self.GOLD_TICKERS]

        bullish = sum(1 for p in gold_positions if p.position_change_pct > 0)
        bearish = sum(1 for p in gold_positions if p.position_change_pct < 0)

        sorted_by_change = sorted(gold_positions, key=lambda p: p.position_change_pct, reverse=True)

        etf_shares = sum(p.shares for p in gold_positions if p.ticker in {"GLD", "IAU", "GLDM"})
        miner_shares = sum(p.shares for p in gold_positions if p.ticker in {"GDX", "GDXJ", "NEM", "GOLD"})

        return InstitutionalSummary(
            quarter=self._latest_filed_quarter(),
            total_institutions=len({p.institution for p in gold_positions}),
            net_gold_bullish=bullish,
            net_gold_bearish=bearish,
            top_buyers=[p for p in sorted_by_change[:5] if p.position_change_pct > 0],
            top_sellers=[p for p in sorted_by_change[-5:] if p.position_change_pct < 0],
            gold_etf_total_shares=etf_shares,
            gold_miners_total_shares=miner_shares,
        )

    def _fallback_summary(self) -> InstitutionalSummary:
        """回退数据 — **占位常量, 不是真实 13F filing**.

        ⚠️ 2026-09-17 核查: 上游 `_fetch_whalewisdom()` 恒返回 [], 故
        `fetch_latest_quarter()` **永远**走到这里, 报告里的 "4家增持 vs 3家减持"
        与 "Berkshire Hathaway 增持2,000,000股GDX" 是下面这些字面量, 每日不变,
        却以「机构持仓」名义持续给聪明钱维度 +0.35 分 (永久看多)。

        现标记 `is_placeholder=True`, 由信号层据此归零分数并显式披露。
        真正修复需接 SEC EDGAR 13F (公开免费) 或付费聚合源。
        """
        logger.warning("13F 无真实数据源, 返回占位常量 (不可作为机构持仓依据)")
        quarter = self._latest_filed_quarter()
        return InstitutionalSummary(
            quarter=quarter,
            total_institutions=7,
            net_gold_bullish=4,
            net_gold_bearish=3,
            top_buyers=[
                InstitutionPosition("Bridgewater", "GLD", 5000000, 950000000, quarter, 0.15),
                InstitutionPosition("Berkshire Hathaway", "GDX", 2000000, 120000000, quarter, 0.08),
            ],
            top_sellers=[
                InstitutionPosition("Soros Fund", "GLD", 0, 0, quarter, -1.0, is_closed=True),
            ],
            gold_etf_total_shares=15_000_000,
            gold_miners_total_shares=8_000_000,
            is_placeholder=True,
        )

    @staticmethod
    def _latest_filed_quarter(now: datetime | None = None) -> str:
        """最近一期「已过申报窗口」的 13F 季度.

        13F 须于季末后 45 天内申报, 因此当季数据在季末 +45d 前根本不存在。
        事故 2026-09-17: 原 `_current_quarter()` 在 9/17 (Q3 进行中) 返回
        "Q3 2026", 报告显示为 "QQ3 2026" —— 标注了一个不可能已申报的季度。

        Args:
            now: 基准时间, 默认当前。

        Returns:
            形如 "Q2 2026" (2026-09-17 时点 → Q2 2026)。
        """
        now = now or datetime.now()
        quarter = (now.month - 1) // 3 + 1
        year = now.year
        for _ in range(8):
            end_month = quarter * 3
            if end_month == 12:
                q_end = datetime(year, 12, 31)
            else:
                q_end = datetime(year, end_month + 1, 1) - timedelta(days=1)
            if (now - q_end).days >= 45:
                return f"Q{quarter} {year}"
            quarter -= 1
            if quarter == 0:
                quarter, year = 4, year - 1
        return f"Q{quarter} {year}"

    @staticmethod
    def _current_quarter() -> str:
        """当前自然季度 (保留供既有调用方使用; 标注 13F 应改用 _latest_filed_quarter)."""
        now = datetime.now()
        q = (now.month - 1) // 3 + 1
        return f"Q{q} {now.year}"

    def get_bullish_score(self) -> float:
        """计算机构整体看涨分数 (-1 ~ +1)."""
        summary = self.fetch_latest_quarter()
        if summary is None:
            return 0.0

        total = summary.net_gold_bullish + summary.net_gold_bearish
        if total == 0:
            return 0.0

        return (summary.net_gold_bullish - summary.net_gold_bearish) / total
