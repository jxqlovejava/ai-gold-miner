"""反带节奏检测信号 — 识别新闻炒作与机构偏见."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loguru import logger

from gold_miner.data.institutional_13f import Institutional13FFetcher
from gold_miner.data.investment_bank_targets import InvestmentBankTargetFetcher
from gold_miner.data.news import NewsItem
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength
from gold_miner.signals.institutional_time_series import InstitutionalTimeSeriesAnalyzer
from gold_miner.storage.local import LocalFileStore

# 标题党 / 情绪化关键词
CLICKBAIT_KEYWORDS: list[str] = [
    "重磅", "震惊", "突发", "暴涨", "崩盘", "血洗", "踩踏",
    "史诗级", "历史性", " unprecedented ", " to the moon ", " moon ",
    "爆炸性", "炸裂", "惊呆了", "深夜", "重磅炸弹", "黑天鹅",
]

# 阈值常量
MIN_NEWS_POOL = 5
CLICKBAIT_RATIO_THRESHOLD = 0.30
SOURCE_CONCENTRATION_THRESHOLD = 0.60
EXTREME_SENTIMENT_THRESHOLD = 0.70
EXTREME_SENTIMENT_RATIO = 0.70
LOW_TIER_MIN_COUNT = 3
LOW_TIER_SOURCES = {"T3", "unknown"}
TARGET_DISPERSION_THRESHOLD = 0.30
FLIP_FLOP_WINDOW_DAYS = 30
FLIP_FLOP_MIN_UPSIDE = 5.0
WALK_TALK_QUARTERS = 2
WALK_TALK_SELL_THRESHOLD = -0.10


@dataclass(frozen=True)
class HypeBiasConfig:
    """反带节奏检测配置."""

    min_news_pool: int = MIN_NEWS_POOL
    clickbait_ratio_threshold: float = CLICKBAIT_RATIO_THRESHOLD
    source_concentration_threshold: float = SOURCE_CONCENTRATION_THRESHOLD
    extreme_sentiment_threshold: float = EXTREME_SENTIMENT_THRESHOLD
    extreme_sentiment_ratio: float = EXTREME_SENTIMENT_RATIO
    low_tier_min_count: int = LOW_TIER_MIN_COUNT
    target_dispersion_threshold: float = TARGET_DISPERSION_THRESHOLD
    flip_flop_window_days: int = FLIP_FLOP_WINDOW_DAYS
    flip_flop_min_upside: float = FLIP_FLOP_MIN_UPSIDE
    walk_talk_quarters: int = WALK_TALK_QUARTERS
    walk_talk_sell_threshold: float = WALK_TALK_SELL_THRESHOLD


class HypeBiasSignalGenerator:
    """反带节奏信号生成器.

    输出与炒作/恐慌方向相反的 contrarian 信号，用于提示情绪过热风险。
    """

    def __init__(
        self,
        news_items: list[NewsItem] | None = None,
        current_spot: float = 0.0,
        config: HypeBiasConfig | None = None,
    ) -> None:
        self.news_items = list(news_items) if news_items else []
        self.current_spot = current_spot
        self.config = config or HypeBiasConfig()
        self.bank_fetcher = InvestmentBankTargetFetcher()
        self.inst_13f_fetcher = Institutional13FFetcher()
        self.store = LocalFileStore()

        # 缓存机构数据，避免同一 pipeline 内重复请求
        self._bank_targets: list[Any] | None = None
        self._bank_consensus: dict[str, Any] | None = None
        self._inst_summary: Any | None = None
        self._time_series_analyzer: InstitutionalTimeSeriesAnalyzer | None = None

    def generate_signals(self) -> list[Signal]:
        """生成反带节奏信号."""
        signals: list[Signal] = []
        signals.extend(self._news_signals())
        signals.extend(self._institutional_signals())
        return signals

    # ------------------------------------------------------------------
    # 新闻面启发式
    # ------------------------------------------------------------------

    def _news_signals(self) -> list[Signal]:
        items = self.news_items
        if len(items) < self.config.min_news_pool:
            return []

        signals: list[Signal] = []
        dominant = self._dominant_news_direction(items)
        if dominant == SignalDirection.NEUTRAL:
            return []

        # 共用元数据
        base_metadata: dict[str, Any] = {
            "news_count": len(items),
            "dominant_direction": dominant.value,
        }

        signals.extend(self._h1_clickbait(items, dominant, base_metadata))
        signals.extend(self._h2_source_concentration(items, dominant, base_metadata))
        signals.extend(self._h3_sentiment_extreme(items, dominant, base_metadata))
        signals.extend(self._h4_low_tier_push(items, dominant, base_metadata))

        return signals

    def _h1_clickbait(
        self,
        items: list[NewsItem],
        dominant: SignalDirection,
        base_metadata: dict[str, Any],
    ) -> list[Signal]:
        """标题党炒作检测."""
        clickbait_count = sum(
            1 for it in items
            if any(kw.lower() in (it.title + " " + it.summary).lower() for kw in CLICKBAIT_KEYWORDS)
        )
        ratio = clickbait_count / len(items)
        if ratio < self.config.clickbait_ratio_threshold:
            return []

        contrarian = self._contrarian(dominant)
        score = self._contrarian_score(ratio, 0.3, 0.8, contrarian)
        return [Signal(
            name=f"标题党炒作过热({contrarian.value})",
            dimension="hype_bias",
            direction=contrarian,
            strength=self._strength_by_ratio(ratio),
            score=score,
            description=f"{clickbait_count}/{len(items)}条新闻含情绪化/标题党关键词，{self._heat_word(dominant)}信号可能失真",
            metadata={**base_metadata, "heuristic": "clickbait", "clickbait_count": clickbait_count, "ratio": round(ratio, 2)},
        )]

    def _h2_source_concentration(
        self,
        items: list[NewsItem],
        dominant: SignalDirection,
        base_metadata: dict[str, Any],
    ) -> list[Signal]:
        """同源洗稿 / 信源集中检测."""
        if not items:
            return []
        source_counts = Counter(it.source for it in items)
        top_source, top_count = source_counts.most_common(1)[0]
        ratio = top_count / len(items)
        if ratio < self.config.source_concentration_threshold:
            return []

        contrarian = self._contrarian(dominant)
        score = self._contrarian_score(ratio, 0.6, 0.9, contrarian)
        return [Signal(
            name=f"同源报道集中({contrarian.value})",
            dimension="hype_bias",
            direction=contrarian,
            strength=self._strength_by_ratio(ratio),
            score=score,
            description=f"{top_source}占 {top_count}/{len(items)}条报道，疑似同源洗稿/单一信源带节奏",
            metadata={**base_metadata, "heuristic": "source_concentration", "top_source": top_source, "ratio": round(ratio, 2)},
        )]

    def _h3_sentiment_extreme(
        self,
        items: list[NewsItem],
        dominant: SignalDirection,
        base_metadata: dict[str, Any],
    ) -> list[Signal]:
        """情绪极端化 / FUD / hype 检测."""
        extreme_count = sum(
            1 for it in items
            if abs(it.sentiment) >= self.config.extreme_sentiment_threshold
        )
        ratio = extreme_count / len(items)
        if ratio < self.config.extreme_sentiment_ratio:
            return []

        contrarian = self._contrarian(dominant)
        score = self._contrarian_score(ratio, 0.7, 1.0, contrarian)
        return [Signal(
            name=f"情绪极端化({contrarian.value})",
            dimension="hype_bias",
            direction=contrarian,
            strength=self._strength_by_ratio(ratio),
            score=score,
            description=f"{extreme_count}/{len(items)}条新闻情感极值 ≥{self.config.extreme_sentiment_threshold}，市场可能{self._heat_word(dominant)}",
            metadata={**base_metadata, "heuristic": "sentiment_extreme", "extreme_count": extreme_count, "ratio": round(ratio, 2)},
        )]

    def _h4_low_tier_push(
        self,
        items: list[NewsItem],
        dominant: SignalDirection,
        base_metadata: dict[str, Any],
    ) -> list[Signal]:
        """低可信源带节奏检测."""
        low_tier_items = [
            it for it in items
            if it.metadata.get("source_tier", "unknown") in LOW_TIER_SOURCES
            and (it.sentiment > 0 if dominant == SignalDirection.BULLISH else it.sentiment < 0)
        ]
        if len(low_tier_items) < self.config.low_tier_min_count:
            return []

        contrarian = self._contrarian(dominant)
        score = self._contrarian_score(len(low_tier_items), 3, 8, contrarian)
        return [Signal(
            name=f"低可信源带节奏({contrarian.value})",
            dimension="hype_bias",
            direction=contrarian,
            strength=self._strength_by_count(len(low_tier_items), 3, 8),
            score=score,
            description=f"{len(low_tier_items)}条低可信(T3/unknown)源同向推送{dominant.value}叙事",
            metadata={**base_metadata, "heuristic": "low_tier_push", "low_tier_count": len(low_tier_items)},
        )]

    # ------------------------------------------------------------------
    # 机构面启发式
    # ------------------------------------------------------------------

    def _bank_consensus_cached(self) -> dict[str, Any] | None:
        if self._bank_consensus is None and self.current_spot > 0:
            try:
                self._bank_consensus = self.bank_fetcher.fetch_consensus(self.current_spot)
            except Exception as e:
                logger.debug(f"投行目标价获取失败: {e}")
                self._bank_consensus = {"status": "error"}
        return self._bank_consensus

    def _bank_targets_cached(self) -> list[Any]:
        if self._bank_targets is None and self.current_spot > 0:
            try:
                self._bank_targets = self.bank_fetcher.fetch_all_targets(self.current_spot)
            except Exception as e:
                logger.debug(f"投行目标价列表获取失败: {e}")
                self._bank_targets = []
        return self._bank_targets or []

    def _inst_summary_cached(self) -> Any | None:
        if self._inst_summary is None:
            try:
                self._inst_summary = self.inst_13f_fetcher.fetch_latest_quarter()
            except Exception as e:
                logger.debug(f"13F 数据获取失败: {e}")
                self._inst_summary = None
        return self._inst_summary

    def _institutional_signals(self) -> list[Signal]:
        signals: list[Signal] = []

        # 先获取数据
        summary = self._inst_summary_cached()
        targets = self._bank_targets_cached()

        # 持久化历史数据（去重后追加）
        if targets:
            self._persist_bank_targets(targets)
        if summary:
            self._persist_13f_holdings(summary)

        signals.extend(self._h5_bank_target_divergence())
        signals.extend(self._h6_said_bullish_sold())
        signals.extend(self._h7_bank_target_flip_flop())
        signals.extend(self._h8_walk_talk_mismatch())
        return signals

    def _h5_bank_target_divergence(self) -> list[Signal]:
        """投行目标价离散度检测：共识看涨但分歧大 → 警惕过热."""
        if self.current_spot <= 0:
            return []

        consensus = self._bank_consensus_cached()
        if not consensus or consensus.get("status") != "ok":
            return []

        total = consensus.get("total_banks", 0)
        bullish = consensus.get("bullish_count", 0)
        upside = consensus.get("upside_pct", 0)
        highest = consensus.get("highest_target", 0)
        lowest = consensus.get("lowest_target", 0)
        avg = consensus.get("avg_target", 0)

        if total < 5 or avg <= 0:
            return []

        dispersion = (highest - lowest) / avg
        if dispersion <= self.config.target_dispersion_threshold:
            return []

        # 共识看涨但离散度大 → 炒作过热，输出 bearish
        if bullish / total >= 0.6 and upside > 0:
            return [Signal(
                name="投行目标价分歧大(炒作过热)",
                dimension="hype_bias",
                direction=SignalDirection.BEARISH,
                strength=self._strength_by_ratio(dispersion, threshold=0.3, cap=0.6),
                score=-round(min(dispersion, 0.6), 2),
                description=f"{bullish}/{total}家投行看涨但目标价离散度 {dispersion:.1%}，上涨空间{upside:.1f}%，警惕一致性预期反转",
                metadata={
                    "heuristic": "bank_target_divergence",
                    "dispersion": round(dispersion, 3),
                    "bullish_ratio": round(bullish / total, 2),
                    "upside_pct": upside,
                    "highest_target": highest,
                    "lowest_target": lowest,
                },
            )]

        return []

    def _h6_said_bullish_sold(self) -> list[Signal]:
        """唱多做空 / 减持检测：机构口头看涨但 13F 显示减仓."""
        if self.current_spot <= 0:
            return []

        consensus = self._bank_consensus_cached()
        summary = self._inst_summary_cached()

        if not consensus or consensus.get("status") != "ok" or summary is None:
            return []

        signals: list[Signal] = []
        bullish_banks = {b.lower() for b in self._extract_bullish_banks()}
        if not bullish_banks:
            return []

        # 13F top_sellers 中若包含近期看涨投行或其关联机构 → 反向 bearish
        for seller in summary.top_sellers:
            seller_name = seller.institution.lower()
            for bank in bullish_banks:
                if self._name_overlap(bank, seller_name):
                    signals.append(Signal(
                        name="机构唱多做空信号",
                        dimension="hype_bias",
                        direction=SignalDirection.BEARISH,
                        strength=SignalStrength.MODERATE,
                        score=-0.4,
                        description=f"{seller.institution} 13F 显示减持 {seller.ticker}，与投行目标价看涨方向冲突",
                        metadata={
                            "heuristic": "said_bullish_sold",
                            "institution": seller.institution,
                            "ticker": seller.ticker,
                            "position_change_pct": seller.position_change_pct,
                        },
                    ))
                    break

        return signals

    def _persist_bank_targets(self, targets: list[Any]) -> None:
        """持久化本次获取的投行目标价."""
        now = datetime.now().isoformat()
        for t in targets:
            try:
                self.store.append_bank_target({
                    "timestamp": now,
                    "bank": t.bank,
                    "target_price": t.target_price,
                    "current_spot": t.current_price,
                    "upside_pct": round(t.upside_pct, 2),
                    "rating": t.rating,
                    "horizon": t.horizon,
                })
            except Exception as e:
                logger.debug(f"持久化投行目标价失败 [{t.bank}]: {e}")

    def _persist_13f_holdings(self, summary: Any) -> None:
        """持久化本次 13F 持仓数据."""
        now = datetime.now().isoformat()
        for seller in summary.top_sellers:
            try:
                self.store.append_institutional_13f({
                    "timestamp": now,
                    "quarter": summary.quarter,
                    "institution": seller.institution,
                    "ticker": seller.ticker,
                    "shares": seller.shares,
                    "value_usd": seller.value_usd,
                    "position_change_pct": seller.position_change_pct,
                    "is_new": seller.is_new,
                    "is_closed": seller.is_closed,
                })
            except Exception as e:
                logger.debug(f"持久化 13F 持仓失败 [{seller.institution}]: {e}")

        for buyer in summary.top_buyers:
            try:
                self.store.append_institutional_13f({
                    "timestamp": now,
                    "quarter": summary.quarter,
                    "institution": buyer.institution,
                    "ticker": buyer.ticker,
                    "shares": buyer.shares,
                    "value_usd": buyer.value_usd,
                    "position_change_pct": buyer.position_change_pct,
                    "is_new": buyer.is_new,
                    "is_closed": buyer.is_closed,
                })
            except Exception as e:
                logger.debug(f"持久化 13F 持仓失败 [{buyer.institution}]: {e}")

    def _load_time_series_analyzer(self) -> InstitutionalTimeSeriesAnalyzer:
        if self._time_series_analyzer is None:
            history = self.store.load_bank_target_history()
            self._time_series_analyzer = InstitutionalTimeSeriesAnalyzer(history)
        return self._time_series_analyzer

    def _h7_bank_target_flip_flop(self) -> list[Signal]:
        """H7: 投行目标价 30 天内方向反转."""
        if self.current_spot <= 0:
            return []

        analyzer = self._load_time_series_analyzer()
        flip_flops = analyzer.detect_target_flip_flops(
            window_days=self.config.flip_flop_window_days,
            min_upside_threshold=self.config.flip_flop_min_upside,
        )

        signals: list[Signal] = []
        for ff in flip_flops:
            # contrarian: 从 bearish 转 bullish 视为追多带节奏 → bearish
            # 从 bullish 转 bearish 视为杀跌过度 → bullish
            if ff.previous_direction == "bearish" and ff.current_direction == "bullish":
                direction = SignalDirection.BEARISH
            elif ff.previous_direction == "bullish" and ff.current_direction == "bearish":
                direction = SignalDirection.BULLISH
            else:
                continue

            change_pct = abs(ff.current_target - ff.previous_target) / ff.previous_target
            score = 0.5 if change_pct >= 0.15 else 0.3
            score = -score if direction == SignalDirection.BEARISH else score

            signals.append(Signal(
                name=f"{ff.bank}目标价方向反转({direction.value})",
                dimension="hype_bias",
                direction=direction,
                strength=SignalStrength.MODERATE if change_pct < 0.15 else SignalStrength.STRONG,
                score=round(score, 2),
                description=(
                    f"{ff.bank} {ff.previous_date.strftime('%m-%d')} {ff.previous_direction} "
                    f"→ {ff.current_date.strftime('%m-%d')} {ff.current_direction}，"
                    f"目标价从 {ff.previous_target:.0f} 变为 {ff.current_target:.0f}"
                ),
                metadata={
                    "heuristic": "bank_target_flip_flop",
                    "bank": ff.bank,
                    "previous_target": ff.previous_target,
                    "current_target": ff.current_target,
                    "previous_direction": ff.previous_direction,
                    "current_direction": ff.current_direction,
                },
            ))

        return signals

    def _h8_walk_talk_mismatch(self) -> list[Signal]:
        """H8: 投行口头看涨但关联机构 13F 净卖出."""
        if self.current_spot <= 0:
            return []

        consensus = self._bank_consensus_cached()
        if not consensus or consensus.get("status") != "ok":
            return []

        bullish_banks = {b.lower(): b for b in self._extract_bullish_banks()}
        if not bullish_banks:
            return []

        holdings_history = self.store.load_institutional_13f_history()
        recent_holdings = [
            h for h in holdings_history
            if self._is_recent_quarter(h.get("quarter", ""), self.config.walk_talk_quarters)
        ]

        analyzer = self._load_time_series_analyzer()
        mismatches = analyzer.detect_walk_talk_mismatches(
            bank_targets=[{"bank": name, "direction": "bullish"} for name in bullish_banks.values()],
            holdings_13f=recent_holdings,
            sell_threshold=self.config.walk_talk_sell_threshold,
        )

        signals: list[Signal] = []
        seen: set[tuple[str, str, str]] = set()
        for mm in mismatches:
            key = (mm.bank, mm.institution, mm.ticker)
            if key in seen:
                continue
            seen.add(key)

            signals.append(Signal(
                name="言行不一：口头看涨但减持",
                dimension="hype_bias",
                direction=SignalDirection.BEARISH,
                strength=SignalStrength.MODERATE,
                score=-0.35,
                description=(
                    f"{mm.bank} 目标价看涨，但 {mm.institution} 13F "
                    f"显示 {mm.ticker} 减持 {abs(mm.position_change_pct):.0%}"
                ),
                metadata={
                    "heuristic": "walk_talk_mismatch",
                    "bank": mm.bank,
                    "institution": mm.institution,
                    "ticker": mm.ticker,
                    "position_change_pct": mm.position_change_pct,
                    "quarter": mm.quarter,
                },
            ))

        return signals

    @staticmethod
    def _is_recent_quarter(quarter: str, n: int) -> bool:
        """判断 quarter 是否在最近 n 个季度内."""
        try:
            parts = quarter.split()
            if len(parts) != 2:
                return False
            q_str, year_str = parts
            q = int(q_str.replace("Q", ""))
            year = int(year_str)
        except (ValueError, AttributeError):
            return False

        now = datetime.now()
        current_q = (now.month - 1) // 3 + 1
        current_year = now.year

        # 计算距离当前季度的季度数
        quarters_diff = (current_year - year) * 4 + (current_q - q)
        return 0 <= quarters_diff < n

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _dominant_news_direction(items: list[NewsItem]) -> SignalDirection:
        """判断新闻池主导方向."""
        bullish = sum(1 for it in items if it.sentiment > 0.1)
        bearish = sum(1 for it in items if it.sentiment < -0.1)
        neutral = len(items) - bullish - bearish

        if bullish > bearish and bullish > neutral:
            return SignalDirection.BULLISH
        if bearish > bullish and bearish > neutral:
            return SignalDirection.BEARISH
        return SignalDirection.NEUTRAL

    @staticmethod
    def _contrarian(direction: SignalDirection) -> SignalDirection:
        if direction == SignalDirection.BULLISH:
            return SignalDirection.BEARISH
        if direction == SignalDirection.BEARISH:
            return SignalDirection.BULLISH
        return SignalDirection.NEUTRAL

    @staticmethod
    def _heat_word(direction: SignalDirection) -> str:
        return "看涨炒作" if direction == SignalDirection.BULLISH else "恐慌抛售"

    @staticmethod
    def _strength_by_ratio(ratio: float, threshold: float = 0.3, cap: float = 0.8) -> SignalStrength:
        if ratio >= cap:
            return SignalStrength.STRONG
        if ratio >= threshold:
            return SignalStrength.MODERATE
        return SignalStrength.WEAK

    @staticmethod
    def _strength_by_count(count: int, threshold: int, strong: int) -> SignalStrength:
        if count >= strong:
            return SignalStrength.STRONG
        if count >= threshold:
            return SignalStrength.MODERATE
        return SignalStrength.WEAK

    @staticmethod
    def _contrarian_score(
        value: float,
        threshold: float,
        cap: float,
        direction: SignalDirection,
    ) -> float:
        """根据偏离程度计算反向信号分数."""
        normalized = min((value - threshold) / (cap - threshold), 1.0) if cap > threshold else 1.0
        score = 0.2 + normalized * 0.6  # 0.2 ~ 0.8
        if direction == SignalDirection.BEARISH:
            score = -score
        return round(score, 2)

    def _extract_bullish_banks(self) -> list[str]:
        """从缓存的投行目标价中提取看涨银行名."""
        targets = self._bank_targets_cached()
        return [t.bank for t in targets if t.is_bullish]

    @staticmethod
    def _name_overlap(a: str, b: str) -> bool:
        """简单名称重叠检测."""
        a_tokens = set(a.lower().split())
        b_tokens = set(b.lower().split())
        return bool(a_tokens & b_tokens)
