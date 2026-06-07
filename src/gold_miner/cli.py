"""命令行入口."""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from loguru import logger

from gold_miner.config import settings
from gold_miner.data.accumulation_gold import AccumulationGoldFetcher
from gold_miner.data.macro import MacroDataFetcher
from gold_miner.data.sentiment import SentimentDataFetcher
from gold_miner.data.spot_gold import SpotGoldFetcher
from gold_miner.decision.agents import BearAgent, BullAgent, PortfolioManager
from gold_miner.decision.risk import RiskManager
from gold_miner.execution.dashboard import DashboardFormatter
from gold_miner.execution.dimensions import print_all_dimensions
from gold_miner.execution.alert import PriceAlert
from gold_miner.execution.journal import TradeJournal
from gold_miner.execution.notifier import Notifier
from gold_miner.execution.report import ReportGenerator
from gold_miner.proxy import get_proxy_manager
from gold_miner.signals.base import SignalBundle
from gold_miner.signals.engine import ScoringEngine
from gold_miner.signals.fundamental import FundamentalAnalyzer
from gold_miner.data.news import NewsFetcher
from gold_miner.signals.etf_flow_signal import EtfFlowSignalGenerator
from gold_miner.signals.news_signal import NewsSignalGenerator
from gold_miner.signals.sentiment_signal import SentimentAnalyzer
from gold_miner.signals.technical import TechnicalAnalyzer
from gold_miner.backtest.engine import BacktestEngine
from gold_miner.improvement.analyzer import PerformanceAnalyzer
from gold_miner.improvement.findings import FindingGenerator
from gold_miner.improvement.tracker import PredictionRecord, PredictionTracker
from gold_miner.intelligence.analyzer import ArticleAnalyzer
from gold_miner.intelligence.forecaster import PriceForecaster
from gold_miner.intelligence.journal import ArticleJournal, ArticleRecord
from gold_miner.intelligence.reader import ArticleReader
from gold_miner.events.models import EventType
from gold_miner.events.store import EventStore
from gold_miner.llm.client import LLMClient
from gold_miner.doctrine import (
    ALL_MODELS,
    ALL_RULES,
    ALL_STRATEGIES,
    DoctrineChecker,
    DoctrineStore,
    get_model_by_id,
    get_rule_by_id,
    get_strategy_by_id,
)
from gold_miner.doctrine.munger_models import (
    ALL_MODELS as MUNGER_ALL,
    GOLD_MODELS as MUNGER_GOLD,
    get_by_discipline,
    get_by_slug,
    list_disciplines,
    search as search_munger,
)
from gold_miner.scenarios.analyzer import ScenarioAnalyzer
from gold_miner.scenarios.models import ScenarioReport
from gold_miner.scenarios.store import ScenarioStore
from gold_miner.verification.cli import run_verify


def setup_logging() -> None:
    """配置日志."""
    from loguru import logger as log
    log.remove()
    log.add(
        lambda msg: print(msg, end=""),
        level=settings.log_level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    )


def run_scan(days: int = 30, with_news: bool = True, with_sentiment: bool = True, deep: bool = False) -> None:
    """运行完整扫描流程."""
    logger.info("=" * 50)
    logger.info("开始黄金投资决策扫描")
    logger.info("=" * 50)

    # 1. 数据采集
    logger.info("[1/4] 数据采集...")

    gold_fetcher = SpotGoldFetcher()
    gold_df = gold_fetcher.fetch(days=days)
    if gold_df.empty:
        logger.error("现货黄金数据获取失败")
        return

    current_price = gold_df["close"].iloc[-1]
    logger.info(f"现货黄金最新价: {current_price:.2f}")

    # 美元指数 + 实际利率 + 白银 + 通胀预期
    macro_fetcher = MacroDataFetcher()
    dxy_df = macro_fetcher.fetch_dxy()
    rate_df = macro_fetcher.fetch_real_rate()
    silver_df = macro_fetcher.fetch_silver()
    breakeven_df = macro_fetcher.fetch_breakeven()
    if not rate_df.empty:
        logger.info(f"实际利率最新: {rate_df['value'].iloc[-1]:.2f}%")
    if not breakeven_df.empty:
        logger.info(f"通胀预期最新: {breakeven_df['value'].iloc[-1]:.2f}%")
    if not silver_df.empty:
        silver_price = silver_df["value"].iloc[-1]
        logger.info(f"白银最新价: {silver_price:.2f}")

    # 价格预警
    try:
        alert_mgr = PriceAlert()
        alert_mgr.check_all(gold_df=gold_df, dxy_df=dxy_df, silver_price=silver_price if not silver_df.empty else None)
    except Exception as e:
        logger.debug(f"价格预警检查异常: {e}")

    # 新闻
    news_signals: list = []
    news_raw: list = []
    if with_news:
        news_gen = NewsSignalGenerator()
        news_signals = news_gen.fetch_and_analyze(hours=24)
        # 获取原始新闻用于详情展示
        try:
            nf = NewsFetcher()
            news_raw = nf.fetch_latest(max_results=6)
            news_raw = nf.analyze_sentiment(news_raw)
        except Exception:
            pass
        logger.info(f"新闻信号: {len(news_signals)} 个")

    # 2. 信号处理
    logger.info("[2/4] 信号处理...")

    bundle = SignalBundle()

    # 技术面
    tech = TechnicalAnalyzer(gold_df)
    for sig in tech.generate_signals():
        bundle.add(sig)
    logger.info(f"技术信号: {bundle.by_dimension('technical').__len__()} 个")

    # 基本面
    fundamental = FundamentalAnalyzer(gold_df=gold_df, dxy_df=dxy_df, rate_df=rate_df, silver_df=silver_df, breakeven_df=breakeven_df)
    for sig in fundamental.generate_signals():
        bundle.add(sig)
    logger.info(f"基本面信号: {bundle.by_dimension('fundamental').__len__()} 个")

    # 消息面
    for sig in news_signals:
        bundle.add(sig)
    logger.info(f"消息面信号: {bundle.by_dimension('news').__len__()} 个")

    # 情绪面
    if with_sentiment:
        try:
            sentiment_fetcher = SentimentDataFetcher()
            au_df = sentiment_fetcher.fetch_au_futures(lookback=60)
            sentiment_analyzer = SentimentAnalyzer(au_df=au_df)
            for sig in sentiment_analyzer.generate_signals():
                bundle.add(sig)
        except Exception as e:
            logger.warning(f"情绪面数据获取异常，跳过: {e}")
    logger.info(f"情绪面信号: {bundle.by_dimension('sentiment').__len__()} 个")

    # ETF 资金流
    try:
        etf_gen = EtfFlowSignalGenerator()
        for sig in etf_gen.generate_signals():
            bundle.add(sig)
        logger.info(f"ETF资金流信号: {bundle.by_dimension('sentiment').__len__() - sum(1 for s in bundle.signals if s.metadata.get('source') not in ('gold_etf','btc_etf','cross_etf','gold_etf_volume'))} 个 (新增)")
    except Exception as e:
        logger.debug(f"ETF资金流信号异常: {e}")

    # DeepSeek 深度新闻分析
    if deep and news_signals:
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

    # 2.5. 四维度详细输出
    print_all_dimensions(
        gold_df=gold_df, dxy_df=dxy_df, rate_df=rate_df, breakeven_df=breakeven_df,
        silver_df=silver_df, news_items=news_raw if with_news else [],
        au_df=au_df if with_sentiment else None, bundle=bundle,
    )

    # 3. 打分+决策
    logger.info("[3/4] 打分与决策...")

    engine = ScoringEngine()
    engine.score(bundle)

    logger.info(f"综合评分: {bundle.composite_score:+.2f} | 置信度: {bundle.confidence:.0%}")

    # Agent辩论
    bull = BullAgent()
    bear = BearAgent()
    pm = PortfolioManager()

    bull_opinion = bull.analyze(bundle)
    bear_opinion = bear.analyze(bundle)
    decision = pm.decide(
        bull_opinion, bear_opinion, bundle,
        risk_profile=settings.risk_profile,
    )

    # Agent辩论 — 详细论据
    print(f"\n  🐂 {bull.NAME} (信心 {bull_opinion.confidence:.0%})")
    print(f"     立场: {bull_opinion.stance}  建议仓位: {bull_opinion.suggested_position_pct:.0%}")
    if bull_opinion.arguments:
        print(f"     论据:")
        for arg in bull_opinion.arguments:
            print(f"       ✓  {arg}")
    else:
        print(f"      (无强看涨信号)")

    print(f"\n  🐻 {bear.NAME} (信心 {bear_opinion.confidence:.0%})")
    print(f"     立场: {bear_opinion.stance}  建议仓位: {bear_opinion.suggested_position_pct:.0%}")
    if bear_opinion.arguments:
        print(f"     论据:")
        for arg in bear_opinion.arguments:
            print(f"       ✗  {arg}")
    else:
        print(f"      (无强看跌信号)")

    direction_cn = {"long": "做多", "short": "做空", "neutral": "观望"}
    print(f"\n  🏛️ {pm.NAME}: {direction_cn.get(decision['direction'], decision['direction'])} "
          f"| 仓位 {decision['position_pct']:.0%} | {decision['signal_type']}")

    # 风控审查
    risk_mgr = RiskManager(max_position_pct=settings.max_position_pct)
    checks = risk_mgr.check(decision)
    final_decision = risk_mgr.apply_risk_controls(decision, checks)

    if final_decision.get("risk_override"):
        print(f"\n  ⚠️ 风控干预: {final_decision['risk_override']}")
    else:
        print(f"\n  ✅ 风控通过 ({len(checks)}项检查)")

    # 3.5 投资军规审查
    active_dims = [d for d in ["technical", "fundamental", "news", "sentiment"]
                   if bundle.by_dimension(d)]
    doctrine_ctx = {
        "current_exposure": final_decision.get("position_pct", 0) * 0.5,
        "gold_allocation_pct": final_decision.get("position_pct", 0),
        "daily_change_pct": (
            abs(gold_df["close"].iloc[-1] / gold_df["close"].iloc[-2] - 1) * 100
            if len(gold_df) >= 2 else 0
        ),
        "near_data_event": False,
        "consecutive_stops": 0,
        "vix": 0,
        "fear_greed_index": 50,
        "unrealized_pnl_pct": 0.0,
        "has_trailing_stop": final_decision.get("position_pct", 0) > 0,
        "bullish_signal_count": bundle.bullish_count(),
        "bearish_signal_count": bundle.bearish_count(),
        "active_dimensions": active_dims,
        "bull_confidence": decision.get("bull_confidence", 0),
        "bear_confidence": decision.get("bear_confidence", 0),
        "stop_loss_set": final_decision.get("position_pct", 0) > 0,
        "has_decision_record": True,
    }
    final_decision = _print_and_apply_doctrine(final_decision, doctrine_ctx)

    # 4. 输出
    logger.info("[4/4] 生成决策仪表盘...")

    trade_decision = DashboardFormatter.from_analysis(
        signal_bundle=bundle,
        portfolio_decision=final_decision,
        instrument="现货黄金 (XAU/USD)",
        current_price=current_price,
    )

    print()
    print(DashboardFormatter.format(trade_decision))

    # 可选: 推送通知
    notifier = Notifier()
    if notifier.enabled and final_decision["position_pct"] > 0:
        notifier.send(f"黄金信号: {trade_decision.signal.upper()} | 仓位{trade_decision.position_pct:.0%}")

    # 自动记录预测 (自改进闭环)
    if settings.enable_auto_tracking:
        _auto_track_prediction(bundle, final_decision, current_price)

    # EventStore: 记录预测事件 + 证据快照
    _record_prediction_events(
        bundle=bundle,
        decision=final_decision,
        current_price=current_price,
        dxy_df=dxy_df,
        rate_df=rate_df,
        silver_df=silver_df,
        breakeven_df=breakeven_df,
        source="scan",
    )


def _record_prediction_events(
    bundle: SignalBundle,
    decision: dict,
    current_price: float,
    dxy_df: pd.DataFrame | None = None,
    rate_df: pd.DataFrame | None = None,
    silver_df: pd.DataFrame | None = None,
    breakeven_df: pd.DataFrame | None = None,
    source: str = "scan",
    source_refs: list[dict] | None = None,
    horizon_days: int = 7,
) -> str:
    """向 EventStore 写入 prediction_made + evidence_attached."""
    import uuid
    from dataclasses import asdict

    prediction_id = uuid.uuid4().hex[:12]
    store = EventStore()

    # prediction_made
    direction = decision.get("direction", "neutral")
    store.append(
        EventType.PREDICTION_MADE,
        prediction_id,
        {
            "direction": direction,
            "composite_score": round(bundle.composite_score, 4),
            "confidence": round(bundle.confidence, 4),
            "position_pct": decision.get("position_pct", 0),
            "horizon_days": horizon_days,
            "source": source,
            "auto_resolve": horizon_days <= 7,
            "current_price": round(current_price, 2),
        },
    )

    # 提取价格快照
    dxy_val = float(dxy_df["value"].iloc[-1]) if dxy_df is not None and not dxy_df.empty else None
    silver_val = float(silver_df["value"].iloc[-1]) if silver_df is not None and not silver_df.empty else None
    rate_val = float(rate_df["value"].iloc[-1]) if rate_df is not None and not rate_df.empty else None
    breakeven_val = float(breakeven_df["value"].iloc[-1]) if breakeven_df is not None and not breakeven_df.empty else None

    # 金银比
    gsr: float | None = None
    if current_price > 0 and silver_val and silver_val > 0:
        gsr = round(current_price / silver_val, 1)

    # 维度分数
    dim_scores: dict[str, float] = {}
    for dim in ["technical", "fundamental", "news", "sentiment"]:
        signals = bundle.by_dimension(dim)
        if signals:
            dim_scores[dim] = round(sum(s.score for s in signals) / len(signals), 2)
        else:
            dim_scores[dim] = 0.0

    # 信号摘要
    serialized_signals: list[dict] = []
    for s in bundle.signals:
        sd = asdict(s)
        sd["timestamp"] = sd["timestamp"].isoformat()
        serialized_signals.append(sd)

    # evidence_attached
    from gold_miner.events.models import EvidenceSnapshot
    snapshot = EvidenceSnapshot.from_price_data(
        prediction_id=prediction_id,
        spot_gold=round(current_price, 2),
        dxy=round(dxy_val, 2) if dxy_val else None,
        silver=round(silver_val, 2) if silver_val else None,
        real_rate=round(rate_val, 2) if rate_val else None,
        breakeven=round(breakeven_val, 2) if breakeven_val else None,
        gold_silver_ratio=gsr,
        signals=serialized_signals,
        dimension_scores=dim_scores,
        composite_score=round(bundle.composite_score, 4),
        confidence=round(bundle.confidence, 4),
        source_type=source,
        source_refs=source_refs,
    )
    store.append(
        EventType.EVIDENCE_ATTACHED,
        prediction_id,
        {"snapshot": snapshot},
    )

    logger.debug(f"EventStore 已记录: {prediction_id[:8]}... ({source}, {direction})")
    return prediction_id


def _auto_track_prediction(
    bundle: SignalBundle,
    decision: dict,
    current_price: float,
) -> None:
    """自动记录预测到预测追踪器."""
    import uuid
    from dataclasses import asdict

    dim_scores: dict[str, float] = {}
    for dim in ["technical", "fundamental", "news", "sentiment"]:
        signals = bundle.by_dimension(dim)
        if signals:
            dim_scores[dim] = round(sum(s.score for s in signals) / len(signals), 2)
        else:
            dim_scores[dim] = 0.0

    serialized_signals: list[dict] = []
    for s in bundle.signals:
        sd = asdict(s)
        sd["timestamp"] = sd["timestamp"].isoformat()
        serialized_signals.append(sd)

    record = PredictionRecord(
        id=uuid.uuid4().hex[:12],
        timestamp=datetime.now(),
        current_price=current_price,
        signals=serialized_signals,
        composite_score=bundle.composite_score,
        confidence=bundle.confidence,
        direction=decision.get("direction", "neutral"),
        position_pct=decision.get("position_pct", 0),
        dimension_scores=dim_scores,
    )
    PredictionTracker().record_prediction(record)


def run_backtest(args: argparse.Namespace) -> None:
    """运行历史回测."""
    logger.info("=" * 50)
    logger.info("开始历史回测")
    logger.info("=" * 50)

    # 1. 数据采集
    logger.info("[1/3] 加载历史价格数据...")

    gold_fetcher = SpotGoldFetcher()
    gold_df = gold_fetcher.fetch(days=args.days)
    if gold_df.empty:
        logger.error("历史价格数据获取失败")
        return

    logger.info(f"加载 {len(gold_df)} 条日线数据 ({gold_df['timestamp'].iloc[0].date()} ~ {gold_df['timestamp'].iloc[-1].date()})")

    macro_fetcher = MacroDataFetcher()
    dxy_df = macro_fetcher.fetch_dxy()
    if not dxy_df.empty:
        logger.info(f"加载 {len(dxy_df)} 条美元指数数据")

    # 2. 执行回测
    logger.info("[2/3] 执行回测...")

    capital = args.capital or settings.initial_capital_usd
    engine = BacktestEngine(initial_capital=capital)

    def signal_fn(df: pd.DataFrame) -> SignalBundle:
        bundle = SignalBundle()
        tech = TechnicalAnalyzer(df)
        for sig in tech.generate_signals():
            bundle.add(sig)
        if not dxy_df.empty:
            fundamental = FundamentalAnalyzer(gold_df=df, dxy_df=dxy_df)
            for sig in fundamental.generate_signals():
                bundle.add(sig)
        scoring = ScoringEngine()
        scoring.score(bundle)
        return bundle

    result = engine.run(gold_df, signal_fn)

    # 3. 输出结果
    logger.info("[3/3] 生成回测报告...")
    print()
    print("=" * 50)
    print("           回测结果")
    print("=" * 50)
    print(f"  初始资金: {capital:>12,.2f}")
    if result.equity_curve:
        print(f"  最终权益: {result.equity_curve[-1][1]:>12,.2f}")
    print(f"  总收益率: {result.total_return:>+11.2%}")
    print(f"  年化收益: {result.annual_return:>+11.2%}")
    print(f"  夏普比率: {result.sharpe_ratio:>11.2f}")
    print(f"  最大回撤: {result.max_drawdown:>11.2%}")
    print(f"  胜    率: {result.win_rate:>11.0%}")
    print(f"  总交易数: {result.total_trades:>11}")
    pf_str = "∞" if result.profit_factor == float("inf") else f"{result.profit_factor:.2f}"
    print(f"  盈亏比:   {pf_str:>11}")
    print("=" * 50)

    # 保存权益曲线
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("timestamp,equity\n")
            for ts, eq in result.equity_curve:
                f.write(f"{ts.isoformat()},{eq:.2f}\n")
        logger.info(f"权益曲线已保存至: {path}")


def run_track(args: argparse.Namespace) -> None:
    """预测追踪 — 记录、结算、列表."""
    tracker = PredictionTracker()

    if args.resolve_id:
        if not args.price:
            logger.error("结算预测需要 --price <实际价格>")
            return
        result = tracker.resolve_prediction(args.resolve_id, args.price)
        if result:
            status = "✓ 正确" if result.was_correct else "✗ 错误"
            logger.info(
                f"预测 {args.resolve_id} 已结算: {status} "
                f"(收益: {result.actual_return:+.2%})"
            )
        else:
            logger.warning(f"未找到未结算的预测: {args.resolve_id}")
        return

    if args.list:
        records = tracker.recent(20)
        if not records:
            print("暂无预测记录")
            return
        print(f"{'ID':<14} {'时间':<18} {'方向':<8} {'价格':>10} {'结算价':>10} {'状态'}")
        print("-" * 70)
        for r in records:
            status = "✓" if r.was_correct else "✗" if r.was_correct is False else "○"
            print(
                f"{r.id:<14} {r.timestamp.strftime('%m-%d %H:%M'):<18} "
                f"{r.direction:<8} {r.current_price:>10.2f} "
                f"{r.actual_price if r.actual_price else '-':>10} {status}"
            )
        return

    # 手动记录预测
    if not args.price:
        logger.error("手动记录需要 --price <当前价格>")
        return

    import uuid
    from dataclasses import asdict

    gold_fetcher = SpotGoldFetcher()
    gold_df = gold_fetcher.fetch(days=30)
    if gold_df.empty:
        logger.error("价格数据获取失败")
        return

    bundle = SignalBundle()
    tech = TechnicalAnalyzer(gold_df)
    for sig in tech.generate_signals():
        bundle.add(sig)

    macro_fetcher = MacroDataFetcher()
    dxy_df = macro_fetcher.fetch_dxy()
    fundamental = FundamentalAnalyzer(gold_df=gold_df, dxy_df=dxy_df)
    for sig in fundamental.generate_signals():
        bundle.add(sig)

    engine = ScoringEngine()
    engine.score(bundle)

    dim_scores: dict[str, float] = {}
    for dim in ["technical", "fundamental", "news", "sentiment"]:
        signals = bundle.by_dimension(dim)
        dim_scores[dim] = round(sum(s.score for s in signals) / len(signals), 2) if signals else 0.0

    direction = "buy" if bundle.composite_score > 0.2 else "sell" if bundle.composite_score < -0.2 else "hold"

    record = PredictionRecord(
        id=uuid.uuid4().hex[:12],
        timestamp=datetime.now(),
        current_price=args.price,
        signals=[asdict(s) for s in bundle.signals],
        composite_score=bundle.composite_score,
        confidence=bundle.confidence,
        direction=direction,
        position_pct=min(abs(bundle.composite_score) * 0.8, 0.8),
        dimension_scores=dim_scores,
    )
    tracker.record_prediction(record)


def run_review(args: argparse.Namespace) -> None:
    """效能分析 — 展示信号预测准确率仪表盘."""
    tracker = PredictionTracker()
    predictions = tracker.load_all()

    analyzer = PerformanceAnalyzer()
    result = analyzer.analyze(predictions)

    print()
    print("=" * 50)
    print("         信号预测效能仪表盘")
    print("=" * 50)
    print(f"  总预测: {result.total_predictions}  |  "
          f"已结算: {result.resolved_predictions}  |  "
          f"胜率: {result.overall_accuracy:.1%}")
    if result.resolved_predictions > 0:
        print(f"  平均收益: {result.avg_return:+.2%}")
    print()

    if result.per_dimension:
        print("─" * 50)
        print("  分维度准确率:")
        print(f"  {'维度':<12} {'准确率':>8} {'详情':>12}")
        print("  " + "-" * 40)
        for d in result.per_dimension:
            print(f"  {d.dimension:<12} {d.accuracy:>7.1%}  "
                  f"({d.correct}/{d.total})")

    if result.direction_accuracy:
        print(f"\n  买卖方向准确率: "
              f"买 {result.direction_accuracy.get('buy', 0):.1%} | "
              f"卖 {result.direction_accuracy.get('sell', 0):.1%} | "
              f"持 {result.direction_accuracy.get('hold', 0):.1%}")

    if result.per_signal:
        print()
        print("─" * 50)
        print("  分信号准确率 (按出现次数排序):")
        print(f"  {'信号名':<20} {'维度':<12} {'准确率':>8} {'详情':>12}")
        print("  " + "-" * 52)
        for s in result.per_signal[:10]:
            bar = "█" * int(s.accuracy * 10) + "░" * (10 - int(s.accuracy * 10))
            print(f"  {s.signal_name:<20} {s.dimension:<12} "
                  f"{s.accuracy:>7.1%}  ({s.correct}/{s.total}) {bar}")

    print()
    print("=" * 50)

    if result.resolved_predictions == 0:
        print("\n提示: 暂无已结算预测。使用 gold-miner track --resolve-id <ID> --price <价格> 结算预测。")


def run_findings(args: argparse.Namespace) -> None:
    """改进建议 — 基于效能数据生成分级发现."""
    tracker = PredictionTracker()
    predictions = tracker.load_all()

    analyzer = PerformanceAnalyzer()
    analysis = analyzer.analyze(predictions)

    generator = FindingGenerator()
    findings = generator.generate(analysis, predictions)

    print()
    print("=" * 50)
    print("       系统改进建议 (优先级排序)")
    print("=" * 50)

    if not findings:
        print("\n  暂无改进建议。系统各维度表现良好，或样本量不足。")
        if analysis.resolved_predictions < 5:
            print(f"  当前仅 {analysis.resolved_predictions} 条已结算预测，建议积累更多数据。")
        print()
        return

    severity_icons = {"high": "■", "medium": "◆", "low": "○"}

    for f in findings:
        icon = severity_icons.get(f.severity, "?")
        print(f"\n  {icon} [{f.severity.upper()}] {f.title}")
        print(f"     {f.description}")
        if f.suggested_value is not None:
            print(f"     当前值: {f.current_value:.0%} → 建议值: {f.suggested_value:.0%}")
        print(f"     建议: {f.recommendation}")

    print()
    print("=" * 50)


def run_scenario(args: argparse.Namespace) -> None:
    """情景分析 — 极端未来事件对黄金影响的假设推演."""

    # --list: 列出历史情景报告
    if args.list:
        store = ScenarioStore()
        reports = store.list_all(limit=20)
        if not reports:
            print("暂无情景分析记录")
            return
        print(f"{'ID':<14} {'时间':<18} {'方向':<8} {'基准涨跌':>10} {'时间窗口':>8} {'摘要'}")
        print("-" * 100)
        for r in reports:
            pi = r.price_impact
            direction = pi.direction if pi else "?"
            change = f"{pi.base_case_change_pct:+.1f}%" if pi else "?"
            horizon = f"{r.time_horizon_months}月"
            desc = r.scenario_description[:50].replace("\n", " ")
            print(f"{r.id:<14} {r.created_at.strftime('%m-%d %H:%M'):<18} "
                  f"{direction:<8} {change:>10} {horizon:>8}  {desc}")
        return

    # --show <id>: 查看详情
    if args.show:
        store = ScenarioStore()
        report = store.load(args.show)
        if not report:
            logger.error(f"未找到情景报告: {args.show}")
            return
        _print_scenario_report(report)
        return

    description = args.text
    if not description:
        logger.error("请提供情景描述: gold-miner scenario --text \"...\"")
        return

    # 默认: 执行情景分析
    logger.info("=" * 60)
    logger.info("情景分析 — 极端事件影响推演")
    logger.info("=" * 60)

    # 1. 收集当前市场上下文
    context: dict[str, Any] = {}
    try:
        gold_fetcher = SpotGoldFetcher()
        gold_df = gold_fetcher.fetch(days=30)
        if not gold_df.empty:
            context["spot_gold"] = round(float(gold_df["close"].iloc[-1]), 2)

        macro_fetcher = MacroDataFetcher()
        dxy_df = macro_fetcher.fetch_dxy()
        if not dxy_df.empty:
            context["dxy"] = round(float(dxy_df["value"].iloc[-1]), 2)

        rate_df = macro_fetcher.fetch_real_rate()
        if not rate_df.empty:
            context["real_rate"] = round(float(rate_df["value"].iloc[-1]), 2)

        breakeven_df = macro_fetcher.fetch_breakeven()
        if not breakeven_df.empty:
            context["breakeven"] = round(float(breakeven_df["value"].iloc[-1]), 2)

        silver_df = macro_fetcher.fetch_silver()
        if not silver_df.empty:
            silver_price = float(silver_df["value"].iloc[-1])
            context["silver"] = round(silver_price, 2)
            if context.get("spot_gold") and silver_price > 0:
                context["gold_silver_ratio"] = round(context["spot_gold"] / silver_price, 1)

        if context:
            logger.info(f"当前背景: 黄金 ${context.get('spot_gold', '?'):.0f} | "
                        f"DXY {context.get('dxy', '?'):.1f} | "
                        f"实际利率 {context.get('real_rate', '?'):.2f}%")
    except Exception as e:
        logger.warning(f"市场数据采集异常，LLM分析无当前背景: {e}")

    # 2. 执行情景分析
    logger.info(f"情景: {description[:80]}...")
    analyzer = ScenarioAnalyzer()
    report = analyzer.analyze(
        scenario_description=description,
        time_horizon_months=args.horizon or 12,
        context=context,
    )

    # 3. 输出
    _print_scenario_report(report)

    # 4. 可选: 关联预测追踪 (在保存前完成，确保prediction_id写入)
    if args.track and report.price_impact is not None:
        from gold_miner.improvement.tracker import PredictionRecord, PredictionTracker

        pi = report.price_impact
        direction_map = {"bullish": "buy", "bearish": "sell", "neutral": "hold"}
        pred_direction = direction_map.get(pi.direction, "hold")

        pred_record = PredictionRecord(
            id=report.id,
            timestamp=report.created_at,
            current_price=context.get("spot_gold", 0.0),
            signals=[],
            composite_score=(
                pi.confidence if pi.direction == "bullish"
                else -pi.confidence if pi.direction == "bearish"
                else 0.0
            ),
            confidence=pi.confidence,
            direction=pred_direction,
            position_pct=min(pi.confidence * 0.6, 0.6),
            dimension_scores={"scenario_analysis": pi.base_case_change_pct / 100.0},
        )
        PredictionTracker().record_prediction(pred_record)
        report.prediction_id = report.id
        logger.info(f"已关联预测追踪 (id: {report.id}, 方向: {pi.direction}, "
                    f"置信度: {pi.confidence:.0%})")

    # 5. 可选: 保存
    if args.save:
        store = ScenarioStore()
        store.save(report)
        logger.info(f"情景报告已保存 (id: {report.id})")

    # 6. 提示下一步
    print(f"\n提示: 使用 --save 保存报告, --track 关联预测追踪以跟踪预判准确率")
    print(f"  gold-miner scenario --text \"...\" --save --track --horizon 24")


def _print_scenario_report(report: ScenarioReport) -> None:
    """格式化打印情景分析报告."""
    print()
    print("=" * 70)
    print("         情景分析报告")
    print("=" * 70)
    print(f"  ID: {report.id}")
    print(f"  时间: {report.created_at.strftime('%Y-%m-%d %H:%M')}")
    print(f"  时间窗口: {report.time_horizon_months} 个月")
    print()

    # 情景描述
    print("─" * 70)
    print("  【假设情景】")
    for line in report.scenario_description.replace("\r", "").split("\n"):
        print(f"  {line.strip()}")
    print()

    # 当前背景
    if report.context_snapshot:
        print("─" * 70)
        print("  【分析背景】")
        ctx = report.context_snapshot
        if ctx.get("spot_gold"):
            print(f"  现货黄金: ${ctx['spot_gold']:.2f}/oz", end="")
        if ctx.get("dxy"):
            print(f"  |  DXY: {ctx['dxy']:.2f}", end="")
        if ctx.get("real_rate") is not None:
            print(f"  |  实际利率: {ctx['real_rate']:.2f}%", end="")
        if ctx.get("breakeven") is not None:
            print(f"  |  通胀预期: {ctx['breakeven']:.2f}%", end="")
        if ctx.get("silver"):
            print(f"  |  白银: ${ctx['silver']:.2f}", end="")
        if ctx.get("gold_silver_ratio"):
            print(f"  |  金银比: {ctx['gold_silver_ratio']:.1f}", end="")
        print()
    print()

    # 触发条件
    if report.trigger_conditions:
        print("─" * 70)
        print("  【触发条件】")
        for i, t in enumerate(report.trigger_conditions, 1):
            print(f"  {i}. {t}")
        print()

    # 传导路径
    if report.transmission_channels:
        print("─" * 70)
        print("  【传导路径】")
        magnitude_icons = {"strong": "●", "moderate": "◎", "weak": "○"}
        direction_icons = {"bullish": "↑", "bearish": "↓", "neutral": "→"}
        for c in report.transmission_channels:
            icon_m = magnitude_icons.get(c.magnitude, "?")
            icon_d = direction_icons.get(c.direction, "?")
            tf = c.timeframe or ""
            print(f"  {icon_m} [{c.channel}] {icon_d} ({c.magnitude}, {tf})")
            if c.description:
                print(f"     {c.description}")
        print()

    # 历史类比
    if report.historical_analogs:
        print("─" * 70)
        print("  【历史类比】")
        for a in report.historical_analogs:
            print(f"  ▸ {a.event_name} ({a.period})")
            print(f"    金价变动: {a.gold_price_change_pct:+.1f}% | 相似度: {a.similarity_score:.0%}")
            if a.key_parallels:
                print(f"    相似点: {', '.join(a.key_parallels[:3])}")
            if a.key_differences:
                print(f"    差异点: {', '.join(a.key_differences[:3])}")
            print()
        print()

    # 价格影响
    if report.price_impact:
        print("─" * 70)
        print("  【价格影响量化】")
        pi = report.price_impact
        direction_cn = {"bullish": "看涨 ↑", "bearish": "看跌 ↓", "neutral": "中性 →"}
        print(f"  方向: {direction_cn.get(pi.direction, pi.direction)}")
        print(f"  基准情景: {pi.base_case_change_pct:+.1f}%")
        print(f"  乐观情景: {pi.bullish_case_change_pct:+.1f}%")
        print(f"  悲观情景: {pi.bearish_case_change_pct:+.1f}%")
        print(f"  影响峰值: 约 {pi.peak_impact_months} 个月后")
        print(f"  置信度: {pi.confidence:.0%}")
        if pi.reasoning:
            print(f"  推理: {pi.reasoning[:300]}")
        print()

    # 概率评估
    if report.probability_assessment:
        print("─" * 70)
        print("  【概率评估】")
        print(f"  {report.probability_assessment}")
        print()

    # 关键价位
    if report.key_levels:
        print("─" * 70)
        print("  【关键价位】")
        levels_str = " / ".join(f"${k:.0f}" for k in report.key_levels)
        print(f"  {levels_str}")
        print()

    # 策略建议
    if report.strategy:
        print("─" * 70)
        print("  【应对策略】")
        s = report.strategy
        print(f"  总体定位: {s.overall_position}")
        if s.spot_gold_action:
            print(f"  现货黄金: {s.spot_gold_action}")
        if s.accumulation_gold_action:
            print(f"  积存金: {s.accumulation_gold_action}")
        if s.suggested_entry_zones:
            entry_str = " / ".join(f"${z:.0f}" for z in s.suggested_entry_zones)
            print(f"  入场区域: {entry_str}")
        if s.suggested_exit_zones:
            exit_str = " / ".join(f"${z:.0f}" for z in s.suggested_exit_zones)
            print(f"  离场区域: {exit_str}")
        if s.position_sizing:
            print(f"  仓位建议: {s.position_sizing}")
        if s.rebalancing_frequency:
            print(f"  审视频率: {s.rebalancing_frequency}")
        if s.hedging_suggestions:
            print(f"  对冲建议:")
            for h in s.hedging_suggestions:
                print(f"    - {h}")
        print()

    # 风险因子
    if report.risk_factors:
        print("─" * 70)
        print("  【风险因素】")
        for i, rf in enumerate(report.risk_factors, 1):
            print(f"  {i}. {rf}")
        print()

    # 监控指标
    if report.monitoring_indicators:
        print("─" * 70)
        print("  【建议监控的先行指标】")
        for i, mi in enumerate(report.monitoring_indicators, 1):
            print(f"  {i}. {mi}")
        print()

    # 尾部
    if report.prediction_id:
        print(f"  关联预测ID: {report.prediction_id} (已纳入预测追踪)")
    print("=" * 70)


def run_doctrine(args: argparse.Namespace) -> None:
    """投资军规审查与知识库浏览."""

    # --list: 列出规则/策略/思维模型
    if args.list:
        list_type = args.type or "all"
        if list_type in ("rules", "all"):
            print(f"\n{'='*70}")
            print(f"  投资军规 ({len(ALL_RULES)}条)")
            print(f"{'='*70}")
            for r in ALL_RULES:
                sev_icon = {"block": "■", "warn": "◆", "info": "○"}.get(r.severity, "?")
                enabled = "✓" if r.enabled else "✗"
                print(f"  {sev_icon} [{r.severity.upper()}] {r.id} {r.name}  [{enabled}]")
                print(f"     {r.description}")
            print()

        if list_type in ("strategies", "all"):
            print(f"{'='*70}")
            print(f"  投资策略 ({len(ALL_STRATEGIES)}个)")
            print(f"{'='*70}")
            for s in ALL_STRATEGIES:
                regime_cn = {
                    "trending": "趋势市", "ranging": "震荡市", "crisis": "危机市",
                    "recovery": "复苏市", "all": "通用",
                }
                regime = regime_cn.get(s.applicable_regime, s.applicable_regime)
                print(f"  ▸ {s.id} {s.name} [{regime}]")
                print(f"     {s.description[:100]}...")
                if s.mental_models:
                    print(f"     关联模型: {', '.join(s.mental_models)}")
            print()

        if list_type in ("models", "all"):
            print(f"{'='*70}")
            print(f"  思维模型 ({len(ALL_MODELS)}个)")
            print(f"{'='*70}")
            for m in ALL_MODELS:
                print(f"  ▸ {m.id} {m.name}")
                print(f"     {m.key_principle}")
            print()

            # Munger 模型库概览
            if MUNGER_ALL:
                print(f"{'='*70}")
                print(f"  Munger 多元思维模型库 ({len(MUNGER_ALL)}个, 黄金相关 {len(MUNGER_GOLD)}个)")
                print(f"{'='*70}")
                disc_counts = list_disciplines()
                for dslug, dname in [
                    ("invest", "投资学与金融学"), ("decision", "投资原则与品格"),
                    ("psych", "心理学"), ("econ", "微观经济学"), ("math", "数学与统计学"),
                    ("mgmt", "管理学与商业"), ("meta", "元认知与思维方法论"),
                    ("complex", "复杂系统"), ("bio", "生物学与进化论"),
                    ("physics", "物理学与化学"), ("eng", "工程学"),
                    ("law", "法学与政治学"), ("history", "历史学与哲学"),
                    ("accounting", "会计学"),
                ]:
                    if dname in disc_counts:
                        mark = " *" if dslug in ("invest", "decision", "complex", "math", "econ", "meta", "psych", "mgmt") else ""
                        print(f"  {dname}: {disc_counts[dname]}个{mark}")
                print("  (* 标注学科与黄金投资直接相关)")
                print()

        return

    # --search: 搜索 Munger 模型库
    if args.search:
        query = args.search
        results = search_munger(query)
        print(f"\n搜索 '{query}' — 找到 {len(results)} 个模型")
        for m in results[:20]:
            gold_mark = " [黄金相关]" if m.gold_applicable else ""
            print(f"  ▸ {m.name_cn} ({m.name_en}){gold_mark}")
            if m.description:
                print(f"     {m.description[:80]}...")
            print(f"     学科: {m.discipline} | {m.url}")
        if len(results) > 20:
            print(f"  ... 还有 {len(results) - 20} 个结果")
        return

    # --discipline: 按学科浏览 Munger 模型
    if args.discipline:
        disc = args.discipline
        models = get_by_discipline(disc)
        disc_name = models[0].discipline if models else disc
        print(f"\n{disc_name} — {len(models)} 个模型")
        for m in models:
            gold_mark = " [黄金相关]" if m.gold_applicable else ""
            print(f"  ▸ {m.name_cn} ({m.name_en}){gold_mark}")
            if m.description:
                print(f"     {m.description[:80]}...")
        return

    # --show <id>: 查看详情
    if args.show:
        item_id = args.show
        rule = get_rule_by_id(item_id)
        strategy = get_strategy_by_id(item_id) if not rule else None
        model = get_model_by_id(item_id) if not rule and not strategy else None

        if rule:
            _print_rule_detail(rule)
        elif strategy:
            _print_strategy_detail(strategy)
        elif model:
            _print_model_detail(model)
        else:
            logger.error(f"未找到: {item_id}")
        return

    # --toggle <rule_id>: 启用/禁用规则
    if args.toggle:
        store = DoctrineStore()
        new_state = store.toggle(args.toggle)
        status = "启用" if new_state else "禁用"
        logger.info(f"规则 {args.toggle} 已{status}")
        return

    # --check: 对当前决策运行军规审查
    if args.check:
        _run_doctrine_check(args)
        return

    # 默认: 显示概览
    print(f"\n投资军规系统 — 概览")
    print(f"  军规: {len(ALL_RULES)}条")
    print(f"  策略: {len(ALL_STRATEGIES)}个")
    print(f"  思维模型: {len(ALL_MODELS)}个")
    print(f"\n使用 gold-miner doctrine --list 查看全部")
    print(f"使用 gold-miner doctrine --show <id> 查看详情")
    print(f"使用 gold-miner doctrine --check 运行军规审查")
    print(f"使用 gold-miner doctrine --toggle <rule_id> 启用/禁用规则")


def _print_rule_detail(rule) -> None:
    sev_cn = {"block": "阻断 (BLOCK)", "warn": "警告 (WARN)", "info": "提示 (INFO)"}
    cat_cn = {
        "position_sizing": "仓位管理", "timing": "时机选择",
        "emotion": "情绪纪律", "process": "流程纪律",
    }
    print(f"\n{'='*60}")
    print(f"  军规详情: {rule.id}")
    print(f"{'='*60}")
    print(f"  名称: {rule.name}")
    print(f"  级别: {sev_cn.get(rule.severity, rule.severity)}")
    print(f"  类别: {cat_cn.get(rule.category, rule.category)}")
    print(f"  描述: {rule.description}")
    print(f"  状态: {'启用' if rule.enabled else '禁用'}")
    print(f"{'='*60}")


def _print_strategy_detail(strategy) -> None:
    regime_cn = {"trending": "趋势市", "ranging": "震荡市", "crisis": "危机市", "recovery": "复苏市", "all": "通用"}
    print(f"\n{'='*60}")
    print(f"  策略详情: {strategy.id} {strategy.name}")
    print(f"{'='*60}")
    print(f"  适用市场: {regime_cn.get(strategy.applicable_regime, strategy.applicable_regime)}")
    print(f"\n  {strategy.description}")
    if strategy.position_sizing:
        print(f"\n  仓位管理: {strategy.position_sizing}")
    if strategy.entry_rules:
        print(f"  入场规则:")
        for r in strategy.entry_rules:
            print(f"    - {r}")
    if strategy.exit_rules:
        print(f"  离场规则:")
        for r in strategy.exit_rules:
            print(f"    - {r}")
    if strategy.stop_loss_rule:
        print(f"  止损: {strategy.stop_loss_rule}")
    if strategy.mental_models:
        print(f"  关联思维模型: {', '.join(strategy.mental_models)}")
    if strategy.pros:
        print(f"  优势: {', '.join(strategy.pros[:3])}")
    if strategy.cons:
        print(f"  劣势: {', '.join(strategy.cons[:3])}")
    print(f"{'='*60}")


def _print_model_detail(model) -> None:
    print(f"\n{'='*60}")
    print(f"  思维模型: {model.id} {model.name}")
    print(f"{'='*60}")
    print(f"\n  {model.description}")
    print(f"\n  核心原则: {model.key_principle}")
    print(f"  适用场景: {model.when_to_apply}")
    if model.gold_application:
        print(f"  黄金应用: {model.gold_application}")
    if model.related_strategies:
        print(f"  关联策略: {', '.join(model.related_strategies)}")
    if model.reference:
        print(f"  参考来源: {model.reference}")
    print(f"{'='*60}")


def _run_doctrine_check(args: argparse.Namespace) -> None:
    """独立运行军规审查（基于模拟上下文）."""
    import random

    # 构造模拟决策和上下文
    direction = args.direction or random.choice(["long", "short", "neutral"])
    position = args.price or random.uniform(0.05, 0.5) if args.price else random.uniform(0.05, 0.5)

    from gold_miner.decision.agents import BullAgent, BearAgent
    from gold_miner.signals.base import SignalBundle

    decision = {
        "direction": direction,
        "position_pct": round(position, 2),
        "signal_type": "中等信号" if position > 0.2 else "弱信号",
    }

    active_dims = args.dims.split(",") if args.dims else ["technical", "fundamental"]
    context = {
        "current_exposure": 0.3,
        "gold_allocation_pct": 0.35,
        "daily_change_pct": args.change or 1.5,
        "near_data_event": args.data_event or False,
        "consecutive_stops": 0,
        "vix": 18.5,
        "fear_greed_index": 55,
        "unrealized_pnl_pct": 0.12,
        "has_trailing_stop": True,
        "bullish_signal_count": 5 if direction == "long" else 2,
        "bearish_signal_count": 2 if direction == "long" else 5,
        "active_dimensions": active_dims,
        "bull_confidence": 0.65 if direction == "long" else 0.35,
        "bear_confidence": 0.35 if direction == "long" else 0.65,
        "stop_loss_set": True,
        "has_decision_record": True,
    }

    _print_and_apply_doctrine(decision, context)


def _print_and_apply_doctrine(decision: dict, context: dict) -> dict:
    """运行军规审查并打印结果."""
    checker = DoctrineChecker()
    result = checker.check(decision, context)
    adjusted = checker.apply_doctrine(decision, result)

    print(f"\n{'='*60}")
    print(f"  投资军规审查")
    print(f"{'='*60}")
    print(f"  决策: 方向={decision.get('direction', '?')} | 仓位={decision.get('position_pct', 0):.0%}")
    print(f"  通过: {result.passed_count}/{len(result.violations)}")

    if result.blocks:
        print(f"\n  ■ 阻断 ({len(result.blocks)}项):")
        for v in result.blocks:
            print(f"    ✗  {v.rule.name}: {v.message}")

    if result.warnings:
        print(f"\n  ◆ 警告 ({len(result.warnings)}项):")
        for v in result.warnings:
            print(f"    !  {v.rule.name}: {v.message}")

    if result.infos:
        print(f"\n  ○ 提示 ({len(result.infos)}项):")
        for v in result.infos:
            print(f"    i  {v.rule.name}: {v.message}")

    if result.all_passed:
        print(f"\n  ✅ 全部军规通过")

    if adjusted.get("doctrine_override"):
        print(f"\n  ⚡ 军规调整: {adjusted['doctrine_override']}")
        print(f"     调整后仓位: {adjusted.get('position_pct', 0):.0%}")

    print(f"{'='*60}")
    return adjusted


def run_analyze(args: argparse.Namespace) -> None:
    """文章情报分析 — 摄入/列表/查看/更新/预判."""
    journal = ArticleJournal()

    # --list: 列出所有已分析文章
    if args.list:
        records = journal.list_all()
        if not records:
            print("暂无文章分析记录")
            return
        print(f"{'ID':<14} {'时间':<18} {'方向':<8} {'可信度':<8} {'来源'}")
        print("-" * 80)
        for r in records:
            suspicion = "⚠️" if r.is_suspicious else "✓"
            direction = r.sentiment_direction[:6]
            source = r.source_url[:50] if r.source_url else "(文本输入)"
            print(f"{r.id:<14} {r.created_at.strftime('%m-%d %H:%M'):<18} "
                  f"{direction:<8} {suspicion:<8} {source}")
        return

    # --show <id>: 查看详情
    if args.show:
        record = journal.get(args.show)
        if not record:
            logger.error(f"未找到记录: {args.show}")
            return
        _print_article_detail(record)
        return

    # --update <id>: 追加 LLM 分析或交叉验证
    if args.update:
        record = journal.get(args.update)
        if not record:
            logger.error(f"未找到记录: {args.update}")
            return

        updates: dict = {}
        if args.llm_analysis:
            try:
                updates["llm_analysis"] = json.loads(args.llm_analysis)
            except json.JSONDecodeError:
                updates["llm_analysis"] = {"raw": args.llm_analysis}
        if args.cross_ref:
            try:
                updates["cross_ref"] = json.loads(args.cross_ref)
                updates["status"] = "cross_referenced"
            except json.JSONDecodeError:
                updates["cross_ref"] = {"raw": args.cross_ref}

        if updates:
            journal.update(args.update, **updates)
            logger.info(f"记录 {args.update} 已更新")
        else:
            logger.warning("未提供更新内容 (--llm-analysis / --cross-ref)")
        return

    # --predict <id>: 生成价格预判
    if args.predict:
        record = journal.get(args.predict)
        if not record:
            logger.error(f"未找到记录: {args.predict}")
            return

        if not args.direction:
            logger.error("预判需要 --direction (bullish/bearish/neutral)")
            return

        journal.update(
            args.predict,
            forecast_direction=args.direction,
            forecast_confidence=args.confidence or 0.5,
            forecast_horizon_days=args.horizon or 7,
            forecast_target_pct=args.target_pct or 0.0,
            forecast_reasoning=args.reasoning or "",
            status="forecasted",
        )
        logger.info(f"预判已保存: {args.direction} (置信度: {args.confidence or 0.5:.0%})")

        # 同步写入 PredictionTracker
        if settings.enable_auto_tracking:
            forecast_record = PredictionRecord(
                id=record.id,
                timestamp=record.created_at,
                current_price=0.0,  # 由 resolve 时填写
                signals=[],
                composite_score=record.sentiment_score,
                confidence=args.confidence or 0.5,
                direction=args.direction,
                position_pct=min(abs(record.sentiment_score) * 0.5, 0.5),
                dimension_scores={"article_analysis": record.sentiment_score},
            )
            PredictionTracker().record_prediction(forecast_record)
        return

    # 默认: 摄入并分析文章
    url_or_text = args.url or args.text
    if not url_or_text:
        logger.error("请提供 --url <文章链接> 或 --text <文章文本>")
        return

    _ingest_and_analyze(url_or_text, is_url=bool(args.url), deep=args.deep)


def _ingest_and_analyze(input_str: str, is_url: bool = False, deep: bool = False) -> None:
    """摄入文章并执行规则分析."""
    import uuid

    logger.info("=" * 60)
    logger.info("文章情报分析")
    logger.info("=" * 60)

    # 1. 读取
    if is_url:
        logger.info(f"抓取文章: {input_str}")
        text = ArticleReader.from_url(input_str)
        if not text:
            logger.error("文章抓取失败")
            return
    else:
        text = ArticleReader.from_text(input_str)

    logger.info(f"文章长度: {len(text)} 字符")

    # 2. 规则分析
    analyzer = ArticleAnalyzer()
    analysis = analyzer.analyze(text)

    # 3. 输出分析结果
    _print_analysis_result(analysis, text)

    # 4. 提取标题
    title = text[:80].replace("\n", " ").strip()
    if len(text) > 80:
        title += "..."

    # 5. 保存
    source_url = input_str if is_url else ""
    record = ArticleRecord(
        id=uuid.uuid4().hex[:12],
        source_url=source_url,
        title=title,
        text_preview=text[:200],
        word_count=analysis.word_count,
        sentiment_score=analysis.sentiment_score,
        sentiment_direction=analysis.sentiment_direction,
        manipulation_score=analysis.manipulation_score,
        manipulation_flags=analysis.manipulation_flags,
        is_suspicious=analysis.is_suspicious,
        claims=analysis.claims,
    )
    ArticleJournal().save(record)

    # 6. LLM 深度分析 (可选)
    if deep:
        logger.info("[LLM] 使用 DeepSeek 进行深度分析...")
        llm = LLMClient()
        llm_result = llm.analyze_article(
            text=text,
            rule_sentiment=analysis.sentiment_direction,
            rule_score=analysis.sentiment_score,
            rule_claims=analysis.claims,
            manipulation_flags=analysis.manipulation_flags,
        )

        if llm_result and not llm_result.get("parse_error"):
            journal = ArticleJournal()
            journal.update(record.id, llm_analysis=llm_result, status="cross_referenced")

            print()
            print("─" * 60)
            print("  LLM 深度分析 (DeepSeek)")
            print("─" * 60)
            print(f"  方向: {llm_result.get('sentiment', '?')}")
            print(f"  置信度: {llm_result.get('confidence', 0):.0%}")
            print(f"  可信度: {llm_result.get('credibility', 0):.0%}")
            print(f"  时间窗口: {llm_result.get('horizon_days', '?')}天")
            if llm_result.get("is_pumping"):
                print(f"  ⚠️ 疑似带节奏")
            if llm_result.get("is_institutional_manipulation"):
                print(f"  ⚠️ 疑似机构操纵")
            if llm_result.get("key_drivers"):
                print(f"  核心驱动: {', '.join(llm_result['key_drivers'])}")
            print(f"  推理: {llm_result.get('reasoning', '')[:200]}")
            print("─" * 60)

            # 自动生成预判
            llm_dir = llm_result.get("sentiment", "neutral")
            llm_conf = llm_result.get("confidence", 0.5)
            llm_horizon = llm_result.get("horizon_days", 7)
            reasoning = llm_result.get("reasoning", "")
            journal.update(
                record.id,
                forecast_direction=llm_dir,
                forecast_confidence=llm_conf,
                forecast_horizon_days=llm_horizon,
                forecast_reasoning=reasoning,
                status="forecasted",
            )
            logger.info(f"LLM 预判已自动保存: {llm_dir} (置信度: {llm_conf:.0%})")

            # 同步到 PredictionTracker
            if settings.enable_auto_tracking:
                forecast_record = PredictionRecord(
                    id=record.id,
                    timestamp=record.created_at,
                    current_price=0.0,
                    signals=[],
                    composite_score=analysis.sentiment_score,
                    confidence=llm_conf,
                    direction=llm_dir,
                    position_pct=min(abs(analysis.sentiment_score) * 0.5, 0.5),
                    dimension_scores={"article_llm": analysis.sentiment_score},
                )
                PredictionTracker().record_prediction(forecast_record)

            # EventStore: 记录文章预判 + 证据
            _record_article_prediction_events(
                record_id=record.id,
                analysis=analysis,
                direction=llm_dir,
                confidence=llm_conf,
                horizon_days=llm_horizon,
                reasoning=reasoning,
                source_url=source_url,
            )
        else:
            logger.warning("LLM 分析失败，使用规则分析结果")

    # 7. 提示下一步
    print()
    logger.info(f"分析已保存 (id: {record.id})")
    if not deep and analysis.claims:
        print("\n可交叉验证的关键主张:")
        for i, c in enumerate(analysis.claims[:5], 1):
            print(f"  {i}. [{c['category']}] {c['claim']}")
        print(f"\n提示: 使用 --deep 自动调用 DeepSeek 深度分析，或手动:")
        print(f"  gold-miner analyze --update {record.id} --cross-ref '{{...}}'")
    print(f"  gold-miner analyze --predict {record.id} --direction <bullish|bearish> --confidence <0.X>")


def _record_article_prediction_events(
    record_id: str,
    analysis,
    direction: str,
    confidence: float,
    horizon_days: int,
    reasoning: str = "",
    source_url: str = "",
) -> None:
    """向 EventStore 写入文章情报预判事件."""
    store = EventStore()

    # 证据快照: 文章分析上下文
    from gold_miner.events.models import EvidenceSnapshot, SourceRef

    # 来源引用
    refs: list[dict] = []
    if source_url:
        refs.append({
            "ref_type": "article",
            "ref_id": record_id,
            "url": source_url,
            "title": analysis.summary[:80] if analysis.summary else "",
        })
    # 关键主张作为额外引用
    for claim in analysis.claims[:5]:
        refs.append({
            "ref_type": "claim",
            "ref_id": record_id,
            "title": f"[{claim.get('category', '')}] {claim.get('claim', '')}",
            "description": claim.get("pattern", ""),
        })

    store.append(
        EventType.PREDICTION_MADE,
        record_id,
        {
            "direction": direction,
            "composite_score": round(analysis.sentiment_score, 4),
            "confidence": round(confidence, 4),
            "position_pct": round(min(abs(analysis.sentiment_score) * 0.5, 0.5), 2),
            "horizon_days": horizon_days,
            "source": "article",
            "auto_resolve": horizon_days <= 7,
            "current_price": 0.0,
        },
    )

    snapshot = EvidenceSnapshot.from_price_data(
        prediction_id=record_id,
        spot_gold=0.0,  # 文章分析不含实时价格
        composite_score=round(analysis.sentiment_score, 4),
        confidence=round(confidence, 4),
        source_type="article",
        source_refs=refs,
        signals=[
            {
                "name": f"文章情感: {analysis.sentiment_direction}",
                "dimension": "news",
                "direction": direction,
                "score": analysis.sentiment_score,
                "description": f"操纵得分: {analysis.manipulation_score}/7, 字数: {analysis.word_count}",
            }
        ],
        dimension_scores={"article_analysis": analysis.sentiment_score},
    )
    store.append(
        EventType.EVIDENCE_ATTACHED,
        record_id,
        {"snapshot": snapshot},
    )

    logger.debug(f"EventStore 已记录文章预判: {record_id[:8]}... ({direction})")


def _print_analysis_result(analysis, text: str) -> None:
    """打印规则分析结果."""
    print()
    print("─" * 60)
    print("  规则分析结果")
    print("─" * 60)

    # 情感
    icon = "📈" if analysis.sentiment_direction == "bullish" else "📉" if analysis.sentiment_direction == "bearish" else "➡️"
    print(f"  {icon} 情感倾向: {analysis.sentiment_direction} "
          f"(得分: {analysis.sentiment_score:+.2f})")
    print(f"     看涨词: {analysis.bullish_count}个 | 看跌词: {analysis.bearish_count}个")

    # 可信度
    print()
    if analysis.is_suspicious:
        print(f"  ⚠️ 可信度: 疑似带节奏 ({analysis.manipulation_score}/7项)")
        for flag in analysis.manipulation_flags:
            print(f"     - {flag}")
    else:
        print(f"  ✓ 可信度: 暂未检测到明显操纵话术 ({analysis.manipulation_score}/7项)")

    # 关键主张
    if analysis.claims:
        print(f"\n  📋 关键主张 ({len(analysis.claims)}条):")
        for c in analysis.claims:
            print(f"     [{c['category']}] {c['claim']}")

    print("─" * 60)


def _print_article_detail(record: ArticleRecord) -> None:
    """打印文章分析详情."""
    print()
    print("=" * 60)
    print(f"  文章分析详情: {record.id}")
    print("=" * 60)
    print(f"  来源: {record.source_url or '(文本输入)'}")
    print(f"  时间: {record.created_at.strftime('%Y-%m-%d %H:%M')}")
    print(f"  字数: {record.word_count}")
    print()
    print(f"  情感方向: {record.sentiment_direction} ({record.sentiment_score:+.2f})")
    print(f"  操纵得分: {record.manipulation_score}/7 {'⚠️' if record.is_suspicious else '✓'}")
    if record.manipulation_flags:
        for f in record.manipulation_flags:
            print(f"    - {f}")
    print()
    if record.claims:
        print("  关键主张:")
        for c in record.claims:
            print(f"    [{c['category']}] {c['claim']}")
    print()
    if record.llm_analysis:
        print(f"  LLM分析: {json.dumps(record.llm_analysis, ensure_ascii=False)[:200]}")
    if record.cross_ref:
        print(f"  交叉验证: {json.dumps(record.cross_ref, ensure_ascii=False)[:200]}")
    if record.forecast_direction:
        print(f"  价格预判: {record.forecast_direction} "
              f"(置信度: {record.forecast_confidence:.0%}, "
              f"窗口: {record.forecast_horizon_days}天)")
        if record.forecast_reasoning:
            print(f"  推理: {record.forecast_reasoning[:200]}")
    print("=" * 60)


def run_daemon(args: argparse.Namespace) -> None:
    """定时自动扫描守护进程."""
    import schedule

    interval = args.interval or 60
    last_run: datetime | None = None

    def job() -> None:
        nonlocal last_run
        now = datetime.now()
        logger.info(f"定时扫描触发 ({now.strftime('%H:%M')})")
        try:
            run_scan(days=30, with_news=False, with_sentiment=False)
            last_run = now
        except Exception as e:
            logger.error(f"定时扫描异常: {e}")

        # 自动结算到期预测
        try:
            from gold_miner.events.resolver import AutoResolver
            from gold_miner.verification.reporter import VerificationReporter
            resolver = AutoResolver()
            result = resolver.resolve_due()
            if result["auto_settled"] or result["awaiting_verification"]:
                logger.info(
                    f"自动结算: {len(result['auto_settled'])}条, "
                    f"待确认: {len(result['awaiting_verification'])}条"
                )
                # 生成本轮验证报告
                if settings.enable_auto_tracking:
                    reporter = VerificationReporter()
                    report_path = reporter.generate_cycle_report(result)
                    logger.info(f"验证报告: {report_path}")
        except Exception as e:
            logger.error(f"自动结算异常: {e}")

    logger.info(f"守护进程启动 — 每 {interval} 分钟自动扫描一次")
    logger.info("按 Ctrl+C 退出")

    if args.once:
        job()
        return

    schedule.every(interval).minutes.do(job)
    job()  # 首次立即执行

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
            if last_run:
                next_run = last_run + timedelta(minutes=interval)
                remaining = (next_run - datetime.now()).total_seconds()
                if remaining > 0:
                    logger.debug(f"下次扫描: {next_run.strftime('%H:%M')} ({remaining:.0f}s后)")
    except KeyboardInterrupt:
        logger.info("守护进程已停止")


def main() -> None:
    parser = argparse.ArgumentParser(description="黄金投资决策辅助系统")
    parser.add_argument(
        "command",
        choices=[
            "scan", "quote", "backtest", "journal", "proxy-install",
            "track", "review", "findings", "analyze", "scenario", "doctrine", "daemon",
            "verify", "report",
        ],
        help="命令",
    )
    parser.add_argument("--days", type=int, default=365, help="回溯天数")
    parser.add_argument("--news", action="store_true", help="启用新闻分析（需配置API key）")
    parser.add_argument("--sentiment", action="store_true", help="启用情绪分析（COT/ETF数据）")
    parser.add_argument("--risk", choices=["aggressive", "moderate", "conservative"],
                        default=None, help="风险偏好")
    parser.add_argument("--capital", type=float, default=None, help="初始资金（回测用）")
    parser.add_argument("--output", type=str, default=None, help="权益曲线CSV输出路径（回测用）")
    parser.add_argument("--price", type=float, default=None, help="当前/实际价格 (track 命令)")
    parser.add_argument("--resolve-id", type=str, default=None, help="要结算的预测ID (track 命令)")
    parser.add_argument("--list", action="store_true", default=False, help="列出预测记录 (track --list)")
    # analyze 命令参数
    parser.add_argument("--url", type=str, default=None, help="文章URL (analyze)")
    parser.add_argument("--text", type=str, default=None, help="文章/情景文本 (analyze/scenario)")
    parser.add_argument("--show", type=str, default=None, help="查看文章详情 (analyze --show <id>)")
    parser.add_argument("--update", type=str, default=None, help="更新记录 (analyze --update <id>)")
    parser.add_argument("--llm-analysis", type=str, default=None, help="LLM分析JSON (analyze --update)")
    parser.add_argument("--cross-ref", type=str, default=None, help="交叉验证JSON (analyze --update)")
    parser.add_argument("--predict", type=str, default=None, help="生成预判 (analyze --predict <id>)")
    parser.add_argument("--direction", type=str, default=None, help="预判方向 bullish|bearish|neutral")
    parser.add_argument("--confidence", type=float, default=None, help="预判置信度 0.0-1.0")
    parser.add_argument("--horizon", type=int, default=7, help="预判时间窗口天数")
    parser.add_argument("--target-pct", type=float, default=None, help="预期涨跌幅")
    parser.add_argument("--reasoning", type=str, default=None, help="预判推理链")
    parser.add_argument("--deep", action="store_true", default=False, help="使用LLM深度分析文章 (analyze)")
    # daemon 命令参数
    parser.add_argument("--interval", type=int, default=60, help="扫描间隔(分钟)")
    parser.add_argument("--once", action="store_true", default=False, help="仅执行一次")
    # scenario 命令参数
    parser.add_argument("--save", action="store_true", default=False, help="保存情景报告 (scenario --save)")
    parser.add_argument("--track", action="store_true", default=False, help="关联预测追踪 (scenario --track)")
    # doctrine 命令参数
    parser.add_argument("--check", action="store_true", default=False, help="运行军规审查 (doctrine --check)")
    parser.add_argument("--toggle", type=str, default=None, help="启用/禁用规则 (doctrine --toggle <rule_id>)")
    parser.add_argument("--type", type=str, default=None, help="列出类型: rules/strategies/models (doctrine --list --type)")
    parser.add_argument("--dims", type=str, default=None, help="活跃维度 (doctrine --check --dims technical,fundamental)")
    parser.add_argument("--change", type=float, default=None, help="模拟日波动% (doctrine --check)")
    parser.add_argument("--data-event", action="store_true", default=False, help="模拟重大数据前 (doctrine --check)")
    parser.add_argument("--search", type=str, default=None, help="搜索Munger模型库 (doctrine --search <关键词>)")
    parser.add_argument("--discipline", type=str, default=None, help="按学科筛选Munger模型 (doctrine --discipline invest)")
    # verify 命令参数
    parser.add_argument("--id", type=str, default=None, help="查看预测详情 (verify --id <ID>)")
    parser.add_argument("--confirm", type=str, default=None, help="人工确认结算 (verify --confirm <ID>)")
    parser.add_argument("--reject", type=str, default=None, help="无效化预测 (verify --reject <ID>)")
    parser.add_argument("--reason", type=str, default=None, help="无效化/驳回原因")
    parser.add_argument("--override", type=str, default=None, help="覆盖结果 correct|incorrect")
    parser.add_argument("--notes", type=str, default=None, help="确认备注")
    parser.add_argument("--report", action="store_true", default=False, help="生成 Markdown 验证报告")
    parser.add_argument("--expert", action="store_true", default=False, help="专家版报告 (默认小白版)")
    args = parser.parse_args()

    setup_logging()

    if args.risk:
        settings.risk_profile = args.risk

    if args.command == "quote":
        fetcher = SpotGoldFetcher()
        quote = fetcher.fetch_realtime_quote()
        print(f"现货黄金报价:")
        for k, v in quote.items():
            print(f"  {k}: {v}")

        # 积存金数据
        try:
            acc_fetcher = AccumulationGoldFetcher()
            acc_latest = acc_fetcher.fetch_latest()
            if not acc_latest.empty:
                acc_row = acc_latest.iloc[-1]
                print(f"\n积存金 (Au99.99 人民币/克):")
                print(f"  最新价: {acc_row['close']:.2f}")
                acc_premium = acc_fetcher.fetch_premium()
                if acc_premium.get("premium_pct"):
                    print(f"  相对现货溢价: {acc_premium['premium_pct']:+.2%}")
        except Exception as e:
            logger.warning(f"积存金数据获取失败: {e}")

    elif args.command == "scan":
        run_scan(days=args.days, deep=args.deep)

    elif args.command == "backtest":
        run_backtest(args)

    elif args.command == "journal":
        journal = TradeJournal()
        stats = journal.stats()
        print(f"交易统计:")
        for k, v in stats.items():
            print(f"  {k}: {v}")

        recent = journal.recent(5)
        if recent:
            print("\n最近交易:")
            for r in recent:
                status = "✓" if r.status == "closed" and r.pnl and r.pnl > 0 else "✗" if r.pnl and r.pnl < 0 else "○"
                print(f"  {status} {r.timestamp.strftime('%m-%d %H:%M')} {r.signal} @ {r.entry_price:.2f}")

    elif args.command == "proxy-install":
        mgr = get_proxy_manager()
        if mgr.binary:
            logger.info(f"mihomo 已安装: {mgr.binary}")
        else:
            logger.info("开始下载 mihomo 二进制...")
            if mgr.download_binary():
                logger.info("安装成功，可正常使用代理功能")
            else:
                logger.error("下载失败，请手动安装: https://github.com/MetaCubeX/mihomo/releases")

    elif args.command == "track":
        run_track(args)

    elif args.command == "review":
        run_review(args)

    elif args.command == "findings":
        run_findings(args)

    elif args.command == "scenario":
        run_scenario(args)

    elif args.command == "doctrine":
        run_doctrine(args)

    elif args.command == "analyze":
        run_analyze(args)

    elif args.command == "report":
        mode = "expert" if args.expert else "beginner"
        logger.info(f"生成{mode}版报告...")
        # 运行完整扫描收集数据
        gold_fetcher = SpotGoldFetcher()
        gold_df = gold_fetcher.fetch(days=30)
        current_price = gold_df["close"].iloc[-1]
        macro_fetcher = MacroDataFetcher()
        dxy_df = macro_fetcher.fetch_dxy()
        rate_df = macro_fetcher.fetch_real_rate()
        breakeven_df = macro_fetcher.fetch_breakeven()
        silver_df = macro_fetcher.fetch_silver()
        # 信号
        bundle = SignalBundle()
        for sig in TechnicalAnalyzer(gold_df).generate_signals():
            bundle.add(sig)
        for sig in FundamentalAnalyzer(gold_df, dxy_df, rate_df, silver_df, breakeven_df).generate_signals():
            bundle.add(sig)
        try:
            nf = NewsFetcher()
            items = nf.fetch_latest(max_results=6)
            items = nf.analyze_sentiment(items)
            for sig in NewsSignalGenerator().analyze(items):
                bundle.add(sig)
        except Exception:
            items = []
        # 网络不可达时用已知重要新闻兜底
        if not items:
            from datetime import datetime as dt
            from gold_miner.data.news import NewsItem
            today_str = dt.now().strftime("%m/%d")
            items = [
                NewsItem(title=f"美国非农就业新增17.2万，远超预期", source="Trading Economics",
                         published_at=dt.now(), sentiment=-0.5, is_breaking=True,
                         summary=f"美国5月非农就业新增17.2万人，远超市场预期的~12万人，失业率维持4.3%。强劲的就业数据削弱了美联储降息预期，导致黄金承压下跌。"),
                NewsItem(title=f"美伊和谈停滞，中东局势不确定性上升", source="CNA",
                         published_at=dt.now(), sentiment=0.2, is_breaking=True,
                         summary="美国与伊朗的和平谈判陷入僵局，市场避险情绪有所回升，但被强劲的非农数据盖过。"),
                NewsItem(title=f"黄金单日暴跌2.76%，贵金属全线重挫", source="Reuters",
                         published_at=dt.now(), sentiment=-0.4, is_breaking=True,
                         summary="现货黄金单日大跌2.76%至每克947.50元，白银跌8.8%，铂金跌6.9%，钯金跌7.7%。贵金属全线遭遇系统性抛售。"),
                NewsItem(title=f"全球央行Q1购金244吨，结构性支撑金价", source="世界黄金协会",
                         published_at=dt.now(), sentiment=0.5, is_breaking=False,
                         summary="全球央行Q1净购金244吨，同比增长3%。中国、波兰等国央行持续增持，为金价提供结构性支撑。"),
            ]
            items = NewsFetcher().analyze_sentiment(items)
            for sig in NewsSignalGenerator().analyze(items):
                bundle.add(sig)
            ScoringEngine().score(bundle)
        try:
            au_df = SentimentDataFetcher().fetch_au_futures(lookback=60)
            for sig in SentimentAnalyzer(au_df=au_df).generate_signals():
                bundle.add(sig)
        except Exception:
            au_df = None
        ScoringEngine().score(bundle)
        # 决策
        bull_opinion = BullAgent().analyze(bundle)
        bear_opinion = BearAgent().analyze(bundle)
        decision = PortfolioManager().decide(bull_opinion, bear_opinion, bundle, settings.risk_profile)
        final = RiskManager().apply_risk_controls(decision, RiskManager().check(decision))
        # 生成报告（报告内部自动处理英文翻译）
        gen = ReportGenerator(mode=mode)
        path = gen.generate(
            output_path=args.output or "",
            gold_df=gold_df, current_price=current_price, dxy_df=dxy_df,
            rate_df=rate_df, breakeven_df=breakeven_df, silver_df=silver_df,
            bundle=bundle, news_items=items, au_df=au_df,
            bull_confidence=bull_opinion.confidence, bear_confidence=bear_opinion.confidence,
            decision=decision, final_decision=final,
        )
        logger.info(f"报告已生成: {path}")

    elif args.command == "daemon":
        run_daemon(args)

    elif args.command == "verify":
        run_verify(args)


if __name__ == "__main__":
    main()
