"""完整分析管线 — 封装 run_scan() 硬编码流程为可配置、可测试的类."""

from __future__ import annotations

import re
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from loguru import logger

from gold_miner.advisor.monitor_evaluator import MonitorContext, MonitorEvaluator
from gold_miner.config import settings
from gold_miner.data.jd_accumulation_gold import JdAccumulationGoldFetcher, JdGoldPrice
from gold_miner.data.macro import MacroDataFetcher
from gold_miner.data.news import NewsFetcher, NewsItem
from gold_miner.data.sentiment import SentimentDataFetcher
from gold_miner.data.spot_gold import SpotGoldFetcher
from gold_miner.decision.agents import AgentOpinion, BearAgent, BullAgent, PortfolioManager
from gold_miner.decision.institutional_flow import (
    apply_institutional_outflow_gate,
    assess_institutional_flow,
    signals_indicate_etf_flow_available,
    signals_indicate_institutional_selling,
)
from gold_miner.decision.position_state import resolve_position_state
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
from gold_miner.signals.cot_signal import CotSignalGenerator
from gold_miner.signals.economic_calendar import EconomicCalendarSignalGenerator
from gold_miner.signals.engine import ScoringEngine
from gold_miner.signals.etf_flow_signal import EtfFlowSignalGenerator
from gold_miner.signals.fundamental import FundamentalAnalyzer
from gold_miner.signals.institutional_signal import InstitutionalSignalGenerator
from gold_miner.signals.monitor_signal import MonitorSignalGenerator
from gold_miner.signals.news_signal import NewsSignalGenerator
from gold_miner.signals.recent_events import RecentEventSignalGenerator
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
    institutional_flow: dict[str, Any] = field(default_factory=dict)
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

        # Step 3+4: source_truth + agent_debate 并行 (都只读 bundle,写不同字段)
        self._step_source_truth_and_debate(ctx, result)

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
        """Step 1: 数据采集 — 7 路并行获取 (gold/intl/minsheng/dxy/rate/silver/breakeven)."""
        logger.info("[1/9] 数据采集 (并行)...")

        # 独立 fetcher 实例确保线程安全
        gold_fetcher = SpotGoldFetcher()
        intl_fetcher = SpotGoldFetcher()
        dxy_fetcher = MacroDataFetcher()
        rate_fetcher = MacroDataFetcher()
        silver_fetcher = MacroDataFetcher()
        be_fetcher = MacroDataFetcher()

        with ThreadPoolExecutor(max_workers=7) as pool:
            futures: dict[Future, str] = {
                pool.submit(gold_fetcher.fetch, days=ctx.days): "gold",
                pool.submit(intl_fetcher.fetch_international_quote): "intl",
                pool.submit(self._fetch_minsheng_accumulation_price): "minsheng",
                pool.submit(dxy_fetcher.fetch_dxy): "dxy",
                pool.submit(rate_fetcher.fetch_real_rate): "rate",
                pool.submit(silver_fetcher.fetch_silver): "silver",
                pool.submit(be_fetcher.fetch_breakeven): "breakeven",
            }

            raw: dict[str, Any] = {}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    raw[key] = future.result()
                except Exception as e:
                    logger.debug(f"[collect] {key} 获取失败: {e}")
                    raw[key] = None

        # --- gold_df (必须成功) ---
        gold_df = raw.get("gold")
        if gold_df is None or gold_df.empty:
            logger.error("现货黄金数据获取失败")
            return
        result.gold_df = gold_df
        result.current_price = gold_df["close"].iloc[-1]
        logger.info(f"国内金价 Au99.99: {result.current_price:.2f} 元/克 (来源: SGE/jinjia)")

        # --- 国际金价 ---
        try:
            intl_quote = raw.get("intl")
            if intl_quote and isinstance(intl_quote, list) and intl_quote[0].get("price"):
                result.intl_price = intl_quote[0]["price"]
                logger.info(
                    f"国际金价 XAU/USD: {result.intl_price:.2f} 美元/盎司 "
                    f"({intl_quote[0].get('name', '伦敦金')})"
                )
        except Exception as e:
            logger.debug(f"国际金价解析失败: {e}")

        # --- 积存金 ---
        ms_price = raw.get("minsheng")
        if ms_price:
            result.minsheng_accumulation_price = ms_price.price
            result.minsheng_accumulation_change_pct = ms_price.change_pct
            logger.info(
                f"民生银行积存金: {result.minsheng_accumulation_price:.2f} 元/克 "
                f"({result.minsheng_accumulation_change_pct})"
            )

        # --- 宏观数据 ---
        result.dxy_df = raw.get("dxy") if raw.get("dxy") is not None else pd.DataFrame()
        result.rate_df = raw.get("rate") if raw.get("rate") is not None else pd.DataFrame()
        result.silver_df = raw.get("silver") if raw.get("silver") is not None else pd.DataFrame()
        result.breakeven_df = raw.get("breakeven") if raw.get("breakeven") is not None else pd.DataFrame()

        if not result.rate_df.empty:
            logger.info(f"实际利率最新: {result.rate_df['value'].iloc[-1]:.2f}%")
        if not result.breakeven_df.empty:
            logger.info(f"通胀预期最新: {result.breakeven_df['value'].iloc[-1]:.2f}%")
        if not result.silver_df.empty:
            logger.info(f"白银最新价: {result.silver_df['value'].iloc[-1]:.2f}")

        # 价格预警 (可选)
        if not ctx.skip_alerts:
            try:
                alert_mgr = PriceAlert()
                silver_price = (
                    result.silver_df["value"].iloc[-1]
                    if not result.silver_df.empty else None
                )
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

    @staticmethod
    def _portfolio_gold_pct(portfolio: dict[str, Any] | None, current_price: float) -> float:
        """当前黄金市值 / total_funds."""
        if not portfolio or current_price <= 0:
            return 0.0
        limits = portfolio.get("limits") or {}
        total_funds = float(limits.get("total_funds") or 0)
        if total_funds <= 0:
            return 0.0
        grams = 0.0
        for pos in (portfolio.get("positions") or {}).values():
            if isinstance(pos, dict):
                grams += float(pos.get("grams") or 0)
        return (grams * current_price) / total_funds

    @staticmethod
    def _has_active_stop_order() -> bool:
        """是否存在 active 的 OCO/止损类条件单."""
        path = _PROJECT_DATA_DIR / "private" / "conditional_orders.jsonl"
        if not path.exists():
            return False
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                import json as _json
                row = _json.loads(line)
                if row.get("status") != "active":
                    continue
                if row.get("type") == "oco" or row.get("direction") in ("卖出", "sell"):
                    return True
                oco = row.get("oco") or {}
                if oco.get("stop_loss"):
                    return True
        except Exception as e:
            logger.debug(f"读取条件单失败: {e}")
        return False

    @staticmethod
    def _near_high_impact_event(days: int = 2) -> bool:
        """未来 days 天内是否有中高影响宏观事件（FOMC/CPI/PCE/NFP 等）."""
        try:
            from gold_miner.data.calendar import EventCalendar, EventImpact

            cal = EventCalendar()
            upcoming = cal.get_upcoming(days=days, min_impact=EventImpact.MEDIUM)
            high_types = {
                "fed_rate", "cpi", "pce", "nfp", "fomc", "ppi",
            }
            for e in upcoming:
                et = getattr(e.event_type, "value", str(e.event_type)).lower()
                if et in high_types or e.impact in (EventImpact.HIGH, EventImpact.CRITICAL):
                    return True
                name = (e.name or "").upper()
                if any(k in name for k in ("FOMC", "CPI", "PCE", "非农", "利率决议")):
                    return True
        except Exception as e:
            logger.debug(f"near_data_event 检查失败: {e}")
        return False

    # ------------------------------------------------------------------
    # Step 2: 信号生成
    # ------------------------------------------------------------------

    def _step_generate_signals(self, ctx: AnalysisContext, result: AnalysisResult) -> None:
        """Step 2: 信号生成 — 独立维度并行采集,分两批执行."""
        logger.info("[2/9] 信号生成 (并行)...")

        bundle = SignalBundle()

        # ---- 先评估 active monitor 触发条件，关闭已触发的 ----
        self._evaluate_active_monitors(result)

        # ---- Batch 1: 所有独立信号生成器并行 ----
        news_signals: list[Signal] = []

        with ThreadPoolExecutor(max_workers=12) as pool:
            futures: dict[Future, str] = {}

            # 技术面
            futures[pool.submit(
                lambda: TechnicalAnalyzer(result.gold_df).generate_signals()
            )] = "technical"

            # 基本面
            futures[pool.submit(
                lambda: FundamentalAnalyzer(
                    gold_df=result.gold_df,
                    dxy_df=result.dxy_df,
                    rate_df=result.rate_df,
                    silver_df=result.silver_df,
                    breakeven_df=result.breakeven_df,
                ).generate_signals()
            )] = "fundamental"

            # 消息面
            if ctx.with_news:
                futures[pool.submit(
                    lambda: NewsSignalGenerator().fetch_and_analyze(hours=24)
                )] = "news"

            # 情绪面
            if ctx.with_sentiment:
                futures[pool.submit(self._fetch_and_generate_sentiment)] = "sentiment"

            # ETF 资金流
            futures[pool.submit(
                lambda: EtfFlowSignalGenerator().generate_signals()
            )] = "etf"

            # COT 聪明钱（强制接入 scan）
            futures[pool.submit(
                lambda: CotSignalGenerator().generate_signals()
            )] = "cot"

            # 聪明钱合成：13F / 投行 / COMEX 大户 / 综合（强制接入 scan）
            spot_for_inst = float(result.current_price or 0) or 3300.0
            # InstitutionalSignal 用美元现货更合理；无国际价时用国内价兜底
            try:
                if result.intl_price and float(result.intl_price) > 0:
                    spot_for_inst = float(result.intl_price)
            except Exception:
                pass
            futures[pool.submit(
                lambda s=spot_for_inst: InstitutionalSignalGenerator(
                    current_spot=s
                ).generate_signals()
            )] = "smart_money"

            # 经济日历
            futures[pool.submit(
                lambda: EconomicCalendarSignalGenerator().generate_signals()
            )] = "economic_calendar"

            # 事件结果驱动
            futures[pool.submit(self._generate_event_driven_signals)] = "event_driven"

            # 近期事件时效性加权
            futures[pool.submit(
                lambda: RecentEventSignalGenerator().generate_signals()
            )] = "recent_events"

            # Monitor 触发结果
            futures[pool.submit(
                lambda: MonitorSignalGenerator().generate_signals()
            )] = "monitor"

            # ---- 收集 Batch 1 结果 ----
            batch_results: dict[str, list[Signal]] = {}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    sigs = future.result()
                    if key == "sentiment":
                        # sentiment future returns (signals, au_df) tuple
                        sigs, au_df = sigs if isinstance(sigs, tuple) else (sigs, None)
                        if au_df is not None:
                            result.au_df = au_df
                    batch_results[key] = sigs or []
                    logger.debug(f"[2/9] {key}: {len(batch_results[key])} 个信号")
                except Exception as e:
                    logger.warning(f"[2/9] {key} 信号生成异常: {e}")
                    batch_results[key] = []

        # ---- 注入所有 Batch 1 信号 ----
        for key, sigs in batch_results.items():
            for sig in sigs:
                bundle.add(sig)
            if key != "sentiment":  # sentiment already logged in the worker
                logger.info(f"{key}信号: {len(sigs)} 个")

        # 消息面信号单独记录 (用于 DeepSeek)
        news_signals = batch_results.get("news", [])
        logger.info(f"消息面信号: {len(news_signals)} 个")

        # 情绪面日志
        sentiment_sigs = batch_results.get("sentiment", [])
        logger.info(f"情绪面信号: {len(sentiment_sigs)} 个")

        # ---- 消息面补充: 新闻原文抓取 (轻量,主线程) ----
        result.news_raw = []
        if ctx.with_news:
            try:
                nf = NewsFetcher()
                result.news_raw = nf.fetch_latest(max_results=6)
                result.news_raw = nf.analyze_sentiment(result.news_raw)
            except Exception:
                pass

        # ---- Batch 2: DeepSeek 深度分析 (依赖 news_signals + composite_score) ----
        if ctx.deep and news_signals:
            try:
                logger.info("[DeepSeek] 深度分析新闻...")
                llm = LLMClient()
                news_text = "\n".join(
                    f"- [{s.metadata.get('source', '?')}] {s.description}"
                    for s in news_signals
                )[:3000]
                # 基于已收集信号计算初步方向
                bullish = sum(1 for s in bundle.signals if s.direction == SignalDirection.BULLISH)
                bearish = sum(1 for s in bundle.signals if s.direction == SignalDirection.BEARISH)
                pre_score = (bullish - bearish) / max(len(bundle.signals), 1)
                llm_result = llm.analyze_article(
                    text=news_text,
                    rule_sentiment=(
                        "bullish" if pre_score > 0.1
                        else "bearish" if pre_score < -0.1
                        else "neutral"
                    ),
                    rule_score=pre_score,
                )
                if llm_result and not llm_result.get("parse_error"):
                    direction = llm_result.get("sentiment", "neutral")
                    conf = llm_result.get("confidence", 0.5)
                    score_impact = conf if direction == "bullish" else -conf
                    bundle.add(Signal(
                        name="DeepSeek 新闻深度分析",
                        dimension="news",
                        direction=(
                            SignalDirection.BULLISH if direction == "bullish"
                            else SignalDirection.BEARISH if direction == "bearish"
                            else SignalDirection.NEUTRAL
                        ),
                        strength=SignalStrength.MODERATE if conf > 0.6 else SignalStrength.WEAK,
                        score=round(score_impact, 2),
                        description=llm_result.get("reasoning", "")[:150],
                    ))
                    logger.info(f"DeepSeek 分析完成: {direction} (置信度 {conf:.0%})")
            except Exception as e:
                logger.warning(f"DeepSeek分析异常: {e}")

        # ---- 打分 ----
        engine = ScoringEngine()
        engine.score(bundle)
        logger.info(f"综合评分: {bundle.composite_score:+.2f} | 置信度: {bundle.confidence:.0%}")

        result.bundle = bundle
        logger.info("[2/9] 信号生成完成")

    # ---- 信号生成辅助方法 (线程池中执行) ----

    @staticmethod
    def _generate_event_driven_signals() -> list[Signal]:
        """事件结果驱动信号 (线程安全)."""
        from gold_miner.signals.event_driven import EventDrivenSignalGenerator

        return EventDrivenSignalGenerator().generate_post_event_signals_from_calendar(
            lookback_days=7,
        )

    @staticmethod
    def _fetch_and_generate_sentiment() -> tuple[list[Signal], pd.DataFrame | None]:
        """情绪面: 获取 AU 期货数据 + 生成信号 (线程安全)."""
        try:
            sentiment_fetcher = SentimentDataFetcher()
            au_df = sentiment_fetcher.fetch_au_futures(lookback=60)
            analyzer = SentimentAnalyzer(au_df=au_df)
            return analyzer.generate_signals(), au_df
        except Exception as e:
            logger.warning(f"情绪面数据获取异常，跳过: {e}")
            return [], None

    def _evaluate_active_monitors(self, result: AnalysisResult) -> None:
        """评估 active monitor 触发条件，已触发则关闭."""
        try:
            from gold_miner.data.calendar import EventCalendar

            cal = EventCalendar()
            active = cal.get_active_monitors()
            if not active:
                return

            mctx = MonitorContext(
                gold_price=result.current_price,
                minsheng_price=result.minsheng_accumulation_price,
                xauusd=result.intl_price,
            )
            evaluator = MonitorEvaluator(calendar=cal)
            closed = evaluator.evaluate_and_close(active, mctx)

            if closed:
                logger.info(
                    f"[MonitorEvaluator] {len(closed)} 个 monitor 触发: "
                    f"{', '.join(m.name for m, _ in closed)}"
                )
            else:
                logger.info("[MonitorEvaluator] 无 monitor 触发")
        except Exception as e:
            logger.debug(f"active monitor 评估异常: {e}")

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
            long_only=True,
        )
        # 透传 bundle 置信度，供持仓状态机弱信号判定
        result.decision["confidence"] = result.bundle.confidence

        logger.info("[4/9] Agent 辩论完成")

    # ------------------------------------------------------------------
    # Step 3+4: 来源验证 + Agent 辩论 (并行)
    # ------------------------------------------------------------------

    def _step_source_truth_and_debate(
        self, ctx: AnalysisContext, result: AnalysisResult
    ) -> None:
        """并行执行来源验证和 Agent 辩论 — 二者只读 bundle,写不同字段."""
        logger.info("[3+4/9] 来源验证 + Agent 辩论 (并行)...")

        with ThreadPoolExecutor(max_workers=2) as pool:
            f_source = pool.submit(self._step_source_truth, ctx, result)
            f_debate = pool.submit(self._step_agent_debate, ctx, result)

            # 等待双方完成
            try:
                f_source.result()
            except Exception as e:
                logger.warning(f"来源验证异常: {e}")

            try:
                f_debate.result()
            except Exception as e:
                logger.warning(f"Agent 辩论异常: {e}")

    # ------------------------------------------------------------------
    # Step 5: 风控审查
    # ------------------------------------------------------------------

    def _step_risk_check(self, ctx: AnalysisContext, result: AnalysisResult) -> None:
        logger.info("[5/9] 风控审查...")

        # 尽早加载持仓，供集中度检查与后续状态机
        if not result.portfolio:
            _, portfolio, _ = self._load_investor_data(result)
        else:
            portfolio = result.portfolio

        current_gold_pct = self._portfolio_gold_pct(portfolio, result.current_price)

        risk_mgr = RiskManager(max_position_pct=settings.max_position_pct)
        result.checks = risk_mgr.check(
            result.decision,
            current_position_pct=current_gold_pct,
        )
        result.final_decision = risk_mgr.apply_risk_controls(result.decision, result.checks)

        # 持仓状态机：空仓开新仓 → hold/add/reduce/stop/stand_aside
        pos_state = resolve_position_state(
            portfolio or {},
            result.current_price,
            {
                **result.final_decision,
                "confidence": result.bundle.confidence,
                "composite_score": result.bundle.composite_score,
            },
            long_only=True,
        )
        result.final_decision["action"] = pos_state["action"]
        result.final_decision["action_cn"] = pos_state["action_cn"]
        result.final_decision["position_state"] = pos_state
        result.final_decision["unrealized_pnl_pct"] = pos_state["unrealized_pnl_pct"]
        result.final_decision["current_gold_pct"] = pos_state["current_gold_pct"]
        result.final_decision["target_gold_pct"] = pos_state["target_gold_pct"]
        # 用户可见方向：仅做多；动作优先
        result.final_decision["direction"] = pos_state["direction"]
        result.final_decision["position_pct"] = pos_state["position_pct"]
        result.final_decision["signal_type"] = pos_state["signal_type"]
        if pos_state["action"] in ("hold", "stand_aside"):
            # 禁止再展示“买入/做多开仓”
            result.final_decision["direction"] = (
                "long" if pos_state["action"] == "hold" and pos_state["grams"] > 0 else "neutral"
            )
            result.final_decision["position_pct"] = 0.0

        # 机构净流出 → 禁止加仓闸门（持有/减仓不受强制）
        pre_gate_action = str(result.final_decision.get("action") or "")
        flow = assess_institutional_flow(result.bundle.signals)
        result.institutional_flow = flow.to_dict()
        result.final_decision = apply_institutional_outflow_gate(
            result.final_decision, flow
        )
        post_gate_action = str(result.final_decision.get("action") or "")
        action_changed = post_gate_action != pre_gate_action

        if flow.block_add and (pre_gate_action == "add" or action_changed):
            result.checks.append(RiskCheck(
                name="机构净流出禁加仓",
                passed=False,
                message="；".join(flow.reasons) or "机构资金净流出",
                severity="block",
            ))
            logger.warning(
                f"机构资金闸门触发: status={flow.status} net={flow.net_score:+.2f} "
                f"action {pre_gate_action}→{post_gate_action} | "
                f"{'; '.join(flow.reasons)}"
            )
        elif flow.block_add:
            result.checks.append(RiskCheck(
                name="机构净流出禁加仓",
                passed=True,
                message=(
                    f"机构净流出(net={flow.net_score:+.2f})，当前无加仓动作；"
                    f"若后续加仓将被拦截"
                ),
                severity="warn",
            ))
            logger.info(
                f"机构资金净流出待命: status={flow.status} net={flow.net_score:+.2f} "
                f"(当前动作={post_gate_action})"
            )
        else:
            result.checks.append(RiskCheck(
                name="机构净流出禁加仓",
                passed=True,
                message=(
                    f"机构流={flow.status} net={flow.net_score:+.2f}"
                    if flow.has_real_data
                    else "机构流数据不足，闸门未触发（不禁止）"
                ),
                severity="info",
            ))
            logger.info(
                f"机构资金评估: status={flow.status} net={flow.net_score:+.2f} "
                f"block_add={flow.block_add} conf={flow.confidence:.0%}"
            )

        logger.info(
            f"持仓状态: {result.final_decision.get('action_cn', pos_state['action_cn'])} | "
            f"浮盈亏 {pos_state['unrealized_pnl_pct']:+.1%} | "
            f"现仓 {pos_state['current_gold_pct']:.0%}"
        )

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
        pos_state = result.final_decision.get("position_state") or {}
        current_gold_pct = float(
            result.final_decision.get("current_gold_pct")
            or pos_state.get("current_gold_pct")
            or 0.0
        )
        unrealized = float(
            result.final_decision.get("unrealized_pnl_pct")
            or pos_state.get("unrealized_pnl_pct")
            or 0.0
        )
        # 条件单止损：读 private conditional_orders；否则用 portfolio secondary/hard stop
        stop_loss_set = self._has_active_stop_order() or bool(
            pos_state.get("avg_cost") and (pos_state.get("near_hard_stop") is not None)
        )
        # portfolio.yaml 有 hard_stop/secondary_stop 即视为已设止损规则
        if result.portfolio:
            for pos in (result.portfolio.get("positions") or {}).values():
                if isinstance(pos, dict) and (
                    pos.get("hard_stop") is not None or pos.get("secondary_stop") is not None
                ):
                    stop_loss_set = True
                    break

        near_data = self._near_high_impact_event(days=2)
        action = result.final_decision.get("action", "")
        # 军规仓位：加仓用建议增量；持有用现仓；减仓用现仓
        if action == "add":
            exposure_for_rules = result.final_decision.get("position_pct", 0)
        else:
            exposure_for_rules = current_gold_pct

        flow_info = result.institutional_flow or result.final_decision.get("institutional_flow") or {}
        inst_selling = bool(flow_info.get("status") == "outflow" and flow_info.get("has_real_data"))
        if not flow_info and result.bundle.signals:
            # 兜底：从信号再评估一次
            _flow = assess_institutional_flow(result.bundle.signals)
            flow_info = _flow.to_dict()
            inst_selling = signals_indicate_institutional_selling(_flow)

        result.doctrine_ctx = {
            "current_exposure": current_gold_pct,
            "gold_allocation_pct": current_gold_pct,
            "daily_change_pct": (
                abs(result.gold_df["close"].iloc[-1] / result.gold_df["close"].iloc[-2] - 1) * 100
                if len(result.gold_df) >= 2 else 0
            ),
            "near_data_event": near_data,
            "consecutive_stops": 0,  # 尚无自动统计 trade_log；保留字段
            "vix": 0,
            "fear_greed_index": 50,
            # 小数形式：-0.028 = -2.8%（与 checker 中 0.20/-0.10 阈值一致）
            "unrealized_pnl_pct": unrealized if abs(unrealized) <= 2 else unrealized / 100.0,
            "has_trailing_stop": stop_loss_set,
            "bullish_signal_count": result.bundle.bullish_count(),
            "bearish_signal_count": result.bundle.bearish_count(),
            "active_dimensions": active_dims,
            "bull_confidence": result.decision.get("bull_confidence", 0),
            "bear_confidence": result.decision.get("bear_confidence", 0),
            "stop_loss_set": stop_loss_set,
            "has_decision_record": True,
            "proposed_new_position_pct": exposure_for_rules,
            # 机构资金流 → 军规 r020/r021/r024
            "etf_flow_available": signals_indicate_etf_flow_available(result.bundle.signals),
            "institutional_selling": inst_selling,
            "retail_buying": action == "add",  # 建议加仓时视为散户买意
            "institutional_flow_status": flow_info.get("status", "unknown"),
            "institutional_flow_score": flow_info.get("net_score", 0.0),
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
        raw_dir = direction_cn.get(result.decision.get("direction", "neutral"), "观望")
        final = result.final_decision or {}
        action_cn = final.get("action_cn") or ""
        print(
            f"\n  🏛️ {pm_name}: 原始 {raw_dir} | 仓位 {result.decision.get('position_pct', 0):.0%} "
            f"| {result.decision.get('signal_type', '')}"
        )
        if action_cn:
            pos = final.get("position_state") or {}
            print(
                f"     → 持仓动作: **{action_cn}** | 执行方向={direction_cn.get(final.get('direction'), final.get('direction'))} "
                f"| 建议变动仓位 {final.get('position_pct', 0):.0%} | "
                f"浮盈亏 {float(pos.get('unrealized_pnl_pct') or final.get('unrealized_pnl_pct') or 0):+.1%}"
            )
            if final.get("position_state", {}).get("reason"):
                print(f"     原因: {final['position_state']['reason']}")

    def _print_risk_check(self, result: AnalysisResult) -> None:
        if result.final_decision.get("risk_override"):
            print(f"\n  ⚠️ 风控干预: {result.final_decision['risk_override']}")
        else:
            print(f"\n  ✅ 风控通过 ({len(result.checks)}项检查)")

        flow = result.institutional_flow or result.final_decision.get("institutional_flow") or {}
        if flow:
            status = flow.get("status", "unknown")
            net = flow.get("net_score", 0.0)
            block = flow.get("block_add", False)
            flag = "🚫 禁止加仓" if block else "✅ 允许加仓评估"
            print(f"\n  🏦 机构资金流: {status} | net={net:+.2f} | {flag}")
            for r in (flow.get("reasons") or [])[:3]:
                print(f"     · {r}")
            if result.final_decision.get("institutional_gate"):
                print(f"     → {result.final_decision['institutional_gate']}")

    def _print_doctrine_check(self, result: AnalysisResult) -> None:
        if result.doctrine_result is None:
            return

        dr = result.doctrine_result
        print(f"\n{'='*60}")
        print("  投资军规审查")
        print(f"{'='*60}")
        action_cn = result.final_decision.get("action_cn", "")
        print(
            f"  决策: 动作={action_cn or result.final_decision.get('action', '?')} | "
            f"方向={result.final_decision.get('direction', '?')} | "
            f"仓位={result.final_decision.get('position_pct', 0):.0%}"
        )
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

        action = result.final_decision.get("action", "")
        suggested_pct = result.final_decision.get("position_pct", 0)
        # add = 新增敞口；hold/stand_aside = 0；reduce = 不增加
        add_pct = suggested_pct if action == "add" else 0.0
        new_total_gold_pct = float(
            result.final_decision.get("target_gold_pct")
            or (current_gold_pct + add_pct)
        )
        unrealized = float(result.final_decision.get("unrealized_pnl_pct") or 0)

        print(f"  风险画像: {risk_profile}")
        print(f"  持仓周期: {investment_horizon}")
        print(f"  当前黄金占比: {current_gold_pct:.0%} (上限 {max_gold_pct:.0%})")
        print(f"  浮盈亏: {unrealized:+.1%}")
        print(f"  建议动作: {result.final_decision.get('action_cn', action or '—')}")

        single_ok = add_pct <= max_single_pct
        total_ok = new_total_gold_pct <= max_gold_pct

        print(
            f"  建议新开/加仓: {add_pct:.0%} vs 单品种上限 {max_single_pct:.0%} "
            f"— {'兼容 ✅' if single_ok else '超出 ⚠️'}"
        )
        print(
            f"  目标总敞口: {new_total_gold_pct:.0%} "
            f"(现 {current_gold_pct:.0%}"
            f"{f'+{add_pct:.0%}' if add_pct else ''}) "
            f"vs 上限 {max_gold_pct:.0%} "
            f"— {'兼容 ✅' if total_ok else '超出 ⚠️'}"
        )

        if single_ok and total_ok:
            print("\n  综合: 建议符合画像约束 ✅")
        else:
            print("\n  综合: 建议部分超出画像约束 ⚠️")

    def _auto_track(self, result: AnalysisResult) -> None:
        """自动记录预测到预测追踪器，并结算过期未决预测."""
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

        # 方向：优先 action 映射，便于结算 long/neutral
        action = result.final_decision.get("action", "")
        if action == "add":
            track_direction = "long"
        elif action in ("reduce", "stop"):
            track_direction = "short"  # 结算语义：预期价格下跌侧有利
        elif action == "hold":
            track_direction = "neutral"
        else:
            track_direction = result.final_decision.get("direction", "neutral")

        tracker = PredictionTracker()
        if result.current_price > 0:
            resolved = tracker.auto_resolve_stale(
                current_price=result.current_price,
                min_age_hours=24,
            )
            if resolved:
                stats = tracker.stats()
                logger.info(
                    f"自动结算 {len(resolved)} 条过期预测 | "
                    f"ex_test准确率 {stats.get('accuracy_ex_test', 0):.0%} "
                    f"({stats.get('resolved_ex_test', 0)}条)"
                )

        record = PredictionRecord(
            id=uuid.uuid4().hex[:12],
            timestamp=datetime.now(),
            current_price=result.current_price,
            signals=serialized_signals,
            composite_score=result.bundle.composite_score,
            confidence=result.bundle.confidence,
            direction=track_direction,
            position_pct=result.final_decision.get("position_pct", 0),
            dimension_scores=dim_scores,
        )
        tracker.record_prediction(record)

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
