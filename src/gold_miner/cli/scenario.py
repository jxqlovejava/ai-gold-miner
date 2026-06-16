"""Scenario command handler."""

from __future__ import annotations

import argparse
from typing import Any

from loguru import logger

from gold_miner.data.macro import MacroDataFetcher
from gold_miner.data.spot_gold import SpotGoldFetcher
from gold_miner.scenarios.analyzer import ScenarioAnalyzer
from gold_miner.scenarios.models import ScenarioReport
from gold_miner.scenarios.store import ScenarioStore


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
    print("\n提示: 使用 --save 保存报告, --track 关联预测追踪以跟踪预判准确率")
    print("  gold-miner scenario --text \"...\" --save --track --horizon 24")


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
            print("  对冲建议:")
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
