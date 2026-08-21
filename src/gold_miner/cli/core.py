"""CLI core — argparse setup and main dispatcher."""

from __future__ import annotations

import argparse

from loguru import logger as log

from gold_miner.config import settings

# 懒加载: 命令模块按需导入。此前 16 个 run_* 全部顶层 import, 导致
# gold-miner scan 也要连带加载 analysis/web/scenario/long_term 重型链
# (pandas/bs4/pydantic_settings 等, 实测 CLI 启动 ~4s, 服务器更甚)。
# 改为 PEP 562 __getattr__ 惰性导入, 首次访问 run_* 才加载对应模块。
_LAZY_COMMANDS: dict[str, str] = {
    "run_analyze": ".analysis",
    "run_backtest": ".backtest",
    "run_daemon": ".daemon",
    "run_doctrine": ".doctrine",
    "run_journal": ".journal",
    "run_longterm": ".long_term",
    "run_prepare": ".prepare",
    "run_proxy_install": ".proxy_install",
    "run_quote": ".quote",
    "run_record": ".record",
    "run_report": ".report",
    "run_scan": ".scan",
    "run_scenario": ".scenario",
    "run_findings": ".tracking",
    "run_review": ".tracking",
    "run_track": ".tracking",
    "run_doctor_wrapper": ".verify",
    "run_setup_wrapper": ".verify",
    "run_verify_wrapper": ".verify",
    "run_web": ".web",
}


def _resolve(name: str):
    """惰性获取命令函数: 优先读已存在的模块属性(兼容测试 monkeypatch), 否则按需导入.

    说明: 模块内函数体引用全局名走 LOAD_GLOBAL, 不会触发模块 __getattr__,
    因此用显式 _resolve() 而非 PEP 562。首次真实调用后缓存为模块属性, 后续直接命中。
    """
    fn = globals().get(name)
    if fn is not None:
        return fn
    import importlib

    fn = getattr(
        importlib.import_module(_LAZY_COMMANDS[name], package=__package__), name
    )
    globals()[name] = fn  # 缓存, 避免下次重复导入
    return fn


def setup_logging() -> None:
    """配置日志."""
    log.remove()
    log.add(
        lambda msg: print(msg, end=""),
        level=settings.log_level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="黄金投资决策辅助系统")
    parser.add_argument(
        "command",
        choices=[
            "scan", "prepare", "quote", "backtest", "journal", "proxy-install",
            "track", "review", "findings", "analyze", "scenario", "doctrine", "daemon",
            "verify", "report", "advisor", "doctor", "setup", "web",
            "longterm", "record",
        ],
        help="命令",
    )
    parser.add_argument("--demo", action="store_true", default=False, help="Demo 模式：跳过需要 API key 的功能")
    parser.add_argument("--port", type=int, default=8501, help="Web 仪表盘端口 (web 命令)")
    parser.add_argument("--days", type=int, default=365, help="回溯天数")
    parser.add_argument("--news", action="store_true", help="启用新闻分析（需配置API key）")
    parser.add_argument("--sentiment", action="store_true", help="启用情绪分析（COT/ETF数据）")
    parser.add_argument("--risk", choices=["aggressive", "moderate", "conservative"],
                        default=None, help="风险偏好")
    parser.add_argument("--capital", type=float, default=None, help="初始资金（回测用）")
    parser.add_argument("--output", type=str, default=None, help="权益曲线CSV输出路径（回测用）")
    parser.add_argument("--behavior", action="store_true", default=False,
                        help="行为回测模式：对比 AI 建议 vs 实际交易 (backtest)")
    parser.add_argument("--price", type=float, default=None, help="当前/实际价格 (track 命令)")
    parser.add_argument("--resolve-id", type=str, default=None, help="要结算的预测ID (track 命令)")
    parser.add_argument("--list", action="store_true", default=False, help="列出预测记录 (track --list)")
    # analyze 命令参数
    parser.add_argument("--url", type=str, default=None, help="文章URL (analyze)")
    parser.add_argument("--text", type=str, default=None, help="文章/情景文本 (analyze/scenario)")
    parser.add_argument("--show", type=str, default=None, help="查看文章详情 (analyze --show <id>)")
    parser.add_argument("--update", type=str, default=None, help="更新记录 (analyze --update <id>)")
    parser.add_argument("--llm-analysis", type=str, default=None, help="LLM分析JSON (analyze --update)")
    parser.add_argument("--cross-ref", type=str, default=None, help="交叉验证JSON (analyze --update)")
    parser.add_argument("--predict", type=str, default=None, help="生成预判 (analyze --predict <id>)")
    parser.add_argument("--direction", type=str, default=None, help="预判方向 bullish|bearish|neutral")
    parser.add_argument("--confidence", type=float, default=None, help="预判置信度 0.0-1.0")
    parser.add_argument("--horizon", type=int, default=7, help="预判时间窗口天数 / 中长期分析月数 (longterm)")
    parser.add_argument("--target-pct", type=float, default=None, help="预期涨跌幅")
    parser.add_argument("--reasoning", type=str, default=None, help="预判推理链")
    parser.add_argument("--deep", action="store_true", default=False, help="使用LLM深度分析文章 (analyze)")
    parser.add_argument("--report-file", type=str, default=None,
                        help="scan 报告完整输出保存到文件（Tee：同时保留控制台实时输出）")
    # daemon 命令参数
    parser.add_argument("--interval", type=int, default=60, help="扫描间隔(分钟)")
    parser.add_argument("--once", action="store_true", default=False, help="仅执行一次")
    # scenario 命令参数
    parser.add_argument("--save", action="store_true", default=False, help="保存情景报告 (scenario --save)")
    parser.add_argument("--track", action="store_true", default=False, help="关联预测追踪 (scenario --track)")
    # doctrine 命令参数
    parser.add_argument("--check", action="store_true", default=False, help="运行军规审查 (doctrine --check)")
    parser.add_argument("--toggle", type=str, default=None, help="启用/禁用规则 (doctrine --toggle <rule_id>)")
    parser.add_argument("--type", type=str, default=None, help="列出类型: rules/strategies/models (doctrine --list --type)")
    parser.add_argument("--dims", type=str, default=None, help="活跃维度 (doctrine --check --dims technical,fundamental)")
    parser.add_argument("--change", type=float, default=None, help="模拟日波动百分比 doctrine --check")
    parser.add_argument("--data-event", action="store_true", default=False, help="模拟重大数据前 (doctrine --check)")
    parser.add_argument("--search", type=str, default=None, help="搜索Munger模型库 (doctrine --search <关键词>)")
    parser.add_argument("--discipline", type=str, default=None, help="按学科筛选Munger模型 (doctrine --discipline invest)")
    # verify 命令参数
    parser.add_argument("--id", type=str, default=None, help="查看预测详情 (verify --id <ID>)")
    parser.add_argument("--confirm", type=str, default=None, help="人工确认结算 (verify --confirm <ID>)")
    parser.add_argument("--reject", type=str, default=None, help="无效化预测 (verify --reject <ID>)")
    parser.add_argument("--reason", type=str, default=None, help="无效化/驳回原因")
    parser.add_argument("--override", type=str, default=None, help="覆盖结果 correct|incorrect")
    parser.add_argument("--notes", type=str, default=None, help="确认备注")
    parser.add_argument("--report", action="store_true", default=False, help="生成 Markdown 验证报告")
    parser.add_argument("--expert", action="store_true", default=False, help="专家版报告 (默认小白版)")
    # advisor 命令参数
    parser.add_argument("--position", type=float, default=0.0, help="当前仓位 0~1 (advisor)")
    parser.add_argument("--cost", type=float, default=0.0, help="持仓均价 (advisor)")
    parser.add_argument("--strategy-pref", type=str, default=None, help="策略偏好 balanced|maximize_profit|cost_recovery|take_profit advisor")
    parser.add_argument("--question", type=str, default=None, help="咨询问题 advisor ask")
    parser.add_argument("--watch-interval", type=int, default=60, help="监控间隔分钟 advisor watch")
    parser.add_argument("--dry-run", action="store_true", default=False, help="测试运行 advisor watch")
    # setup command parameters
    parser.add_argument("--non-interactive", action="store_true", default=False, help="非交互模式 setup")
    args, unknown = parser.parse_known_args()

    setup_logging()

    if args.risk:
        settings.risk_profile = args.risk

    if args.demo:
        settings.demo_mode = True
        log.info("[Demo 模式] 已启用：跳过新闻/情绪/Polymarket 等需要 API key 的功能")

    if args.command == "prepare":
        _resolve("run_prepare")()
    elif args.command == "quote":
        _resolve("run_quote")()
    elif args.command == "scan":
        _resolve("run_scan")(
            days=args.days,
            with_news=args.news,
            with_sentiment=args.sentiment,
            deep=args.deep,
            report_file=args.report_file,
        )
    elif args.command == "backtest":
        _resolve("run_backtest")(args)
    elif args.command == "journal":
        _resolve("run_journal")()
    elif args.command == "proxy-install":
        _resolve("run_proxy_install")()
    elif args.command == "track":
        _resolve("run_track")(args)
    elif args.command == "review":
        _resolve("run_review")(args)
    elif args.command == "findings":
        _resolve("run_findings")(args)
    elif args.command == "scenario":
        _resolve("run_scenario")(args)
    elif args.command == "doctrine":
        _resolve("run_doctrine")(args)
    elif args.command == "analyze":
        _resolve("run_analyze")(args)
    elif args.command == "report":
        _resolve("run_report")(args)
    elif args.command == "daemon":
        _resolve("run_daemon")(args)
    elif args.command == "verify":
        _resolve("run_verify_wrapper")(args)
    elif args.command == "advisor":
        if args.question:
            from gold_miner.advisor.orchestrator import Advisor
            advisor = Advisor()
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
        else:
            from gold_miner.advisor.action_guide import run_pipeline_and_report
            print("=" * 60)
            print("🎯 今日行动指令")
            print("=" * 60)
            report = run_pipeline_and_report(
                current_position_pct=args.position,
                avg_cost=args.cost,
            )
            print(report.to_markdown())
    elif args.command == "doctor":
        _resolve("run_doctor_wrapper")()
    elif args.command == "setup":
        _resolve("run_setup_wrapper")(args)
    elif args.command == "web":
        _resolve("run_web")(args)
    elif args.command == "longterm":
        _resolve("run_longterm")(args)
    elif args.command == "record":
        _resolve("run_record")(args)
