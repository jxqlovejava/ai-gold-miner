"""Scan command handler."""

from __future__ import annotations

from loguru import logger

from gold_miner.config import settings
from gold_miner.pipeline.analysis import AnalysisContext, AnalysisPipeline


def run_scan(days: int = 30, with_news: bool = True, with_sentiment: bool = True, deep: bool = False) -> None:
    """运行完整扫描流程 — 委托给 AnalysisPipeline."""
    if settings.demo_mode:
        logger.info("[Demo 模式] 关闭新闻/情绪/Polymarket/LLM 深度分析")
        with_news = False
        with_sentiment = False
        deep = False

    logger.info("=" * 50)
    logger.info("开始黄金投资决策扫描")
    logger.info("=" * 50)

    ctx = AnalysisContext(
        days=days,
        with_news=with_news,
        with_sentiment=with_sentiment,
        deep=deep,
        risk_profile=settings.risk_profile,
    )
    pipeline = AnalysisPipeline()
    result = pipeline.run(ctx)

    if result.gold_df.empty:
        logger.error("扫描失败: 无法获取金价数据")
        return

    logger.info("扫描完成")
