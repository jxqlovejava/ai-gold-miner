"""Agent CLI — 主动式Agent命令行入口.

用法:
    gold-miner agent start         启动主动Agent (调度+通知)
    gold-miner agent briefing      手动触发一次全流程简报
    gold-miner agent pre-market    盘前简报
    gold-miner agent post-open     开盘分析
    gold-miner agent closing       尾盘提醒
    gold-miner agent events        事件扫描
    gold-miner agent backtest      运行回测
    gold-miner agent status        查看运行状态
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime


def cmd_start(_args: argparse.Namespace) -> None:
    """启动主动Agent调度循环."""
    from gold_miner.agent.proactive import ProactiveAgent
    agent = ProactiveAgent()
    print("启动主动Agent...")
    print("按 Ctrl+C 停止")
    try:
        agent.start()
    except KeyboardInterrupt:
        agent.stop()
        print("\nAgent已停止")


def cmd_briefing(_args: argparse.Namespace) -> None:
    """手动触发完整分析周期."""
    from gold_miner.agent.proactive import ProactiveAgent
    agent = ProactiveAgent()
    briefings = agent.run_full_cycle()
    for b in briefings:
        print(b.to_markdown())
        print("\n---\n")


def cmd_single(kind: str) -> None:
    """触发单个简报."""
    from gold_miner.agent.proactive import ProactiveAgent
    agent = ProactiveAgent()
    b = agent.run_once(kind)
    if b:
        print(b.to_markdown())
    else:
        print(f"[{kind}] 简报生成失败")


def cmd_pre_market(_args: argparse.Namespace) -> None:
    cmd_single("pre_market")


def cmd_post_open(_args: argparse.Namespace) -> None:
    cmd_single("post_open")


def cmd_closing(_args: argparse.Namespace) -> None:
    cmd_single("closing")


def cmd_events(_args: argparse.Namespace) -> None:
    cmd_single("event_scan")


def cmd_backtest(args: argparse.Namespace) -> None:
    """运行回测."""
    from gold_miner.agent.backtest import BacktestEngine
    from gold_miner.data.spot_gold import SpotGoldFetcher

    engine = BacktestEngine()
    print("获取历史金价数据...")
    fetcher = SpotGoldFetcher()
    df = fetcher.fetch(days=args.days)

    if df.empty:
        print("无法获取金价数据")
        return

    print(f"数据: {len(df)}条 ({df.iloc[0].get('timestamp', '?')} → {df.iloc[-1].get('timestamp', '?')})")

    # 买入持有基准
    bh = engine.run_buy_and_hold(df)
    print(f"\n{bh.summary()}")

    # MA金叉死叉
    ma = engine.run_ma_crossover(df, fast=5, slow=20)
    print(f"\n{ma.summary()}")

    # RSI策略
    rsi = engine.run_rsi_strategy(df)
    print(f"\n{rsi.summary()}")

    # 信号验证
    results = engine.validate_signals(df, lookahead_days=args.lookahead)
    print(f"\n信号预测准确率 ({args.lookahead}日后):")
    for sig, acc in results.items():
        print(f"  {sig}: {acc:.1%}")

    # 保存
    if args.save:
        path = engine.save(ma)
        print(f"\n回测结果已保存: {path}")


def cmd_status(_args: argparse.Namespace) -> None:
    """查看系统状态."""
    from gold_miner.config import settings
    from gold_miner.agent.portfolio import PortfolioTracker
    from gold_miner.agent.briefer import Briefer

    print("=== 系统状态 ===")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"时区: {settings.agent_timezone}")
    print(f"主动Agent: {'启用' if settings.agent_enabled else '未启用'}")

    # 持仓
    try:
        tracker = PortfolioTracker()
        briefer = Briefer()
        price, _ = briefer._fetch_domestic()
        if price > 0:
            print(f"\n{tracker.risk_summary(price)}")
    except Exception as e:
        print(f"持仓加载失败: {e}")

    # 调度表
    print(f"\n调度任务:")
    print(f"  {settings.agent_schedule_pre_market} — 盘前简报")
    print(f"  {settings.agent_schedule_post_open} — 开盘分析")
    print(f"  {settings.agent_schedule_closing} — 尾盘提醒")
    print(f"  {settings.agent_schedule_event_scan} — 事件扫描")

    # 通知
    print(f"\n通知: {'企业微信' if settings.wechat_webhook_url else '未配置'}")
    print(f"数据目录: {settings.data_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="主动式黄金投资Agent")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("start", help="启动主动Agent调度循环")
    sub.add_parser("briefing", help="手动触发完整分析周期")
    sub.add_parser("pre-market", help="盘前简报")
    sub.add_parser("post-open", help="开盘分析")
    sub.add_parser("closing", help="尾盘提醒")
    sub.add_parser("events", help="事件扫描")

    bt = sub.add_parser("backtest", help="运行历史回测")
    bt.add_argument("--days", type=int, default=365, help="回测天数 (默认365)")
    bt.add_argument("--lookahead", type=int, default=5, help="信号验证前瞻天数")
    bt.add_argument("--save", action="store_true", help="保存回测结果")

    sub.add_parser("status", help="查看系统状态")

    args = parser.parse_args()

    commands = {
        "start": cmd_start,
        "briefing": cmd_briefing,
        "pre-market": cmd_pre_market,
        "post-open": cmd_post_open,
        "closing": cmd_closing,
        "events": cmd_events,
        "backtest": cmd_backtest,
        "status": cmd_status,
    }

    fn = commands.get(args.command)
    if fn:
        fn(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
