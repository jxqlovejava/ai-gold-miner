"""Daemon command handler."""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta

from loguru import logger

from gold_miner.cli.scan import run_scan
from gold_miner.config import settings


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
