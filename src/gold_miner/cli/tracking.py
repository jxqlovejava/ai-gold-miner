"""Tracking commands: track, review, findings."""

from __future__ import annotations

import argparse
import uuid
from dataclasses import asdict
from datetime import datetime

from loguru import logger

from gold_miner.data.macro import MacroDataFetcher
from gold_miner.data.spot_gold import SpotGoldFetcher
from gold_miner.improvement.analyzer import PerformanceAnalyzer
from gold_miner.improvement.findings import FindingGenerator
from gold_miner.improvement.tracker import PredictionRecord, PredictionTracker
from gold_miner.signals.base import SignalBundle
from gold_miner.signals.engine import ScoringEngine
from gold_miner.signals.fundamental import FundamentalAnalyzer
from gold_miner.signals.technical import TechnicalAnalyzer


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
