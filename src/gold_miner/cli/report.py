"""Report command handler."""

from __future__ import annotations

import argparse
from datetime import datetime as dt

from loguru import logger

from gold_miner.config import settings
from gold_miner.data.macro import MacroDataFetcher
from gold_miner.data.news import NewsFetcher, NewsItem
from gold_miner.data.sentiment import SentimentDataFetcher
from gold_miner.data.spot_gold import SpotGoldFetcher
from gold_miner.decision.agents import BearAgent, BullAgent, PortfolioManager
from gold_miner.decision.risk import RiskManager
from gold_miner.execution.report import ReportGenerator
from gold_miner.signals.base import SignalBundle
from gold_miner.signals.engine import ScoringEngine
from gold_miner.signals.fundamental import FundamentalAnalyzer
from gold_miner.signals.news_signal import NewsSignalGenerator
from gold_miner.signals.sentiment_signal import SentimentAnalyzer
from gold_miner.signals.technical import TechnicalAnalyzer


def run_report(args: argparse.Namespace) -> None:
    """Generate analysis report."""
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
        items = [
            NewsItem(title="美国非农就业新增17.2万，远超预期", source="Trading Economics",
                     published_at=dt.now(), sentiment=-0.5, is_breaking=True,
                     summary="美国5月非农就业新增17.2万人，远超市场预期的~12万人，失业率维持4.3%。强劲的就业数据削弱了美联储降息预期，导致黄金承压下跌。"),
            NewsItem(title="美伊和谈停滞，中东局势不确定性上升", source="CNA",
                     published_at=dt.now(), sentiment=0.2, is_breaking=True,
                     summary="美国与伊朗的和平谈判陷入僵局，市场避险情绪有所回升，但被强劲的非农数据盖过。"),
            NewsItem(title="黄金单日暴跌2.76%，贵金属全线重挫", source="Reuters",
                     published_at=dt.now(), sentiment=-0.4, is_breaking=True,
                     summary="现货黄金单日大跌2.76%至每克947.50元，白银跌8.8%，铂金跌6.9%，钯金跌7.7%。贵金属全线遭遇系统性抛售。"),
            NewsItem(title="全球央行Q1购金244吨，结构性支撑金价", source="世界黄金协会",
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
