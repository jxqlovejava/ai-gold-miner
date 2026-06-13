"""advisor CLI 命令 — 主动式战略投资顾问命令行接口.

命令:
  advisor daily          — 生成今日行动指令
  advisor alert          — 检查未来预警
  advisor today          — 检查今日重大事件
  advisor sentiment      — 扫描市场情绪
  advisor extreme        — 极端情景压力测试
  advisor ask "..."      — 投资咨询对话
  advisor full           — 全面体检（所有模块）
  advisor watch          — 监控模式（循环检查）

使用方式:
    gold-miner advisor daily --position 0.3 --cost 2300
    gold-miner advisor ask "美联储下周加息怎么办" --position 0.5
"""

from __future__ import annotations

from loguru import logger

from gold_miner.advisor.orchestrator import Advisor
from gold_miner.advisor.core import UserProfile


def register_advisor_parser(subparsers) -> None:
    """注册 advisor 子命令到主 CLI."""
    parser = subparsers.add_parser(
        "advisor",
        help="主动式战略投资顾问 — 预警/指令/咨询",
        description="""
主动式战略投资顾问
==================
从被动报告升级为：主动预警 + 行动指令 + 投资咨询

子命令:
  daily       生成今日行动指令（买卖/仓位/价位）
  alert       检查未来事件预警（未来7天）
  today       检查今日是否有重大事件
  sentiment   扫描市场情绪与节奏对齐
  extreme     极端情景压力测试
  ask         投资咨询对话（自然语言输入）
  full        全面体检（所有模块一次性运行）
  watch       监控模式（后台循环检查预警）

示例:
  gold-miner advisor daily --position 0.3 --cost 2300
  gold-miner advisor alert --days 14
  gold-miner advisor ask "现在该加仓吗" --position 0.2
  gold-miner advisor full --position 0.4
        """.strip(),
        formatter_class=lambda prog: __import__("argparse").RawDescriptionHelpFormatter(prog, width=80),
    )

    sub = parser.add_subparsers(dest="advisor_cmd")

    # --- daily ---
    p_daily = sub.add_parser("daily", help="生成今日行动指令")
    p_daily.add_argument("--position", type=float, default=0.0, help="当前仓位 0~1")
    p_daily.add_argument("--cost", type=float, default=0.0, help="持仓均价")
    p_daily.add_argument("--strategy", type=str, default=None, help="策略偏好: balanced|maximize_profit|cost_recovery|take_profit")
    p_daily.add_argument("--no-news", action="store_true", help="不包含新闻信号")
    p_daily.add_argument("--no-sentiment", action="store_true", help="不包含情绪信号")

    # --- alert ---
    p_alert = sub.add_parser("alert", help="检查未来事件预警")
    p_alert.add_argument("--days", type=int, default=7, help="向前扫描天数")

    # --- today ---
    sub.add_parser("today", help="检查今日重大事件")

    # --- sentiment ---
    sub.add_parser("sentiment", help="扫描市场情绪")

    # --- extreme ---
    p_extreme = sub.add_parser("extreme", help="极端情景压力测试")
    p_extreme.add_argument("--keyword", type=str, default=None, help="特定情景关键词（如 war, inflation）")
    p_extreme.add_argument("--position", type=float, default=0.0, help="当前仓位")

    # --- ask ---
    p_ask = sub.add_parser("ask", help="投资咨询对话")
    p_ask.add_argument("question", type=str, help="咨询问题")
    p_ask.add_argument("--position", type=float, default=0.0, help="当前仓位")
    p_ask.add_argument("--cost", type=float, default=0.0, help="持仓均价")

    # --- full ---
    p_full = sub.add_parser("full", help="全面体检")
    p_full.add_argument("--position", type=float, default=0.0, help="当前仓位")
    p_full.add_argument("--cost", type=float, default=0.0, help="持仓均价")

    # --- watch ---
    p_watch = sub.add_parser("watch", help="监控模式")
    p_watch.add_argument("--position", type=float, default=0.0, help="当前仓位")
    p_watch.add_argument("--cost", type=float, default=0.0, help="持仓均价")
    p_watch.add_argument("--interval", type=int, default=60, help="检查间隔（分钟）")
    p_watch.add_argument("--dry-run", action="store_true", help="运行一次后退出（测试用）")


def run_advisor(args) -> None:
    """执行 advisor 子命令."""
    advisor = Advisor()
    cmd = args.advisor_cmd

    if cmd == "daily":
        _run_daily(advisor, args)
    elif cmd == "alert":
        _run_alert(advisor, args)
    elif cmd == "today":
        _run_today(advisor, args)
    elif cmd == "sentiment":
        _run_sentiment(advisor, args)
    elif cmd == "extreme":
        _run_extreme(advisor, args)
    elif cmd == "ask":
        _run_ask(advisor, args)
    elif cmd == "full":
        _run_full(advisor, args)
    elif cmd == "watch":
        _run_watch(advisor, args)
    else:
        print("请指定子命令: daily|alert|today|sentiment|extreme|ask|full|watch")
        print("运行 'gold-miner advisor -h' 查看帮助")


def _run_daily(advisor: Advisor, args) -> None:
    """执行 daily 命令."""
    print("=" * 60)
    print("🎯 今日行动指令")
    print("=" * 60)

    report = advisor.daily_guide(
        current_position_pct=args.position,
        avg_cost=args.cost,
        strategy_preference=args.strategy,
    )
    print(report.to_markdown())


def _run_alert(advisor: Advisor, args) -> None:
    """执行 alert 命令."""
    print("=" * 60)
    print(f"🔔 未来 {args.days} 天事件预警")
    print("=" * 60)

    report = advisor.check_alerts(days_ahead=args.days)
    print(report.to_markdown())


def _run_today(advisor: Advisor, args) -> None:
    """执行 today 命令."""
    print("=" * 60)
    print("📅 今日重大事件")
    print("=" * 60)

    report = advisor.check_today_events()
    print(report.to_markdown())


def _run_sentiment(advisor: Advisor, args) -> None:
    """执行 sentiment 命令."""
    print("=" * 60)
    print("🧠 市场情绪扫描")
    print("=" * 60)

    report = advisor.sentiment_scan()
    print(report.to_markdown())


def _run_extreme(advisor: Advisor, args) -> None:
    """执行 extreme 命令."""
    print("=" * 60)
    print("⚡ 极端情景压力测试")
    print("=" * 60)

    report = advisor.extreme_check(
        keyword=args.keyword,
        current_position_pct=args.position,
    )
    print(report.to_markdown())


def _run_ask(advisor: Advisor, args) -> None:
    """执行 ask 命令."""
    print("=" * 60)
    print("💬 投资咨询")
    print("=" * 60)
    print(f"问题: {args.question}")
    print("-" * 60)

    report = advisor.consult(
        question=args.question,
        current_position_pct=args.position,
        avg_cost=args.cost,
    )
    print(report.to_markdown())


def _run_full(advisor: Advisor, args) -> None:
    """执行 full 命令."""
    print("=" * 60)
    print("🏥 全面体检")
    print("=" * 60)

    reports = advisor.full_check(
        current_position_pct=args.position,
        avg_cost=args.cost,
    )

    for i, report in enumerate(reports, 1):
        print(f"\n{'─' * 60}")
        print(f"【{i}/{len(reports)}】{report.report_type}")
        print(f"{'─' * 60}")
        print(report.to_markdown())

    print(f"\n{'=' * 60}")
    print(f"✅ 全面体检完成，共 {len(reports)} 份报告")
    print(f"{'=' * 60}")


def _run_watch(advisor: Advisor, args) -> None:
    """执行 watch 命令."""
    if args.dry_run:
        print("=" * 60)
        print("👁️  监控模式（测试运行）")
        print("=" * 60)

        # 运行一次所有检查
        reports = [
            advisor.check_today_events(),
            advisor.check_alerts(days_ahead=3),
            advisor.sentiment_scan(),
        ]

        for report in reports:
            if report.alerts or report.warnings:
                print(report.to_markdown())
                print()

        print("✅ 测试运行完成")
        return

    print("=" * 60)
    print(f"👁️  启动监控模式（间隔 {args.interval} 分钟）")
    print("按 Ctrl+C 停止")
    print("=" * 60)

    advisor.watch_mode(
        current_position_pct=args.position,
        avg_cost=args.cost,
        interval_minutes=args.interval,
    )
