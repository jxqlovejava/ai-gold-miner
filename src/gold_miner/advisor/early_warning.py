"""主动预警引擎 — 基于事件日历提前预警.

核心逻辑:
  1. 从 EventCalendar 加载未来事件
  2. 根据事件类型 + 历史数据，预判对黄金的影响
  3. 生成 EventForecast + 提前应对建议

使用方式:
    engine = EarlyWarningEngine()
    report = engine.scan(days_ahead=7)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from gold_miner.advisor.core import AdvisorReport, AlertLevel, EventForecast
from gold_miner.data.calendar import CalendarEvent, EventCalendar, EventImpact, EventType


# ---------------------------------------------------------------------------
# 历史影响数据库 — 基于回测和历史统计
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HistoricalImpact:
    """某类事件对黄金的历史平均影响."""
    avg_move_pct: float          # 平均波动幅度
    up_prob: float               # 上涨概率
    down_prob: float             # 下跌概率
    typical_range_pct: float     # 典型波动区间
    sample_size: int             # 样本数


EVENT_IMPACT_DB: dict[EventType, HistoricalImpact] = {
    EventType.FED_RATE: HistoricalImpact(
        avg_move_pct=0.012,
        up_prob=0.45,
        down_prob=0.55,
        typical_range_pct=0.025,
        sample_size=120,
    ),
    EventType.CPI: HistoricalImpact(
        avg_move_pct=0.009,
        up_prob=0.52,
        down_prob=0.48,
        typical_range_pct=0.018,
        sample_size=80,
    ),
    EventType.PPI: HistoricalImpact(
        avg_move_pct=0.006,
        up_prob=0.50,
        down_prob=0.50,
        typical_range_pct=0.012,
        sample_size=60,
    ),
    EventType.PCE: HistoricalImpact(
        avg_move_pct=0.007,
        up_prob=0.51,
        down_prob=0.49,
        typical_range_pct=0.014,
        sample_size=50,
    ),
    EventType.NFP: HistoricalImpact(
        avg_move_pct=0.011,
        up_prob=0.48,
        down_prob=0.52,
        typical_range_pct=0.022,
        sample_size=100,
    ),
    EventType.PMI: HistoricalImpact(
        avg_move_pct=0.005,
        up_prob=0.49,
        down_prob=0.51,
        typical_range_pct=0.010,
        sample_size=40,
    ),
    EventType.GEO_POLITICAL: HistoricalImpact(
        avg_move_pct=0.020,
        up_prob=0.65,
        down_prob=0.35,
        typical_range_pct=0.040,
        sample_size=30,
    ),
    EventType.GOLD_RESERVE: HistoricalImpact(
        avg_move_pct=0.008,
        up_prob=0.55,
        down_prob=0.45,
        typical_range_pct=0.015,
        sample_size=20,
    ),
}


# ---------------------------------------------------------------------------
# 预警引擎
# ---------------------------------------------------------------------------

class EarlyWarningEngine:
    """主动预警引擎.

    扫描未来事件日历，结合历史统计预判影响，提前给出应对建议.
    """

    def __init__(self, calendar: EventCalendar | None = None) -> None:
        self.calendar = calendar or EventCalendar()
        self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        """确保日历已加载当前年份数据."""
        if not self.calendar.events:
            self.calendar.load_fixed_calendar(datetime.now().year)

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def scan(self, days_ahead: int = 7) -> AdvisorReport:
        """扫描未来 days_ahead 天内的事件并生成预警报告.

        Args:
            days_ahead: 向前扫描天数，默认7天

        Returns:
            AdvisorReport，report_type="early_warning"
        """
        upcoming = self.calendar.get_upcoming(days=days_ahead, min_impact=EventImpact.MEDIUM)
        logger.info(f"[EarlyWarning] 未来{days_ahead}天发现 {len(upcoming)} 个事件")

        alerts: list[EventForecast] = []
        for event in upcoming:
            forecast = self._forecast_event(event)
            if forecast:
                alerts.append(forecast)

        # 按影响等级排序
        level_order = {AlertLevel.CRITICAL: 0, AlertLevel.HIGH: 1, AlertLevel.MEDIUM: 2, AlertLevel.LOW: 3}
        alerts.sort(key=lambda a: level_order.get(a.impact_level, 99))

        return AdvisorReport(
            report_type="early_warning",
            alerts=alerts,
            confidence=self._avg_confidence(alerts),
            sources=["EventCalendar", "HistoricalImpactDB"],
            warnings=self._generate_warnings(alerts),
        )

    def check_today(self) -> AdvisorReport:
        """检查今天是否有重大事件即将发生.

        Returns:
            如果有今日事件，返回高优先级预警；否则返回空报告
        """
        today_events = self.calendar.get_today()
        if not today_events:
            return AdvisorReport(
                report_type="early_warning",
                confidence=1.0,
                warnings=["今日无重大事件 scheduled"],
            )

        alerts: list[EventForecast] = []
        for event in today_events:
            forecast = self._forecast_event(event)
            if forecast:
                # 今日事件提高影响等级
                forecast = self._escalate_today(forecast)
                alerts.append(forecast)

        return AdvisorReport(
            report_type="early_warning",
            alerts=alerts,
            confidence=self._avg_confidence(alerts),
            sources=["EventCalendar"],
            warnings=[f"⚠️ 今日有重大事件: {', '.join(e.name for e in today_events)} — 建议减少操作"],
        )

    # ------------------------------------------------------------------
    # 内部逻辑
    # ------------------------------------------------------------------

    def _forecast_event(self, event: CalendarEvent) -> EventForecast | None:
        """对单个事件生成影响预判."""
        impact = EVENT_IMPACT_DB.get(event.event_type)
        if impact is None:
            return None

        # 判断方向
        if impact.up_prob > impact.down_prob + 0.15:
            direction = "up"
        elif impact.down_prob > impact.up_prob + 0.15:
            direction = "down"
        else:
            direction = "uncertain"

        # 影响等级
        level = self._impact_level(event.impact, impact)

        # 距离事件天数
        days_until = (event.scheduled_at - datetime.now()).days
        # 临近事件提高影响等级
        if days_until <= 1:
            level = self._escalate_level(level)

        # 生成建议
        advice = self._build_advice(event, direction, days_until)

        return EventForecast(
            event_name=event.name,
            event_type=event.event_type.value,
            scheduled_at=event.scheduled_at,
            impact_level=level,
            gold_direction=direction,
            expected_move_pct=impact.typical_range_pct,
            confidence=min(impact.sample_size / 200, 0.9),
            historical_analogs=self._find_analogs(event.event_type),
            advice_summary=advice,
        )

    @staticmethod
    def _impact_level(event_impact: EventImpact, hist: HistoricalImpact) -> AlertLevel:
        """根据事件重要性和历史波动判定预警等级."""
        if event_impact == EventImpact.HIGH and hist.typical_range_pct > 0.015:
            return AlertLevel.HIGH
        if event_impact == EventImpact.HIGH:
            return AlertLevel.MEDIUM
        if hist.typical_range_pct > 0.010:
            return AlertLevel.MEDIUM
        return AlertLevel.LOW

    @staticmethod
    def _escalate_level(level: AlertLevel) -> AlertLevel:
        """临近事件提升一级."""
        escalation = {
            AlertLevel.LOW: AlertLevel.MEDIUM,
            AlertLevel.MEDIUM: AlertLevel.HIGH,
            AlertLevel.HIGH: AlertLevel.CRITICAL,
            AlertLevel.CRITICAL: AlertLevel.CRITICAL,
        }
        return escalation.get(level, level)

    def _escalate_today(self, forecast: EventForecast) -> EventForecast:
        """今日事件提升影响等级."""
        return EventForecast(
            event_name=f"【今日】{forecast.event_name}",
            event_type=forecast.event_type,
            scheduled_at=forecast.scheduled_at,
            impact_level=self._escalate_level(forecast.impact_level),
            gold_direction=forecast.gold_direction,
            expected_move_pct=forecast.expected_move_pct,
            confidence=forecast.confidence,
            historical_analogs=forecast.historical_analogs,
            advice_summary=forecast.advice_summary,
        )

    @staticmethod
    def _build_advice(event: CalendarEvent, direction: str, days_until: int) -> str:
        """生成针对事件的应对建议."""
        if days_until <= 1:
            return f"事件即将公布，建议观望，待数据出炉后再决策"

        if direction == "up":
            if days_until <= 3:
                return f"事件偏向利多，可考虑提前小幅加仓(≤10%)"
            return f"事件偏向利多，关注但不必提前行动"

        if direction == "down":
            if days_until <= 3:
                return f"事件偏向利空，可考虑提前减仓避险"
            return f"事件偏向利空，保持关注，临近再评估"

        return f"方向不明，建议维持现状，事件前1天再评估"

    @staticmethod
    def _find_analogs(event_type: EventType) -> list[str]:
        """返回历史类似情景关键词."""
        analogs: dict[EventType, list[str]] = {
            EventType.FED_RATE: ["2024-09降息50bp", "2022-03首次加息", "2023-07最后一次加息"],
            EventType.CPI: ["2024-06CPI降温", "2022-06CPI峰值9.1%"],
            EventType.NFP: ["2024-05非农爆冷", "2023-01非农大幅超预期"],
            EventType.GEO_POLITICAL: ["2022-02俄乌冲突", "2023-10巴以冲突"],
        }
        return analogs.get(event_type, [])

    @staticmethod
    def _avg_confidence(alerts: list[EventForecast]) -> float:
        if not alerts:
            return 1.0
        return round(sum(a.confidence for a in alerts) / len(alerts), 2)

    @staticmethod
    def _generate_warnings(alerts: list[EventForecast]) -> list[str]:
        warnings: list[str] = []
        high_count = sum(1 for a in alerts if a.impact_level in (AlertLevel.HIGH, AlertLevel.CRITICAL))
        if high_count >= 2:
            warnings.append(f"⚠️ 未来一周内 {high_count} 个高影响事件密集，建议降低仓位至50%以下")
        if any(a.impact_level == AlertLevel.CRITICAL for a in alerts):
            warnings.append("🔴 存在关键事件，必须提前制定应对预案")
        return warnings
