"""Scan command handler."""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

from loguru import logger

from gold_miner.config import settings
from gold_miner.pipeline.analysis import AnalysisContext, AnalysisPipeline


class _ReportTee:
    """同时写原 stdout 与报告文件的流代理.

    scan 的报告主体（信号表格/军规/Agent博弈等）走 print → sys.stdout，
    loguru 过程日志走 stderr。本类只 tee stdout，保证控制台实时输出
    （输出铁律）+ 文件完整留档两不误。
    """

    def __init__(self, stream: object, report: object) -> None:
        self._stream = stream
        self._report = report

    def write(self, data: str) -> int:
        self._stream.write(data)
        self._report.write(data)
        return len(data)

    def flush(self) -> None:
        self._stream.flush()
        self._report.flush()

    def __getattr__(self, name: str) -> object:
        return getattr(self._stream, name)


def _run_with_report(run, report_file: str | None):
    """在 report_file 上 tee stdout 执行 run()；report_file 为空则原样运行."""
    if not report_file:
        return run()
    report_path = Path(report_file)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        tee = _ReportTee(sys.stdout, f)
        try:
            with contextlib.redirect_stdout(tee):
                return run()
        finally:
            tee.flush()


def run_scan(days: int = 30, with_news: bool = True, with_sentiment: bool = True, deep: bool = False,
             report_file: str | None = None) -> None:
    """运行完整扫描流程 — 委托给 AnalysisPipeline.

    Args:
        days: 回溯天数
        with_news: 启用新闻分析
        with_sentiment: 启用情绪分析
        deep: 使用 LLM 深度分析
        report_file: 非空时把完整报告（print 输出）tee 到该文件，同时保留控制台输出
    """
    if settings.demo_mode:
        logger.info("[Demo 模式] 关闭新闻/情绪/Polymarket/LLM 深度分析")
        with_news = False
        with_sentiment = False
        deep = False

    logger.info("=" * 50)
    logger.info("开始黄金投资决策扫描")
    if report_file:
        logger.info(f"报告将保存到: {report_file}")
    logger.info("=" * 50)

    def _do_scan():
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
        return result

    result = _run_with_report(_do_scan, report_file)
    if result is not None:
        logger.info(f"报告已保存: {report_file}") if report_file else None
