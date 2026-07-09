"""完整分析管线 — 封装 run_scan() 硬编码流程为可配置、可测试的类."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from loguru import logger

from gold_miner.config import settings
from gold_miner.data.jd_accumulation_gold import JdAccumulationGoldFetcher, JdGoldPrice
from gold_miner.data.macro import MacroDataFetcher
from gold_miner.data.news import NewsFetcher, NewsItem
from gold_miner.data.sentiment import SentimentDataFetcher
from gold_miner.data.spot_gold import SpotGoldFetcher
from gold_miner.decision.agents import AgentOpinion, BearAgent, BullAgent, PortfolioManager
from gold_miner.decision.risk import RiskCheck, RiskManager
from gold_miner.doctrine import DoctrineChecker
from gold_miner.doctrine.munger_models import GOLD_MODELS, MungerModel
from gold_miner.doctrine.rules import ALL_RULES
from gold_miner.events.models import EventType
from gold_miner.events.store import EventStore
from gold_miner.execution.alert import PriceAlert
from gold_miner.execution.dashboard import DashboardFormatter, TradeDecision
from gold_miner.execution.dimensions import print_all_dimensions
from gold_miner.execution.notifier import Notifier
from gold_miner.experience import ExperienceLoader
from gold_miner.improvement.tracker import PredictionRecord, PredictionTracker
from gold_miner.llm.client import LLMClient
from gold_miner.signals.base import Signal, SignalBundle, SignalDirection, SignalStrength
from gold_miner.signals.economic_calendar import EconomicCalendarSignalGenerator
from gold_miner.signals.engine import ScoringEngine
from gold_miner.signals.etf_flow_signal import EtfFlowSignalGenerator
from gold_miner.signals.fundamental import FundamentalAnalyzer
from gold_miner.signals.news_signal import NewsSignalGenerator
from gold_miner.signals.sentiment_signal import SentimentAnalyzer
from gold_miner.signals.technical import TechnicalAnalyzer
from gold_miner.storage import get_store

_KEYWORD_RE = re.compile(r"[a-z]+|\d+|[一-鿿]+")
_PROJECT_DATA_DIR = Path(__file__).parents[3] / "data"


@dataclass
class AnalysisContext:
    """分析输入上下文."""

    days: int = 30
    with_news: bool = True
    with_sentiment: bool = True
    deep: bool = False
    risk_profile: str = "moderate"
    skip_tracking: bool = False
    skip_doctrine: bool = False
    skip_alerts: bool = False
    skip_dashboard: bool = False
    skip_notification: bool = False


@dataclass
class AnalysisResult:
    """分析输出结果."""

    bundle: SignalBundle = field(default_factory=SignalBundle)
    decision: dict[str, Any] = field(default_factory=dict)
    final_decision: dict[str, Any] = field(default_factory=dict)
    checks: list[RiskCheck] = field(default_factory=list)
    doctrine_ctx: dict[str, Any] = field(default_factory=dict)
    doctrine_result: Any = None
    prediction_id: str = ""
    current_price: float = 0.0
    intl_price: float = 0.0  # 国际金价 (USD/oz)
    minsheng_accumulation_price: float = 0.0
    minsheng_accumulation_change_pct: str = ""
    gold_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    dxy_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    rate_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    silver_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    breakeven_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    news_raw: list[NewsItem] = field(default_factory=list)
    au_df: pd.DataFrame | None = None
    bull_opinion: AgentOpinion | None = None
    bear_opinion: AgentOpinion | None = None
    trade_decision: TradeDecision | None = None
    alerts: list[Any] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    experience_reminders: list[str] = field(default_factory=list)
    investor_profile: str = ""
    portfolio: dict[str, Any] = field(default_factory=dict)
    munger_models: list[dict[str, Any]] = field(default_factory=list)


class AnalysisPipeline:
    """完整分析管线 — 9步流程.

    Steps:
        1. collect       — 数据采集
        2. generate_signals — 信号生成
        3. source_truth  — 来源验证 (FactChecker 已在 news pipeline 中运行)
        4. agent_debate  — 多空辩论
        5. risk_check    — 风控审查
        6. doctrine_check — 军规审查
        7. munger_models — Munger 思维模型
        8. decide        — 决策输出
        9. track         — 自动追踪
    """

    def __init__(self) -> None:
        self._steps: list[str] = [
            "collect",
            "generate_signals",
            "source_truth",
            "agent_debate",
            "risk_check",
            "doctrine_check",
            "munger_models",
            "decide",
            "track",
        ]

    def run(self, ctx: AnalysisContext | None = None) -> AnalysisResult:
        """执行完整分析管线."""
        ctx = ctx or AnalysisContext()
        result = AnalysisResult()
        result.messages.append(f"开始分析: days={ctx.days}, news={ctx.with_news}, sentiment={ctx.with_sentiment}")

        # Step 1: collect
        self._step_collect(ctx, result)
        if result.gold_df.empty:
            result.messages.append("采集失败: 无法获取金价数据")
            return result

        # Step 2: generate_signals
        self._step_generate_signals(ctx, result)

        # Step 3: source_truth (FactChecker already runs in news pipeline)
        self._step_source_truth(ctx, result)

        # Step 4: agent_debate
        self._step_agent_debate(ctx, result)

        # Step 5: risk_check
        self._step_risk_check(ctx, result)

        # Step 6: doctrine_check
        if not ctx.skip_doctrine:
            self._step_doctrine_check(ctx, result)

        # Step 7: munger_models
        self._step_munger_models(ctx, result)

        # Step 8: decide
        if not ctx.skip_dashboard:
            self._step_decide(ctx, result)

        # Step 9: track
        if not ctx.skip_tracking:
            self._step_track(ctx, result)

        return result

    # ------------------------------------------------------------------
    # Step 1: 数据采集
    # ------------------------------------------------------------------

    def _step_collect(self, ctx: AnalysisContext, result: AnalysisResult) -> None:
        logger.info("[1/9] 数据采集...")

        gold_fetcher = SpotGoldFetcher()
        result.gold_df = gold_fetcher.fetch(days=ctx.days)
        if result.gold_df.empty:
            logger.error("现货黄金数据获取失败")
            return

        result.current_price = result.gold_df["close"].iloc[-1]
        logger.info(f"国内金价 Au99.99: {result.current_price:.2f} 元/克 (来源: SGE/jinjia)")

        # 同步获取国际金价 (USD/oz)，用于跨市场对比
        try:
            intl_quote = gold_fetcher.fetch_international_quote()
            if intl_quote and intl_quote[0].get("price"):
                result.intl_price = intl_quote[0]["price"]
                logger.info(
                    f"国际金价 XAU/USD: {result.intl_price:.2f} 美元/盎司 "
                    f"({intl_quote[0].get('name', '伦敦金')})"
                )
        except Exception as e:
            logger.debug(f"国际金价获取失败: {e}")

        # 同步获取民生银行积存金价格，用于与 Au99.99 现货价格交叉对照
        ms_price = self._fetch_minsheng_accumulation_price()
        if ms_price:
            result.minsheng_accumulation_price = ms_price.price
            result.minsheng_accumulation_change_pct = ms_price.change_pct
            logger.info(
                f"民生银行积存金: {result.minsheng_accumulation_price:.2f} 元/克 "
                f"({result.minsheng_accumulation_change_pct})"
            )

        macro_fetcher = MacroDataFetcher()
        result.dxy_df = macro_fetcher.fetch_dxy()
        result.rate_df = macro_fetcher.fetch_real_rate()
        result.silver_df = macro_fetcher.fetch_silver()
        result.breakeven_df = macro_fetcher.fetch_breakeven()

        if not result.rate_df.empty:
            logger.info(f"实际利率最新: {result.rate_df['value'].iloc[-1]:.2f}%")
        if not result.breakeven_df.empty:
            logger.info(f"通胀预期最新: {result.breakeven_df['value'].iloc[-1]:.2f}%")
        if not result.silver_df.empty:
            silver_price = result.silver_df["value"].iloc[-1]
            logger.info(f"白银最新价: {silver_price:.2f}")

        # 价格预警 (可选)
        if not ctx.skip_alerts:
            try:
                alert_mgr = PriceAlert()
                silver_price = result.silver_df["value"].iloc[-1] if not result.silver_df.empty else None
                result.alerts = alert_mgr.check_all(
                    gold_df=result.gold_df,
                    dxy_df=result.dxy_df,
                    silver_price=silver_price,
                )
            except Exception as e:
                logger.debug(f"价格预警检查异常: {e}")

        logger.info("[1/9] 数据采集完成")

    def _fetch_minsheng_accumulation_price(self) -> JdGoldPrice | None:
        """抓取民生银行积存金当前价格."""
        try:
            return JdAccumulationGoldFetcher(bank="MS").fetch_price()
        except Exception as e:
            logger.debug(f"民生银行积存金价格获取失败: {e}")
            return None

    # ------------------------------------------------------------------
    # Step 2: 信号生成
    # ------------------------------------------------------------------

    def _step_generate_signals(self, ctx: AnalysisContext, result: AnalysisResult) -> None:
        logger.info("[2/9] 信号生成...")

        bundle = SignalBundle()

        # 技术面
        tech = TechnicalAnalyzer(result.gold_df)
        for sig in tech.generate_signals():
            bundle.add(sig)
        logger.info(f"技术信号: {len(bundle.by_dimension('technical'))} 个")

        # 基本面
        fundamental = FundamentalAnalyzer(
            gold_df=result.gold_df,
            dxy_df=result.dxy_df,
            rate_df=result.rate_df,
            silver_df=result.silver_df,
            breakeven_df=result.breakeven_df,
        )
        for sig in fundamental.generate_signals():
            bundle.add(sig)
        logger.info(f"基本面信号: {len(bundle.by_dimension('fundamental'))} 个")

        # 消息面
        news_signals: list[Signal] = []
        result.news_raw = []
        if ctx.with_news:
            news_gen = NewsSignalGenerator()
            news_signals = news_gen.fetch_and_analyze(hours=24)
            try:
                nf = NewsFetcher()
                result.news_raw = nf.fetch_latest(max_results=6)
                result.news_raw = nf.analyze_sentiment(result.news_raw)
            except Exception:
                pass
            logger.info(f"新闻信号: {len(news_signals)} 个")

        for sig in news_signals:
            bundle.add(sig)
        logger.info(f"消息面信号: {len(bundle.by_dimension('news'))} 个")

        # 情绪面
        if ctx.with_sentiment:
            try:
                sentiment_fetcher = SentimentDataFetcher()
                result.au_df = sentiment_fetcher.fetch_au_futures(lookback=60)
                sentiment_analyzer = SentimentAnalyzer(au_df=result.au_df)
                for sig in sentiment_analyzer.generate_signals():
                    bundle.add(sig)
            except Exception as e:
                logger.warning(f"情绪面数据获取异常，跳过: {e}")
        logger.info(f"情绪面信号: {len(bundle.by_dimension('sentiment'))} 个")

        # ETF 资金流
        try:
            etf_gen = EtfFlowSignalGenerator()
            for sig in etf_gen.generate_signals():
                bundle.add(sig)
        except Exception as e:
            logger.debug(f"ETF资金流信号异常: {e}")

        # 经济日历事件提醒
        try:
            ec_gen = EconomicCalendarSignalGenerator()
            for sig in ec_gen.generate_signals():
                bundle.add(sig)
            logger.info(f"经济日历事件: {len(bundle.by_dimension('event_calendar'))} 个")
        except Exception as e:
            logger.warning(f"经济日历信号异常: {e}")
            result.messages.append(f"[事件日历] 加载失败: {e}")

        # 事件结果驱动信号（已发布事件的实际 vs 预期偏差）
        try:
            from gold_miner.signals.event_driven import EventDrivenSignalGenerator

            event_driven_gen = EventDrivenSignalGenerator()
            post_event_signals = (
                event_driven_gen.generate_post_event_signals_from_calendar(
                    lookback_days=7,
                )
            )
            for sig in post_event_signals:
                bundle.add(sig)
            logger.info(
                f"事件结果信号: {len(bundle.by_dimension('event'))} 个"
            )
        except Exception as e:
            logger.warning(f"事件结果信号异常: {e}")

        # DeepSeek 深度分析
        if ctx.deep and news_signals:
            try:
                logger.info("[DeepSeek] 深度分析新闻...")
                llm = LLMClient()
                news_text = "\n".join(
                    f"- [{s.metadata.get('source', '?')}] {s.description}"
                    for s in news_signals
                )[:3000]
                llm_result = llm.analyze_article(
                    text=news_text,
                    rule_sentiment=(
                        "bullish" if bundle.composite_score > 0.1
                        else "bearish" if bundle.composite_score < -0.1
                        else "neutral"
                    ),
                    rule_score=bundle.composite_score,
                )
                if llm_result and not llm_result.get("parse_error"):
                    direction = llm_result.get("sentiment", "neutral")
                    conf = llm_result.get("confidence", 0.5)
                    score_impact = conf if direction == "bullish" else -conf
                    bundle.add(Signal(
                        name="DeepSeek 新闻深度分析",
                        dimension="news",
                        direction=SignalDirection.BULLISH if direction == "bullish" else SignalDirection.BEARISH if direction == "bearish" else SignalDirection.NEUTRAL,
                        strength=SignalStrength.MODERATE if conf > 0.6 else SignalStrength.WEAK,
                        score=round(score_impact, 2),
                        description=llm_result.get("reasoning", "")[:150],
                    ))
                    logger.info(f"DeepSeek 分析完成: {direction} (置信度 {conf:.0%})")
            except Exception as e:
                logger.warning(f"DeepSeek分析异常: {e}")

        # 打分
        engine = ScoringEngine()
        engine.score(bundle)
        logger.info(f"综合评分: {bundle.composite_score:+.2f} | 置信度: {bundle.confidence:.0%}")

        result.bundle = bundle
        logger.info("[2/9] 信号生成完成")

    # ------------------------------------------------------------------
    # Step 3: 来源验证
    # ------------------------------------------------------------------

    def _step_source_truth(self, ctx: AnalysisContext, result: AnalysisResult) -> None:
        """来源验证 — 跨维度一致性检查 + source tier 覆盖审计."""
        logger.info("[3/9] 来源验证...")

        bundle = result.bundle
        if not bundle.signals:
            logger.info("[3/9] 无信号，跳过来源验证")
            return

        warnings: list[str] = []

        # --- 1. Source tier 覆盖审计 ---
        tier_coverage = self._audit_source_tiers(bundle)
        for dim, tiers in tier_coverage.items():
            if not tiers:
                warnings.append(f"[source_tier] {dim} 维度缺少来源标注")
            elif all(t in ("T3", "unknown") for t in tiers):
                warnings.append(f"[source_tier] {dim} 维度全部为低可信源 (T3/unknown)")

        # --- 2. 跨维度方向一致性 ---
        inconsistencies = self._check_cross_dimension_consistency(bundle)
        warnings.extend(inconsistencies)

        # --- 3. 调整置信度 ---
        if inconsistencies:
            penalty = min(len(inconsistencies) * 0.05, 0.15)
            old_conf = bundle.confidence
            bundle.confidence = max(0.1, bundle.confidence - penalty)
            logger.info(
                f"[3/9] 跨维度不一致 ({len(inconsistencies)}项)，"
                f"置信度 {old_conf:.0%} → {bundle.confidence:.0%}"
            )

        for w in warnings:
            result.messages.append(f"[source_truth] {w}")
            logger.info(f"  {w}")

        if not warnings:
            logger.info("[3/9] 来源验证通过，无异常")
        else:
            logger.info(f"[3/9] 来源验证完成 ({len(warnings)} 项提醒)")

    @staticmethod
    def _audit_source_tiers(bundle: SignalBundle) -> dict[str, set[str]]:
        """审计各维度的 source_tier 覆盖.

        Returns:
            {dimension: set of source_tier values}
        """
        coverage: dict[str, set[str]] = {}
        for sig in bundle.signals:
            tier = sig.metadata.get("source_tier", "unknown")
            coverage.setdefault(sig.dimension, set()).add(tier)
        return coverage

    @staticmethod
    def _check_cross_dimension_consistency(bundle: SignalBundle) -> list[str]:
        """检查跨维度信号方向一致性.

        对比各维度的多空方向占比，发现矛盾时发出警告。
        """
        from gold_miner.signals.base import SignalDirection

        # 按维度统计方向
        dim_direction: dict[str, dict[str, int]] = {}
        for sig in bundle.signals:
            if sig.direction == SignalDirection.NEUTRAL:
                continue
            d = dim_direction.setdefault(sig.dimension, {"bullish": 0, "bearish": 0})
            if sig.direction == SignalDirection.BULLISH:
                d["bullish"] += 1
            else:
                d["bearish"] += 1

        # 计算每个维度的主导方向
        dim_stance: dict[str, tuple[str, float]] = {}
        for dim, counts in dim_direction.items():
            total = counts["bullish"] + counts["bearish"]
            if total == 0:
                continue
            bull_pct = counts["bullish"] / total
            if bull_pct >= 0.6:
                dim_stance[dim] = ("bullish", bull_pct)
            elif bull_pct <= 0.4:
                dim_stance[dim] = ("bearish", 1 - bull_pct)
            # 40-60% → 无主导方向，不参与对比

        # 对比维度对
        dims = list(dim_stance.keys())
        warnings: list[str] = []
        for i in range(len(dims)):
            for j in range(i + 1, len(dims)):
                d1, d2 = dims[i], dims[j]
                s1, s2 = dim_stance[d1], dim_stance[d2]
                if s1[0] != s2[0] and s1[1] > 0.6 and s2[1] > 0.6:
                    warnings.append(
                        f"维度矛盾: {d1}({s1[0]} {s1[1]:.0%}) vs "
                        f"{d2}({s2[0]} {s2[1]:.0%})，信号方向相反"
                    )

        return warnings

    # ------------------------------------------------------------------
    # Step 4: Agent 辩论
    # ------------------------------------------------------------------

    def _step_agent_debate(self, ctx: AnalysisContext, result: AnalysisResult) -> None:
        logger.info("[4/9] Agent 辩论...")

        bull = BullAgent()
        bear = BearAgent()
        pm = PortfolioManager()

        result.bull_opinion = bull.analyze(result.bundle)
        result.bear_opinion = bear.analyze(result.bundle)
        result.decision = pm.decide(
            result.bull_opinion,
            result.bear_opinion,
            result.bundle,
            risk_profile=ctx.risk_profile or settings.risk_profile,
        )

        logger.info("[4/9] Agent 辩论完成")

    # ------------------------------------------------------------------
    # Step 5: 风控审查
    # ------------------------------------------------------------------

    def _step_risk_check(self, ctx: AnalysisContext, result: AnalysisResult) -> None:
        logger.info("[5/9] 风控审查...")

        risk_mgr = RiskManager(max_position_pct=settings.max_position_pct)
        result.checks = risk_mgr.check(result.decision)
        result.final_decision = risk_mgr.apply_risk_controls(result.decision, result.checks)

        if result.final_decision.get("risk_override"):
            logger.info(f"风控干预: {result.final_decision['risk_override']}")
        else:
            logger.info(f"风控通过 ({len(result.checks)}项检查)")

        logger.info("[5/9] 风控审查完成")

    # ------------------------------------------------------------------
    # Step 6: 军规审查
    # ------------------------------------------------------------------

    def _step_doctrine_check(self, ctx: AnalysisContext, result: AnalysisResult) -> None:
        logger.info("[6/9] 军规审查...")

        active_dims = [d for d in ["technical", "fundamental", "news", "sentiment"]
                       if result.bundle.by_dimension(d)]
        result.doctrine_ctx = {
            "current_exposure": result.final_decision.get("position_pct", 0) * 0.5,
            "gold_allocation_pct": result.final_decision.get("position_pct", 0),
            "daily_change_pct": (
                abs(result.gold_df["close"].iloc[-1] / result.gold_df["close"].iloc[-2] - 1) * 100
                if len(result.gold_df) >= 2 else 0
            ),
            "near_data_event": False,
            "consecutive_stops": 0,
            "vix": 0,
            "fear_greed_index": 50,
            "unrealized_pnl_pct": 0.0,
            "has_trailing_stop": result.final_decision.get("position_pct", 0) > 0,
            "bullish_signal_count": result.bundle.bullish_count(),
            "bearish_signal_count": result.bundle.bearish_count(),
            "active_dimensions": active_dims,
            "bull_confidence": result.decision.get("bull_confidence", 0),
            "bear_confidence": result.decision.get("bear_confidence", 0),
            "stop_loss_set": result.final_decision.get("position_pct", 0) > 0,
            "has_decision_record": True,
        }

        checker = DoctrineChecker()
        doctrine_result = checker.check(result.final_decision, result.doctrine_ctx)
        result.doctrine_result = doctrine_result
        result.final_decision = checker.apply_doctrine(result.final_decision, doctrine_result)

        logger.info("[6/9] 军规审查完成")

    # ------------------------------------------------------------------
    # Step 7: Munger 思维模型
    # ------------------------------------------------------------------

    def _step_munger_models(self, ctx: AnalysisContext, result: AnalysisResult) -> None:
        """选择与当前情景最相关的 Munger 模型并应用仓位约束.

        每个匹配模型可能触发仓位调整因子（仅缩减，不放大），
        合成因子取所有触发规则的最小值。
        """
        logger.info("[7/9] Munger 思维模型...")

        models = self._select_munger_models(result.bundle, count=3)
        adjustments: list[dict[str, Any]] = []
        composite_factor = 1.0

        current_pos = result.final_decision.get("position_pct", 0)

        for model in models:
            adj = self._model_adjustment(model, result)
            if adj:
                adjustments.append(adj)
                composite_factor = min(composite_factor, adj["factor"])

        # 存储到 result
        result.munger_models = [
            {
                "name_cn": m.name_cn,
                "name_en": m.name_en,
                "description": m.description,
                "gold_relevance_reason": m.gold_relevance_reason,
                "adjustment": next(
                    (a for a in adjustments if a["slug"] == m.slug), None
                ),
            }
            for m in models
        ]

        # 应用仓位调整（仅缩减，不低于 5% 观察仓）
        if composite_factor < 1.0 and current_pos > 0:
            new_pos = max(round(current_pos * composite_factor, 2), 0.05)
            result.final_decision["position_pct"] = new_pos
            result.final_decision["munger_adjustment"] = {
                "factor": composite_factor,
                "adjustments": adjustments,
                "original_position": current_pos,
                "adjusted_position": new_pos,
            }

        logger.info(
            f"[7/9] Munger 模型: {len(models)}个选中, "
            f"合成因子 {composite_factor:.2f}"
        )

    @staticmethod
    def _model_adjustment(model: MungerModel, result: AnalysisResult) -> dict[str, Any] | None:
        """基于模型 slug 匹配仓位约束规则.

        返回 {"slug": ..., "model_name": ..., "factor": ..., "reason": ...} 或 None.
        """
        rules: dict[str, Any] = {
            "margin-of-safety": lambda r: {
                "factor": 0.80,
                "reason": "安全边际：降低仓位为判断错误留缓冲",
            }
            if r.final_decision.get("position_pct", 0) > 0.3
            else None,
            "overoptimism-tendency": lambda r: {
                "factor": 0.85,
                "reason": "过度乐观倾向：系统性高估好结果概率，降低仓位防范",
            }
            if r.final_decision.get("bull_confidence", 0.5) > 0.7
            else None,
            "social-proof": lambda r: {
                "factor": 0.80,
                "reason": "羊群行为：单方向信号过度集中，社会认同驱动时主动降仓",
            }
            if (
                r.bundle.bullish_count() / max(len(r.bundle.signals), 1) > 0.8
                or r.bundle.bearish_count() / max(len(r.bundle.signals), 1) > 0.8
            )
            else None,
            "inversion": lambda r: {
                "factor": 0.85,
                "reason": "逆向思维：先搞清楚什么会导致失败，主动降低仓位留余地",
            }
            if abs(r.bundle.composite_score) > 0.5
            else None,
            "circle-of-competence": lambda r: {
                "factor": 0.70,
                "reason": "能力圈：置信度不足时缩小操作规模，不在模糊区域下重注",
            }
            if r.bundle.confidence < 0.4
            else None,
            "incentive-cause-bias": lambda r: {
                "factor": 0.75,
                "reason": "激励偏见：检测到机构带节奏信号，主动降低仓位防被误导",
            }
            if any(
                s.dimension == "hype_bias" and s.score < -0.2
                for s in r.bundle.signals
            )
            else None,
        }

        rule = rules.get(model.slug)
        if rule:
            adj = rule(result)
            if adj:
                adj["slug"] = model.slug
                adj["model_name"] = model.name_cn
                return adj
        return None

    # ------------------------------------------------------------------
    # Step 8: 决策输出
    # ------------------------------------------------------------------

    def _step_decide(self, ctx: AnalysisContext, result: AnalysisResult) -> None:
        logger.info("[8/9] 决策输出...")

        # 四维度详细输出
        print_all_dimensions(
            gold_df=result.gold_df,
            dxy_df=result.dxy_df,
            rate_df=result.rate_df,
            breakeven_df=result.breakeven_df,
            silver_df=result.silver_df,
            news_items=result.news_raw if ctx.with_news else [],
            au_df=result.au_df if ctx.with_sentiment else None,
            bundle=result.bundle,
        )

        # Agent 辩论输出
        self._print_agent_debate(result)

        # 风控输出
        self._print_risk_check(result)

        # 军规输出
        if not ctx.skip_doctrine:
            self._print_doctrine_check(result)
            self._print_doctrine_checklist(result)

        # Munger 模型
        self._print_munger_models(result)

        # 画像匹配
        profile, portfolio, _warnings = self._load_investor_data(result)
        self._print_profile_match(result, profile, portfolio)

        # 经验提醒
        self._load_and_print_experience(result)

        # 仪表盘
        instrument_label = "积存金 Au99.99 (元/克)"
        if result.intl_price > 0:
            instrument_label += f" | 国际 ${result.intl_price:,.0f}/oz"

        result.trade_decision = DashboardFormatter.from_analysis(
            signal_bundle=result.bundle,
            portfolio_decision=result.final_decision,
            instrument=instrument_label,
            current_price=result.current_price,
        )
        print()
        print(DashboardFormatter.format(result.trade_decision))

        # 推送通知
        if not ctx.skip_notification:
            notifier = Notifier()
            if notifier.enabled and result.final_decision.get("position_pct", 0) > 0:
                notifier.send(
                    f"黄金信号: {result.trade_decision.signal.upper()} | "
                    f"仓位{result.trade_decision.position_pct:.0%}"
                )

        logger.info("[8/9] 决策输出完成")

    # ------------------------------------------------------------------
    # Step 9: 自动追踪
    # ------------------------------------------------------------------

    def _step_track(self, ctx: AnalysisContext, result: AnalysisResult) -> None:
        logger.info("[9/9] 自动追踪...")

        # 自动记录预测
        if settings.enable_auto_tracking:
            self._auto_track(result)

        # EventStore 记录
        result.prediction_id = self._record_events(result)

        logger.info("[9/9] 自动追踪完成")

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _print_agent_debate(self, result: AnalysisResult) -> None:
        bull = result.bull_opinion
        bear = result.bear_opinion
        if bull is None or bear is None:
            return

        direction_cn = {"long": "做多", "short": "做空", "neutral": "观望"}
        print(f"\n  🐂 {bull.agent_name} (信心 {bull.confidence:.0%})")
        print(f"     立场: {bull.stance}  建议仓位: {bull.suggested_position_pct:.0%}")
        if bull.arguments:
            print("     论据:")
            for arg in bull.arguments:
                print(f"       ✓  {arg}")
        else:
            print("      (无强看涨信号)")

        print(f"\n  🐻 {bear.agent_name} (信心 {bear.confidence:.0%})")
        print(f"     立场: {bear.stance}  建议仓位: {bear.suggested_position_pct:.0%}")
        if bear.arguments:
            print("     论据:")
            for arg in bear.arguments:
                print(f"       ✗  {arg}")
        else:
            print("      (无强看跌信号)")

        pm_name = "投资经理"
        print(f"\n  🏛️ {pm_name}: {direction_cn.get(result.decision.get('direction', 'neutral'), '观望')} "
              f"| 仓位 {result.decision.get('position_pct', 0):.0%} | {result.decision.get('signal_type', '')}")

    def _print_risk_check(self, result: AnalysisResult) -> None:
        if result.final_decision.get("risk_override"):
            print(f"\n  ⚠️ 风控干预: {result.final_decision['risk_override']}")
        else:
            print(f"\n  ✅ 风控通过 ({len(result.checks)}项检查)")

    def _print_doctrine_check(self, result: AnalysisResult) -> None:
        if result.doctrine_result is None:
            return

        dr = result.doctrine_result
        print(f"\n{'='*60}")
        print("  投资军规审查")
        print(f"{'='*60}")
        print(f"  决策: 方向={result.final_decision.get('direction', '?')} | "
              f"仓位={result.final_decision.get('position_pct', 0):.0%}")
        print(f"  通过: {dr.passed_count}/{len(dr.violations)}")

        if dr.blocks:
            print(f"\n  ■ 阻断 ({len(dr.blocks)}项):")
            for v in dr.blocks:
                print(f"    ✗  {v.rule.name}: {v.message}")

        if dr.warnings:
            print(f"\n  ◆ 警告 ({len(dr.warnings)}项):")
            for v in dr.warnings:
                print(f"    !  {v.rule.name}: {v.message}")

        if dr.infos:
            print(f"\n  ○ 提示 ({len(dr.infos)}项):")
            for v in dr.infos:
                print(f"    i  {v.rule.name}: {v.message}")

        if dr.all_passed:
            print("\n  ✅ 全部军规通过")

        if result.final_decision.get("doctrine_override"):
            print(f"\n  ⚡ 军规调整: {result.final_decision['doctrine_override']}")

    def _load_and_print_experience(self, result: AnalysisResult) -> None:
        """加载并打印相关经验提醒."""
        try:
            loader = ExperienceLoader()
            active_dims = [
                d for d in ["technical", "fundamental", "news", "sentiment"]
                if result.bundle.by_dimension(d)
            ]
            context = {
                "active_dimensions": active_dims,
                "direction": result.final_decision.get("direction", "neutral"),
                "bundle": result.bundle,
                "news_raw": result.news_raw,
            }
            reminders = loader.load_relevant(context, max_items=3)
            result.experience_reminders = reminders
            if reminders:
                print(f"\n{'='*60}")
                print("  📚 经验提醒")
                print(f"{'='*60}")
                for i, r in enumerate(reminders, 1):
                    print(f"  {i}. {r}")
        except Exception as e:
            logger.debug(f"经验提醒加载异常: {e}")
            print(f"     调整后仓位: {result.final_decision.get('position_pct', 0):.0%}")

        print(f"{'='*60}")

    def _load_investor_data(
        self, result: AnalysisResult
    ) -> tuple[str, dict[str, Any], list[str]]:
        """加载投资者画像与持仓，缺失时回退到 example 文件."""
        store = get_store()
        warnings: list[str] = []

        profile = store.load_investor_profile()
        if not profile:
            example_path = _PROJECT_DATA_DIR / "investor_profile.example.md"
            if example_path.exists():
                profile = example_path.read_text(encoding="utf-8")
                warnings.append("使用示例投资者画像，请填写 data/private/investor_profile.md")
        result.investor_profile = profile

        portfolio = store.load_portfolio()
        if not portfolio:
            example_portfolio_path = _PROJECT_DATA_DIR / "portfolio.example.yaml"
            if example_portfolio_path.exists():
                try:
                    portfolio = yaml.safe_load(
                        example_portfolio_path.read_text(encoding="utf-8")
                    ) or {}
                    warnings.append("使用示例持仓数据，请填写 data/private/portfolio.yaml")
                except yaml.YAMLError:
                    portfolio = {}
        result.portfolio = portfolio

        return profile, portfolio, warnings

    def _select_munger_models(
        self, bundle: SignalBundle, count: int = 3
    ) -> list[MungerModel]:
        """根据信号 bundle 关键词从 GOLD_MODELS 中选择最相关模型."""
        if not GOLD_MODELS:
            return []

        # 无信号时直接返回通用经典模型
        if not bundle.signals:
            return self._fallback_classic_models(count)

        keywords: set[str] = set()
        for sig in bundle.signals:
            keywords.update(self._extract_keywords(sig.dimension))
            keywords.update(self._extract_keywords(sig.name))
            keywords.update(self._extract_keywords(sig.direction.value))
            if sig.dimension == "sentiment":
                keywords.update(["情绪", "恐惧", "贪婪", "极端", "心理"])
            elif sig.dimension == "technical":
                keywords.update(["技术", "趋势", "反转", "突破", "波动"])
            elif sig.dimension == "fundamental":
                keywords.update(["基本面", "利率", "通胀", "美元", "央行"])
            elif sig.dimension == "news":
                keywords.update(["消息", "新闻", "媒体", "舆论", "信息"])

        # 通用兜底关键词
        keywords.update(["黄金", "投资", "风险", "不确定"])

        def score(model: MungerModel) -> int:
            text = " ".join(
                [
                    model.name_cn,
                    model.name_en,
                    model.description,
                    model.gold_relevance_reason,
                    model.discipline,
                ]
            ).lower()
            return sum(1 for kw in keywords if kw in text)

        scored = [(m, score(m)) for m in GOLD_MODELS]
        scored.sort(key=lambda x: x[1], reverse=True)
        selected = [m for m, s in scored if s > 0][:count]

        # 兜底：安全边际、市场先生、能力圈（优先完全匹配）
        if len(selected) < count:
            selected.extend(self._fallback_classic_models(count - len(selected)))

        return selected[:count]

    def _fallback_classic_models(self, count: int) -> list[MungerModel]:
        """返回 Munger 通用经典模型作为兜底."""
        fallback_names = ["安全边际", "市场先生", "能力圈"]
        selected: list[MungerModel] = []
        # 优先完全匹配
        for name in fallback_names:
            exact = next((m for m in GOLD_MODELS if m.name_cn == name), None)
            if exact and exact not in selected:
                selected.append(exact)
            if len(selected) >= count:
                return selected[:count]
        # 完全匹配不足时再用子串匹配补齐
        for name in fallback_names:
            for m in GOLD_MODELS:
                if m.name_cn != name and name in m.name_cn and m not in selected:
                    selected.append(m)
                    if len(selected) >= count:
                        return selected[:count]
        return selected[:count]

    @staticmethod
    def _extract_keywords(text: str) -> set[str]:
        """从信号文本中提取关键词，支持中英文分词."""
        keywords: set[str] = set()
        text_lower = text.lower()
        keywords.add(text_lower)

        # 拆分英文单词、数字、连续中文字符
        tokens = _KEYWORD_RE.findall(text_lower)
        for token in tokens:
            keywords.add(token)
            # 对长中文词生成 2-gram，提高命中概率
            if len(token) >= 4 and all("一" <= c <= "鿿" for c in token):
                for i in range(len(token) - 1):
                    keywords.add(token[i : i + 2])
        return keywords

    def _print_doctrine_checklist(self, result: AnalysisResult) -> None:
        """逐条输出 r001-r030 军规自查清单."""
        print(f"\n{'='*60}")
        print("  投资军规自查 (r001-r030)")
        print(f"{'='*60}")

        doctrine = result.doctrine_result
        if not doctrine:
            print("  军规检查未执行")
            return

        violations = {v.rule.id: v for v in doctrine.violations}
        checked_count = 0
        for rule in ALL_RULES:
            if not rule.enabled:
                print(f"  ⏸️  {rule.id} {rule.name}: 已禁用")
                continue
            checked_count += 1
            v = violations.get(rule.id)
            if v is None or v.passed:
                icon = "✅"
            elif rule.severity == "block":
                icon = "❌"
            else:
                icon = "⚠️"
            print(f"  {icon} {rule.id} {rule.name}: {rule.description[:40]}")

        print(f"\n  通过: {doctrine.passed_count}/{checked_count}")

    def _print_munger_models(self, result: AnalysisResult) -> None:
        """输出与当前决策最相关的 Munger 思维模型 (从 result 读取)."""
        models = result.munger_models
        if not models:
            return

        print(f"\n{'='*60}")
        print("  Munger 思维模型 (Step 7)")
        print(f"{'='*60}")

        for m in models:
            reason = f" | {m['gold_relevance_reason']}" if m.get("gold_relevance_reason") else ""
            print(f"  • {m['name_cn']} / {m['name_en']}{reason}")
            adj = m.get("adjustment")
            if adj:
                print(f"    ⚙️ 仓位约束: ×{adj['factor']:.0%} — {adj['reason']}")
            else:
                print(f"    📖 {m['description'][:80]}...")
            print()

    def _print_profile_match(
        self, result: AnalysisResult, profile: str, portfolio: dict[str, Any]
    ) -> None:
        """输出投资者画像与建议仓位的匹配情况."""
        print(f"\n{'='*60}")
        print("  画像匹配")
        print(f"{'='*60}")

        if not portfolio:
            print("  未找到投资者持仓数据")
            return

        limits = portfolio.get("limits", {})
        total_funds = float(limits.get("total_funds", 200_000))
        max_single_pct = float(limits.get("max_single_pct", 20)) / 100
        max_gold_pct = float(limits.get("max_gold_pct", 80)) / 100
        risk_profile = limits.get("risk_profile", "balanced")
        investment_horizon = limits.get("investment_horizon", "1-3年")

        current_price = result.current_price
        positions = portfolio.get("positions", {})
        current_gold_value = sum(
            float(pos.get("grams", 0)) * current_price
            for pos in positions.values()
        )
        current_gold_pct = current_gold_value / total_funds if total_funds else 0.0

        suggested_pct = result.final_decision.get("position_pct", 0)
        new_total_gold_pct = current_gold_pct + suggested_pct

        print(f"  风险画像: {risk_profile}")
        print(f"  持仓周期: {investment_horizon}")
        print(f"  当前黄金占比: {current_gold_pct:.0%} (上限 {max_gold_pct:.0%})")

        single_ok = suggested_pct <= max_single_pct
        total_ok = new_total_gold_pct <= max_gold_pct

        print(
            f"  建议仓位: {suggested_pct:.0%} vs 单品种上限 {max_single_pct:.0%} "
            f"— {'兼容 ✅' if single_ok else '超出 ⚠️'}"
        )
        print(
            f"  总敞口: {current_gold_pct:.0%}+{suggested_pct:.0%}="
            f"{new_total_gold_pct:.0%} vs 上限 {max_gold_pct:.0%} "
            f"— {'兼容 ✅' if total_ok else '超出 ⚠️'}"
        )

        if single_ok and total_ok:
            print("\n  综合: 建议符合画像约束 ✅")
        else:
            print("\n  综合: 建议部分超出画像约束 ⚠️")

    def _auto_track(self, result: AnalysisResult) -> None:
        """自动记录预测到预测追踪器."""
        dim_scores: dict[str, float] = {}
        for dim in ["technical", "fundamental", "news", "sentiment"]:
            signals = result.bundle.by_dimension(dim)
            if signals:
                dim_scores[dim] = round(sum(s.score for s in signals) / len(signals), 2)
            else:
                dim_scores[dim] = 0.0

        serialized_signals: list[dict[str, Any]] = []
        for s in result.bundle.signals:
            sd = asdict(s)
            sd["timestamp"] = sd["timestamp"].isoformat()
            serialized_signals.append(sd)

        record = PredictionRecord(
            id=uuid.uuid4().hex[:12],
            timestamp=datetime.now(),
            current_price=result.current_price,
            signals=serialized_signals,
            composite_score=result.bundle.composite_score,
            confidence=result.bundle.confidence,
            direction=result.final_decision.get("direction", "neutral"),
            position_pct=result.final_decision.get("position_pct", 0),
            dimension_scores=dim_scores,
        )
        PredictionTracker().record_prediction(record)

    def _record_events(self, result: AnalysisResult) -> str:
        """向 EventStore 写入 prediction_made + evidence_attached."""
        prediction_id = uuid.uuid4().hex[:12]
        store = EventStore()
        direction = result.final_decision.get("direction", "neutral")

        # prediction_made
        store.append(
            EventType.PREDICTION_MADE,
            prediction_id,
            {
                "direction": direction,
                "composite_score": round(result.bundle.composite_score, 4),
                "confidence": round(result.bundle.confidence, 4),
                "position_pct": result.final_decision.get("position_pct", 0),
                "horizon_days": 7,
                "source": "scan",
                "auto_resolve": True,
                "current_price": round(result.current_price, 2),
            },
        )

        # 提取价格快照
        dxy_val = float(result.dxy_df["value"].iloc[-1]) if not result.dxy_df.empty else None
        silver_val = float(result.silver_df["value"].iloc[-1]) if not result.silver_df.empty else None
        rate_val = float(result.rate_df["value"].iloc[-1]) if not result.rate_df.empty else None
        breakeven_val = float(result.breakeven_df["value"].iloc[-1]) if not result.breakeven_df.empty else None

        gsr: float | None = None
        if result.current_price > 0 and silver_val and silver_val > 0:
            gsr = round(result.current_price / silver_val, 1)

        dim_scores: dict[str, float] = {}
        for dim in ["technical", "fundamental", "news", "sentiment"]:
            signals = result.bundle.by_dimension(dim)
            if signals:
                dim_scores[dim] = round(sum(s.score for s in signals) / len(signals), 2)
            else:
                dim_scores[dim] = 0.0

        serialized_signals: list[dict[str, Any]] = []
        for s in result.bundle.signals:
            sd = asdict(s)
            sd["timestamp"] = sd["timestamp"].isoformat()
            serialized_signals.append(sd)

        from gold_miner.events.models import EvidenceSnapshot
        snapshot = EvidenceSnapshot.from_price_data(
            prediction_id=prediction_id,
            spot_gold=round(result.current_price, 2),
            dxy=round(dxy_val, 2) if dxy_val else None,
            silver=round(silver_val, 2) if silver_val else None,
            real_rate=round(rate_val, 2) if rate_val else None,
            breakeven=round(breakeven_val, 2) if breakeven_val else None,
            gold_silver_ratio=gsr,
            signals=serialized_signals,
            dimension_scores=dim_scores,
            composite_score=round(result.bundle.composite_score, 4),
            confidence=round(result.bundle.confidence, 4),
            source_type="scan",
        )
        store.append(
            EventType.EVIDENCE_ATTACHED,
            prediction_id,
            {"snapshot": snapshot},
        )

        logger.debug(f"EventStore 已记录: {prediction_id[:8]}... (scan, {direction})")
        return prediction_id

    # ------------------------------------------------------------------
    # 配置控制
    # ------------------------------------------------------------------

    def enable(self, step_name: str) -> None:
        """启用指定步骤 (当前仅用于文档/测试)."""
        logger.debug(f"启用步骤: {step_name}")

    def disable(self, step_name: str) -> None:
        """禁用指定步骤 (当前仅用于文档/测试)."""
        logger.debug(f"禁用步骤: {step_name}")
