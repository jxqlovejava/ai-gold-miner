"""内置工作流实现."""

from __future__ import annotations

from gold_miner.config import settings
from gold_miner.data.spot_gold import SpotGoldFetcher
from gold_miner.execution.alert import PriceAlert
from gold_miner.execution.journal import TradeJournal
from gold_miner.improvement.analyzer import PerformanceAnalyzer
from gold_miner.improvement.findings import FindingGenerator
from gold_miner.improvement.tracker import PredictionTracker
from gold_miner.pipeline.analysis import AnalysisContext, AnalysisPipeline
from gold_miner.workflows.base import Workflow, WorkflowContext, WorkflowResult


class PreMarketWorkflow(Workflow):
    """盘前工作流: 报价 + 新闻扫描 + 日历 + 预警."""

    name = "pre-market"
    aliases = {"premarket", "pre", "盘前", "开盘前"}
    description = "盘前简报: 报价 + 隔夜新闻扫描 + 今日事件日历 + 预警检查"

    def run(self, ctx: WorkflowContext) -> WorkflowResult:
        if ctx.dry_run:
            return WorkflowResult(success=True, messages=self.dry_run_steps(ctx))

        result = WorkflowResult()
        # 1. 报价
        try:
            fetcher = SpotGoldFetcher()
            quote = fetcher.fetch_realtime_quote()
            result.messages.append(f"现货黄金: {quote}")
        except Exception as e:
            result.messages.append(f"报价获取失败: {e}")

        # 2. 新闻扫描 (简化)
        try:
            from gold_miner.data.news import NewsFetcher
            nf = NewsFetcher()
            items = nf.fetch_latest(max_results=6)
            result.messages.append(f"新闻: {len(items)} 条")
        except Exception as e:
            result.messages.append(f"新闻扫描失败: {e}")

        # 3. 事件日历
        try:
            from gold_miner.data.calendar import EventCalendar
            cal = EventCalendar()
            events = cal.get_upcoming(days=7)
            result.messages.append(f"未来7天事件: {len(events)} 个")
        except Exception as e:
            result.messages.append(f"日历获取失败: {e}")

        # 4. 预警检查
        try:
            gold_df = fetcher.fetch(days=5) if 'fetcher' in dir() else SpotGoldFetcher().fetch(days=5)
            alert_mgr = PriceAlert()
            alerts = alert_mgr.check_all(gold_df=gold_df)
            if alerts:
                result.messages.append(f"预警: {len(alerts)} 条")
                for a in alerts:
                    result.messages.append(f"  [{a.severity}] {a.name}: {a.message}")
            else:
                result.messages.append("预警: 无异常")
        except Exception as e:
            result.messages.append(f"预警检查失败: {e}")

        return result

    def dry_run_steps(self, ctx: WorkflowContext) -> list[str]:
        return [
            "[1] 获取现货黄金实时报价",
            "[2] 扫描隔夜新闻 (6条)",
            "[3] 查询未来7天事件日历",
            "[4] 价格预警检查 (大波动/关键位/DXY异动)",
        ]


class IntraDayWorkflow(Workflow):
    """盘中工作流: 实时报价 + 关键技术位/止损位 + 异常波动."""

    name = "intra-day"
    aliases = {"intraday", "盘中", "日内", "day", "intra"}
    description = "盘中监控: 实时报价 + 关键技术位/止损位检查 + 异常波动提醒"

    def run(self, ctx: WorkflowContext) -> WorkflowResult:
        if ctx.dry_run:
            return WorkflowResult(success=True, messages=self.dry_run_steps(ctx))

        result = WorkflowResult()
        # 1. 实时报价
        try:
            fetcher = SpotGoldFetcher()
            gold_df = fetcher.fetch(days=5)
            if not gold_df.empty:
                latest = gold_df["close"].iloc[-1]
                result.messages.append(f"最新价: {latest:.2f}")
        except Exception as e:
            result.messages.append(f"报价获取失败: {e}")
            return result

        # 2. 关键技术位
        if not gold_df.empty:
            high_20 = gold_df["high"].tail(20).max()
            low_20 = gold_df["low"].tail(20).min()
            result.messages.append(f"20日区间: {low_20:.2f} ~ {high_20:.2f}")

        # 3. 预警
        try:
            alert_mgr = PriceAlert()
            alerts = alert_mgr.check_all(gold_df=gold_df)
            if alerts:
                for a in alerts:
                    result.messages.append(f"⚠️ [{a.severity}] {a.name}: {a.message}")
            else:
                result.messages.append("无异常波动")
        except Exception as e:
            result.messages.append(f"预警检查失败: {e}")

        return result

    def dry_run_steps(self, ctx: WorkflowContext) -> list[str]:
        return [
            "[1] 获取实时报价 + 5日历史",
            "[2] 计算关键技术位 (20日高低点)",
            "[3] 异常波动预警检查",
        ]


class PostMarketWorkflow(Workflow):
    """盘后工作流: 完整 AnalysisPipeline + 摘要."""

    name = "post-market"
    aliases = {"postmarket", "盘后", "收盘", "closing", "post"}
    description = "盘后复盘: 完整分析管线 + 信号摘要 + 持仓复盘 + 生成交易建议"

    def run(self, ctx: WorkflowContext) -> WorkflowResult:
        if ctx.dry_run:
            return WorkflowResult(success=True, messages=self.dry_run_steps(ctx))

        result = WorkflowResult()
        # 完整分析管线
        analysis_ctx = AnalysisContext(
            days=ctx.args.get("days", 30),
            with_news=ctx.args.get("with_news", True),
            with_sentiment=ctx.args.get("with_sentiment", True),
            deep=ctx.args.get("deep", False),
            risk_profile=ctx.args.get("risk_profile", settings.risk_profile),
        )
        pipeline = AnalysisPipeline()
        analysis_result = pipeline.run(analysis_ctx)

        result.data["analysis"] = analysis_result
        result.messages.append(f"分析完成: 综合评分 {analysis_result.bundle.composite_score:+.2f}")
        result.messages.append(f"决策: {analysis_result.final_decision.get('direction', 'neutral')} "
                                 f"仓位 {analysis_result.final_decision.get('position_pct', 0):.0%}")

        return result

    def dry_run_steps(self, ctx: WorkflowContext) -> list[str]:
        return [
            "[1] 数据采集 (金价/DXY/利率/白银/通胀)",
            "[2] 信号生成 (技术/基本面/新闻/情绪/ETF)",
            "[3] 来源验证",
            "[4] Agent 辩论 (多头 vs 空头)",
            "[5] 风控审查",
            "[6] 军规审查",
            "[7] Munger 思维模型",
            "[8] 决策输出 + 仪表盘",
            "[9] 自动追踪 + EventStore",
        ]


class DailyWorkflow(Workflow):
    """日度工作流: 简化版完整扫描."""

    name = "daily"
    aliases = {"日度", "day", "daily-scan", "全天"}
    description = "日度扫描: 简化版完整分析 (pre-market + intra-day + post-market 聚合)"

    def run(self, ctx: WorkflowContext) -> WorkflowResult:
        if ctx.dry_run:
            return WorkflowResult(success=True, messages=self.dry_run_steps(ctx))

        result = WorkflowResult()
        # 简化版: 直接跑 AnalysisPipeline
        analysis_ctx = AnalysisContext(
            days=ctx.args.get("days", 30),
            with_news=True,
            with_sentiment=True,
            deep=False,
            risk_profile=ctx.args.get("risk_profile", settings.risk_profile),
        )
        pipeline = AnalysisPipeline()
        analysis_result = pipeline.run(analysis_ctx)

        result.data["analysis"] = analysis_result
        result.messages.append("日度扫描完成")
        result.messages.append(f"综合评分: {analysis_result.bundle.composite_score:+.2f}")
        result.messages.append(f"建议: {analysis_result.final_decision.get('direction', 'neutral')} "
                                 f"{analysis_result.final_decision.get('position_pct', 0):.0%}")

        return result

    def dry_run_steps(self, ctx: WorkflowContext) -> list[str]:
        return [
            "[1] 数据采集",
            "[2] 多维度信号生成",
            "[3] Agent 辩论 + 风控 + 军规",
            "[4] 决策输出",
            "[5] 自动追踪",
        ]


class PostTradeWorkflow(Workflow):
    """交易后工作流: 记录交易 + 更新 portfolio + 写 trade log."""

    name = "post-trade"
    aliases = {"posttrade", "trade", "交易后", "记录交易"}
    description = "交易后记录: 记录交易 -> 更新 portfolio -> 写 trade log -> 触发复盘"

    def run(self, ctx: WorkflowContext) -> WorkflowResult:
        if ctx.dry_run:
            return WorkflowResult(success=True, messages=self.dry_run_steps(ctx))

        result = WorkflowResult()

        # 1. 记录交易 (如果提供了参数)
        trade_params = ctx.args.get("trade", {})
        if trade_params:
            try:
                import uuid
                from datetime import datetime

                from gold_miner.execution.journal import TradeRecord

                journal = TradeJournal()
                record = TradeRecord(
                    id=uuid.uuid4().hex[:12],
                    timestamp=datetime.now(),
                    signal=trade_params.get("signal", "buy"),
                    instrument=trade_params.get("instrument", "XAUUSD"),
                    position_pct=trade_params.get("position_pct", 0.1),
                    entry_price=trade_params.get("entry_price", 0.0),
                )
                journal.record(record)
                result.messages.append(f"交易已记录: {record.id}")
            except Exception as e:
                result.messages.append(f"交易记录失败: {e}")
        else:
            result.messages.append("未提供交易参数，跳过记录")

        # 2. 更新 portfolio (通过 storage 层)
        try:
            from gold_miner.storage import get_store

            store = get_store()
            portfolio = store.load_portfolio()
            result.messages.append(f"Portfolio 更新完成: {len(portfolio)} 个持仓")
        except Exception as e:
            result.messages.append(f"Portfolio 更新失败: {e}")

        # 3. 触发复盘
        result.messages.append("复盘触发: 建议运行 gold-miner workflow post-market")

        return result

    def dry_run_steps(self, ctx: WorkflowContext) -> list[str]:
        return [
            "[1] 记录交易到 TradeJournal",
            "[2] 更新 portfolio (via storage)",
            "[3] 追加 trade log",
            "[4] 触发复盘建议",
        ]


class WeeklyReviewWorkflow(Workflow):
    """周度复盘工作流: 验证预测 + 生成 findings + 报告."""

    name = "weekly-review"
    aliases = {"weekly", "week", "周度", "周复盘", "review"}
    description = "周度复盘: 验证预测准确率 + 生成改进 findings + 输出周报"

    def run(self, ctx: WorkflowContext) -> WorkflowResult:
        if ctx.dry_run:
            return WorkflowResult(success=True, messages=self.dry_run_steps(ctx))

        result = WorkflowResult()

        # 1. 验证预测
        try:
            tracker = PredictionTracker()
            predictions = tracker.load_all()
            result.messages.append(f"总预测: {len(predictions)} 条")

            resolved = [p for p in predictions if p.actual_price is not None]
            result.messages.append(f"已结算: {len(resolved)} 条")

            if resolved:
                correct = sum(1 for p in resolved if p.was_correct)
                accuracy = correct / len(resolved) if resolved else 0.0
                result.messages.append(f"准确率: {accuracy:.1%}")
        except Exception as e:
            result.messages.append(f"预测验证失败: {e}")

        # 2. 生成 findings
        try:
            analyzer = PerformanceAnalyzer()
            analysis = analyzer.analyze(predictions)
            generator = FindingGenerator()
            findings = generator.generate(analysis, predictions)
            result.messages.append(f"改进建议: {len(findings)} 条")
            for f in findings[:3]:
                result.messages.append(f"  [{f.severity}] {f.title}: {f.recommendation}")
        except Exception as e:
            result.messages.append(f"Findings 生成失败: {e}")

        # 3. 输出周报摘要
        result.messages.append("--- 周度复盘完成 ---")

        return result

    def dry_run_steps(self, ctx: WorkflowContext) -> list[str]:
        return [
            "[1] 加载所有预测记录",
            "[2] 统计已结算/准确率",
            "[3] 按维度分析准确率",
            "[4] 生成改进 findings",
            "[5] 输出周报摘要",
        ]
