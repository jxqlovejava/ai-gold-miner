"""Prepare command — Stage 1 of the 9-step pipeline: calendar validation, event sync, deep news."""

from __future__ import annotations

from loguru import logger

from gold_miner.pipeline.analysis import AnalysisContext, AnalysisPipeline


def run_prepare() -> None:
    """仅执行管线 Step 1: 日历校验 + 事件同步 + 深度新闻搜索."""
    logger.info("=" * 50)
    logger.info("gold-miner prepare — 信息准备")
    logger.info("=" * 50)

    ctx = AnalysisContext()
    pipeline = AnalysisPipeline()

    # 执行 Step 1
    pipeline._step_prepare(ctx, pipeline._make_result())

    logger.info("准备完成. 下一步: gold-miner scan")


# Alias for scan's internal use
def run_prepare_only(days: int = 30) -> AnalysisPipeline:
    """Returns pipeline with Step 1 executed, ready for remaining steps."""
    ctx = AnalysisContext(days=days)
    pipeline = AnalysisPipeline()
    pipeline._step_prepare(ctx, pipeline._make_result())
    return pipeline
