"""完整分析管线 — 封装 run_scan() 硬编码流程为可配置、可测试的类."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd
from loguru import logger

from gold_miner.config import settings
from gold_miner.data.macro import MacroDataFetcher
from gold_miner.data.news import NewsFetcher, NewsItem
from gold_miner.data.sentiment import SentimentDataFetcher
from gold_miner.data.spot_gold import SpotGoldFetcher
from gold_miner.decision.agents import AgentOpinion, BearAgent, BullAgent, PortfolioManager
from gold_miner.decision.risk import RiskCheck, RiskManager
from gold_miner.doctrine import DoctrineChecker
from gold_miner.events.models import EventType
from gold_miner.events.store import EventStore
from gold_miner.execution.alert import PriceAlert
from gold_miner.execution.dashboard import DashboardFormatter, TradeDecision
from gold_miner.execution.dimensions import print_all_dimensions
from gold_miner.execution.notifier import Notifier
from gold_miner.improvement.tracker import PredictionRecord, PredictionTracker
from gold_miner.llm.client import LLMClient
from gold_miner.signals.base import Signal, SignalBundle, SignalDirection, SignalStrength
from gold_miner.signals.engine import ScoringEngine
from gold_miner.signals.etf_flow_signal import EtfFlowSignalGenerator
from gold_miner.signals.fundamental import FundamentalAnalyzer
from gold_miner.signals.news_signal import NewsSignalGenerator
from gold_miner.signals.sentiment_signal import SentimentAnalyzer
from gold_miner.signals.technical import TechnicalAnalyzer


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


class AnalysisPipeline:
    """完整分析管线 — 8步流程.

    Steps:
        1. collect       — 数据采集
        2. generate_signals — 信号生成
        3. source_truth  — 来源验证 (FactChecker 已在 news pipeline 中运行)
        4. agent_debate  — 多空辩论
        5. risk_check    — 风控审查
        6. doctrine_check — 军规审查
        7. decide        — 决策输出
        8. track         — 自动追踪
    """

    def __init__(self) -> None:
        self._steps: list[str] = [
            "collect",
            "generate_signals",
            "source_truth",
            "agent_debate",
            "risk_check",
            "doctrine_check",
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

        # Step 7: decide
        if not ctx.skip_dashboard:
            self._step_decide(ctx, result)

        # Step 8: track
        if not ctx.skip_tracking:
            self._step_track(ctx, result)

        return result

    # ------------------------------------------------------------------
    # Step 1: 数据采集
    # ------------------------------------------------------------------

    def _step_collect(self, ctx: AnalysisContext, result: AnalysisResult) -> None:
        logger.info("[1/8] 数据采集...")

        gold_fetcher = SpotGoldFetcher()
        result.gold_df = gold_fetcher.fetch(days=ctx.days)
        if result.gold_df.empty:
            logger.error("现货黄金数据获取失败")
            return

        result.current_price = result.gold_df["close"].iloc[-1]
        logger.info(f"现货黄金最新价: {result.current_price:.2f}")

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

        logger.info("[1/8] 数据采集完成")

    # ------------------------------------------------------------------
    # Step 2: 信号生成
    # ------------------------------------------------------------------

    def _step_generate_signals(self, ctx: AnalysisContext, result: AnalysisResult) -> None:
        logger.info("[2/8] 信号生成...")

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
        logger.info("[2/8] 信号生成完成")

    # ------------------------------------------------------------------
    # Step 3: 来源验证
    # ------------------------------------------------------------------

    def _step_source_truth(self, ctx: AnalysisContext, result: AnalysisResult) -> None:
        """来源验证 — FactChecker 已在新闻信号生成时运行.

        此处仅做补充验证标签应用。
        """
        logger.info("[3/8] 来源验证...")
        # NewsSignalGenerator.fetch_and_analyze() 内部已调用 FactChecker
        # 这里可以添加跨维度一致性检查等额外验证
        logger.info("[3/8] 来源验证完成 (已在新闻管线中执行)")

    # ------------------------------------------------------------------
    # Step 4: Agent 辩论
    # ------------------------------------------------------------------

    def _step_agent_debate(self, ctx: AnalysisContext, result: AnalysisResult) -> None:
        logger.info("[4/8] Agent 辩论...")

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

        logger.info("[4/8] Agent 辩论完成")

    # ------------------------------------------------------------------
    # Step 5: 风控审查
    # ------------------------------------------------------------------

    def _step_risk_check(self, ctx: AnalysisContext, result: AnalysisResult) -> None:
        logger.info("[5/8] 风控审查...")

        risk_mgr = RiskManager(max_position_pct=settings.max_position_pct)
        result.checks = risk_mgr.check(result.decision)
        result.final_decision = risk_mgr.apply_risk_controls(result.decision, result.checks)

        if result.final_decision.get("risk_override"):
            logger.info(f"风控干预: {result.final_decision['risk_override']}")
        else:
            logger.info(f"风控通过 ({len(result.checks)}项检查)")

        logger.info("[5/8] 风控审查完成")

    # ------------------------------------------------------------------
    # Step 6: 军规审查
    # ------------------------------------------------------------------

    def _step_doctrine_check(self, ctx: AnalysisContext, result: AnalysisResult) -> None:
        logger.info("[6/8] 军规审查...")

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

        logger.info("[6/8] 军规审查完成")

    # ------------------------------------------------------------------
    # Step 7: 决策输出
    # ------------------------------------------------------------------

    def _step_decide(self, ctx: AnalysisContext, result: AnalysisResult) -> None:
        logger.info("[7/8] 决策输出...")

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

        # 仪表盘
        result.trade_decision = DashboardFormatter.from_analysis(
            signal_bundle=result.bundle,
            portfolio_decision=result.final_decision,
            instrument="现货黄金 (XAU/USD)",
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

        logger.info("[7/8] 决策输出完成")

    # ------------------------------------------------------------------
    # Step 8: 自动追踪
    # ------------------------------------------------------------------

    def _step_track(self, ctx: AnalysisContext, result: AnalysisResult) -> None:
        logger.info("[8/8] 自动追踪...")

        # 自动记录预测
        if settings.enable_auto_tracking:
            self._auto_track(result)

        # EventStore 记录
        result.prediction_id = self._record_events(result)

        logger.info("[8/8] 自动追踪完成")

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
            print(f"     调整后仓位: {result.final_decision.get('position_pct', 0):.0%}")

        print(f"{'='*60}")

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
