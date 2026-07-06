"""Backtest command handler."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from loguru import logger

from gold_miner.backtest.behavior import (
    BehavioralBacktestEngine,
    print_behavioral_report,
    save_behavioral_report,
)
from gold_miner.backtest.engine import BacktestEngine
from gold_miner.config import settings
from gold_miner.data.macro import MacroDataFetcher
from gold_miner.data.spot_gold import SpotGoldFetcher
from gold_miner.signals.base import SignalBundle
from gold_miner.signals.engine import ScoringEngine
from gold_miner.signals.fundamental import FundamentalAnalyzer
from gold_miner.signals.technical import TechnicalAnalyzer


def run_backtest(args: argparse.Namespace) -> None:
    """运行历史回测."""

    # 行为回测分支
    if getattr(args, "behavior", False):
        _run_behavioral_backtest(args)
        return

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


def _run_behavioral_backtest(args: argparse.Namespace) -> None:
    """运行行为回测: AI 建议 vs 实际交易."""
    logger.info("=" * 50)
    logger.info("开始行为回测 — AI建议 vs 实际交易")
    logger.info("=" * 50)

    capital = args.capital or settings.initial_capital_usd
    engine = BehavioralBacktestEngine(initial_capital=capital)
    result = engine.run()

    print_behavioral_report(result)

    if args.output:
        save_behavioral_report(result, args.output)
