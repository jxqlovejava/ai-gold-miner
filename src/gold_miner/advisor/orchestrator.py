"""Advisor 编排器 — 串联所有模块，提供统一入口.

设计原则:
  - 每个子模块独立运行，Orchestrator 只负责协调
  - 支持按需调用（用户只关心什么，就返回什么）
  - 不重复计算，结果可缓存
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger

from gold_miner.advisor.action_guide import ActionGuide
from gold_miner.advisor.consultant import Consultant
from gold_miner.advisor.core import (
    AdvisorReport,
    UserProfile,
)
from gold_miner.advisor.early_warning import EarlyWarningEngine
from gold_miner.advisor.extreme_guard import ExtremeGuard
from gold_miner.advisor.sentiment_guard import SentimentGuard


@dataclass
class AdvisorState:
    """Advisor 运行状态 — 缓存中间结果."""
    last_action_guide: AdvisorReport | None = None
    last_early_warning: AdvisorReport | None = None
    last_sentiment: AdvisorReport | None = None
    last_extreme: AdvisorReport | None = None
    last_consult: AdvisorReport | None = None
    timestamp: datetime = field(default_factory=datetime.now)

    def is_fresh(self, minutes: int = 30) -> bool:
        """检查结果是否在指定时间内生成."""
        return (datetime.now() - self.timestamp).total_seconds() < minutes * 60


class Advisor:
    """主动式战略投资顾问 — 统一入口.

    使用方式:
        advisor = Advisor()

        # 每日行动指令
        report = advisor.daily_guide(current_position_pct=0.3, avg_cost=2300)
        print(report.to_markdown())

        # 检查预警
        alerts = advisor.check_alerts(days_ahead=7)

        # 用户咨询
        answer = advisor.consult(
            "美联储下周加息，我该怎么办？",
            current_position_pct=0.5,
            avg_cost=2280,
        )

        # 全面体检
        full_report = advisor.full_check(current_position_pct=0.5)
    """

    def __init__(self, profile: UserProfile | None = None) -> None:
        self.profile = profile or UserProfile()
        self.early_warning = EarlyWarningEngine()
        self.action_guide = ActionGuide()
        self.sentiment_guard = SentimentGuard()
        self.extreme_guard = ExtremeGuard()
        self.consultant = Consultant()
        self.state = AdvisorState()

    # ------------------------------------------------------------------
    # 核心接口
    # ------------------------------------------------------------------

    def daily_guide(
        self,
        current_position_pct: float = 0.0,
        avg_cost: float = 0.0,
        strategy_preference: str | None = None,
    ) -> AdvisorReport:
        """生成今日行动指令.

        这是最重要的接口 — 每天运行一次，告诉用户该怎么做.

        Returns:
            包含 action instruction 的完整报告
        """
        logger.info("[Advisor] 生成每日行动指令...")

        from gold_miner.strategy.objectives import StrategyObjective

        preference = None
        if strategy_preference:
            try:
                preference = StrategyObjective(strategy_preference)
            except ValueError:
                logger.warning(f"未知策略偏好: {strategy_preference}，使用自动选择")

        report = self.action_guide.generate(
            current_position_pct=current_position_pct,
            avg_cost=avg_cost,
            strategy_preference=preference,
        )
        self.state.last_action_guide = report
        return report

    def check_alerts(self, days_ahead: int = 7) -> AdvisorReport:
        """检查未来预警.

        Returns:
            事件预警报告
        """
        logger.info("[Advisor] 检查未来预警...")
        report = self.early_warning.scan(days_ahead=days_ahead)
        self.state.last_early_warning = report
        return report

    def check_today_events(self) -> AdvisorReport:
        """检查今日是否有重大事件.

        Returns:
            今日事件预警（如果有）
        """
        logger.info("[Advisor] 检查今日事件...")
        return self.early_warning.check_today()

    def sentiment_scan(self) -> AdvisorReport:
        """扫描当前市场情绪.

        Returns:
            情绪分析报告
        """
        logger.info("[Advisor] 扫描市场情绪...")
        report = self.sentiment_guard.analyze()
        self.state.last_sentiment = report
        return report

    def extreme_check(
        self, keyword: str | None = None, current_position_pct: float = 0.0
    ) -> AdvisorReport:
        """极端情景检查.

        Args:
            keyword: 特定情景关键词，None=全面体检

        Returns:
            压力测试报告
        """
        logger.info("[Advisor] 极端情景检查...")
        if keyword:
            report = self.extreme_guard.check_scenario(keyword, current_position_pct)
        else:
            report = self.extreme_guard.stress_test(current_position_pct)
        self.state.last_extreme = report
        return report

    def consult(
        self,
        question: str,
        current_position_pct: float = 0.0,
        avg_cost: float = 0.0,
    ) -> AdvisorReport:
        """回答用户咨询.

        Args:
            question: 用户问题
            current_position_pct: 当前仓位
            avg_cost: 持仓均价

        Returns:
            个性化咨询回应
        """
        logger.info(f"[Advisor] 咨询: {question[:40]}...")
        report = self.consultant.answer(
            question=question,
            current_position_pct=current_position_pct,
            avg_cost=avg_cost,
            user_profile=self.profile,
        )
        self.state.last_consult = report
        return report

    def full_check(
        self,
        current_position_pct: float = 0.0,
        avg_cost: float = 0.0,
    ) -> list[AdvisorReport]:
        """全面体检 — 运行所有模块.

        Returns:
            按优先级排序的报告列表
        """
        logger.info("[Advisor] 运行全面体检...")

        reports: list[AdvisorReport] = []

        # 1. 今日事件（最高优先级）
        today = self.check_today_events()
        if today.alerts or today.warnings:
            reports.append(today)

        # 2. 行动指令
        reports.append(self.daily_guide(current_position_pct, avg_cost))

        # 3. 预警
        reports.append(self.check_alerts(days_ahead=7))

        # 4. 情绪
        reports.append(self.sentiment_scan())

        # 5. 极端情景
        reports.append(self.extreme_check(current_position_pct=current_position_pct))

        self.state.timestamp = datetime.now()
        return reports

    def watch_mode(
        self,
        current_position_pct: float = 0.0,
        avg_cost: float = 0.0,
        interval_minutes: int = 60,
    ) -> None:
        """监控模式 — 循环检查并输出预警.

        Args:
            current_position_pct: 当前仓位
            avg_cost: 持仓均价
            interval_minutes: 检查间隔（分钟）
        """
        import time

        logger.info(f"[Advisor] 启动监控模式，间隔 {interval_minutes} 分钟")

        while True:
            try:
                # 检查今日事件
                today = self.check_today_events()
                if today.warnings:
                    print("\n" + "=" * 50)
                    print(today.to_markdown())
                    print("=" * 50 + "\n")

                # 检查预警
                alerts = self.check_alerts(days_ahead=3)
                if alerts.alerts:
                    high = [a for a in alerts.alerts
                            if a.impact_level in ("high", "critical")]
                    if high:
                        print("\n" + "=" * 50)
                        print(alerts.to_markdown())
                        print("=" * 50 + "\n")

                # 每隔几次检查一次情绪
                # 简化: 每次循环都检查
                sentiment = self.sentiment_scan()
                if any("极端" in w for w in sentiment.warnings):
                    print("\n" + "=" * 50)
                    print(sentiment.to_markdown())
                    print("=" * 50 + "\n")

                logger.info(f"[Advisor] 监控循环完成，休眠 {interval_minutes} 分钟")
                time.sleep(interval_minutes * 60)

            except KeyboardInterrupt:
                logger.info("[Advisor] 监控模式被用户中断")
                break
            except Exception as e:
                logger.error(f"[Advisor] 监控循环异常: {e}")
                time.sleep(60)
