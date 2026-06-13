"""主动Agent核心 — 调度器 + 简报任务."""

from __future__ import annotations

import time
from datetime import datetime

from loguru import logger

from gold_miner.agent.briefer import Briefer, Briefing
from gold_miner.agent.notify import NotificationRouter


class ProactiveAgent:
    """主动式黄金投资Agent.

    使用方式:
        agent = ProactiveAgent()
        agent.run_once("pre_market")    # 手动触发一次
        agent.start()                   # 启动调度循环 (前台)
    """

    def __init__(self) -> None:
        self.briefer = Briefer()
        self.notifier = NotificationRouter()
        self._running = False
        self._tasks: list[dict] = []

    # ------------------------------------------------------------------
    # 定时任务定义
    # ------------------------------------------------------------------

    @property
    def schedule(self) -> list[dict]:
        """任务时间表 (北京时间)."""
        from gold_miner.config import settings
        return [
            {"name": "pre_market", "time": settings.agent_schedule_pre_market,
             "desc": "盘前简报", "fn": self.task_pre_market},
            {"name": "post_open", "time": settings.agent_schedule_post_open,
             "desc": "开盘分析", "fn": self.task_post_open},
            {"name": "closing", "time": settings.agent_schedule_closing,
             "desc": "尾盘提醒", "fn": self.task_closing},
            {"name": "event_scan", "time": settings.agent_schedule_event_scan,
             "desc": "事件扫描", "fn": self.task_event_scan},
        ]

    # ------------------------------------------------------------------
    # 单次任务
    # ------------------------------------------------------------------

    def run_once(self, kind: str) -> Briefing | None:
        """手动触发一次指定类型的简报."""
        task_map = {
            "pre_market": self.task_pre_market,
            "post_open": self.task_post_open,
            "closing": self.task_closing,
            "event_scan": self.task_event_scan,
        }
        fn = task_map.get(kind)
        if fn:
            return fn()
        logger.error(f"未知任务类型: {kind}")
        return None

    def run_full_cycle(self) -> list[Briefing]:
        """运行完整分析周期 (调试用)."""
        briefings = []
        for task in self.schedule:
            try:
                b = task["fn"]()
                if b:
                    briefings.append(b)
            except Exception as e:
                logger.error(f"{task['name']} 失败: {e}")
        return briefings

    # ------------------------------------------------------------------
    # 调度循环
    # ------------------------------------------------------------------

    def start(self) -> None:
        """启动调度循环 (前台运行，Ctrl+C退出)."""
        self._running = True
        logger.info("主动Agent启动，等待调度...")
        self._print_schedule()

        last_run: dict[str, str] = {}

        while self._running:
            now = datetime.now()
            current_time = now.strftime("%H:%M")
            today = now.strftime("%Y-%m-%d")

            for task in self.schedule:
                task_time = task["time"]
                task_key = f"{task['name']}_{today}"

                # 检查是否到了执行时间且今天还没执行
                if current_time == task_time and last_run.get(task["name"]) != today:
                    logger.info(f"⏰ 触发: {task['desc']} ({task_time})")
                    try:
                        briefing = task["fn"]()
                        if briefing:
                            self._notify_briefing(briefing)
                    except Exception as e:
                        logger.error(f"{task['name']} 执行失败: {e}")
                    last_run[task["name"]] = today

            # 周末跳过周度展望
            if now.weekday() == 6 and current_time == "21:00" and last_run.get("weekly") != today:
                self.task_weekly_outlook()
                last_run["weekly"] = today

            time.sleep(30)  # 30秒检查一次

    def stop(self) -> None:
        self._running = False
        logger.info("主动Agent已停止")

    # ------------------------------------------------------------------
    # 各任务实现
    # ------------------------------------------------------------------

    def task_pre_market(self) -> Briefing | None:
        try:
            b = self.briefer.pre_market()
            logger.info(f"盘前简报: 国际${b.international_price:.1f} 国内¥{b.domestic_price:.2f}")
            return b
        except Exception as e:
            logger.error(f"盘前简报失败: {e}")
            return None

    def task_post_open(self) -> Briefing | None:
        try:
            b = self.briefer.post_open()
            logger.info(f"开盘分析: {b.signal_direction} 建议:{b.action}")
            return b
        except Exception as e:
            logger.error(f"开盘分析失败: {e}")
            return None

    def task_closing(self) -> Briefing | None:
        try:
            b = self.briefer.closing()
            logger.info(f"尾盘提醒: 占{b.briefer._last_briefing and 'ok' or 'simplified'}")
            return b
        except Exception as e:
            logger.error(f"尾盘提醒失败: {e}")
            return None

    def task_event_scan(self) -> Briefing | None:
        try:
            b = self.briefer.event_scan(days_ahead=7)
            count = len(b.upcoming_events)
            logger.info(f"事件扫描: 未来7天{count}个关键事件")
            return b
        except Exception as e:
            logger.error(f"事件扫描失败: {e}")
            return None

    def task_weekly_outlook(self) -> None:
        """周度展望 — 每周日晚."""
        try:
            b = self.briefer.event_scan(days_ahead=14)
            if b and b.upcoming_events:
                msg = f"📅 **本周关键事件**\n\n" + "\n".join(f"- {e}" for e in b.upcoming_events[:8])
                self.notifier.send_briefing(msg)
        except Exception as e:
            logger.error(f"周度展望失败: {e}")

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _notify_briefing(self, b: Briefing) -> None:
        md = b.to_markdown()
        # 控制台
        print(md)
        # 企业微信
        self.notifier.send_briefing(md)

    def _print_schedule(self) -> None:
        logger.info("任务时间表 (北京时间):")
        for t in self.schedule:
            logger.info(f"  {t['time']} — {t['desc']}")
