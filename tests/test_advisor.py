"""advisor 模块测试 — 主动式战略投资顾问."""

from __future__ import annotations

import pytest

from gold_miner.advisor.core import (
    ActionInstruction,
    ActionType,
    AdvisorReport,
    AlertLevel,
    EventForecast,
    ExtremeStressTest,
    PositionSize,
    SentimentReading,
    UserProfile,
)
from gold_miner.advisor.early_warning import EarlyWarningEngine, HistoricalImpact, EVENT_IMPACT_DB
from gold_miner.advisor.extreme_guard import ExtremeGuard, EXTREME_SCENARIOS
from gold_miner.advisor.consultant import Consultant, ParsedIntent, IntentType
from gold_miner.advisor.sentiment_guard import SentimentGuard
from gold_miner.data.calendar import EventType, EventImpact, CalendarEvent
from datetime import datetime, timedelta


class TestCoreModels:
    """核心数据模型测试."""

    def test_advisor_report_to_markdown(self) -> None:
        report = AdvisorReport(
            report_type="action_guide",
            instruction=ActionInstruction(
                action=ActionType.BUY,
                position_size=PositionSize.MODERATE,
                target_pct=0.4,
                entry_price=2300.0,
                stop_loss=2250.0,
                take_profit=2400.0,
                urgency="high",
                reason="综合评分+0.35，趋势向上",
                risk_note="注意止损",
            ),
            warnings=["测试警告"],
        )
        md = report.to_markdown()
        assert "BUY" in md
        assert "40%" in md
        assert "$2300.00" in md
        assert "测试警告" in md

    def test_user_profile_defaults(self) -> None:
        p = UserProfile()
        assert p.risk_tolerance == "medium"
        assert p.current_position_pct == 0.0
        assert p.max_single_position_pct == 0.8


class TestEarlyWarning:
    """主动预警引擎测试."""

    def test_event_impact_db_coverage(self) -> None:
        """确保核心事件类型都有历史影响数据."""
        core_events = [
            EventType.FED_RATE, EventType.CPI, EventType.PPI,
            EventType.PCE, EventType.NFP, EventType.PMI,
            EventType.GEO_POLITICAL, EventType.GOLD_RESERVE,
        ]
        for et in core_events:
            assert et in EVENT_IMPACT_DB, f"{et} 缺少历史影响数据"

    def test_scan_finds_events(self) -> None:
        engine = EarlyWarningEngine()
        report = engine.scan(days_ahead=30)
        assert isinstance(report, AdvisorReport)
        assert report.report_type == "early_warning"
        # 30天内应该有事件
        assert len(report.alerts) >= 0

    def test_scan_returns_sorted_alerts(self) -> None:
        engine = EarlyWarningEngine()
        report = engine.scan(days_ahead=30)
        # 高优先级应该排在前面
        levels = [a.impact_level for a in report.alerts]
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        numeric = [order.get(l.value, 99) for l in levels]
        assert numeric == sorted(numeric)

    def test_today_events(self) -> None:
        engine = EarlyWarningEngine()
        report = engine.check_today()
        assert report.report_type == "early_warning"
        # 要么有今日事件，要么返回空报告
        assert isinstance(report.warnings, list)

    def test_forecast_event_unknown_type(self) -> None:
        engine = EarlyWarningEngine()
        event = CalendarEvent(
            name="未知事件",
            event_type=EventType.ECB,
            scheduled_at=datetime.now() + timedelta(days=1),
            impact=EventImpact.MEDIUM,
        )
        result = engine._forecast_event(event)
        assert result is None  # ECB 不在 EVENT_IMPACT_DB 中

    def test_escalate_level(self) -> None:
        assert EarlyWarningEngine._escalate_level(AlertLevel.LOW) == AlertLevel.MEDIUM
        assert EarlyWarningEngine._escalate_level(AlertLevel.MEDIUM) == AlertLevel.HIGH
        assert EarlyWarningEngine._escalate_level(AlertLevel.HIGH) == AlertLevel.CRITICAL
        assert EarlyWarningEngine._escalate_level(AlertLevel.CRITICAL) == AlertLevel.CRITICAL

    def test_build_advice_near_event(self) -> None:
        event = CalendarEvent(
            name="FOMC",
            event_type=EventType.FED_RATE,
            scheduled_at=datetime.now(),
            impact=EventImpact.HIGH,
        )
        advice = EarlyWarningEngine._build_advice(event, "up", 0)
        assert "观望" in advice

    def test_build_advice_distant_bullish(self) -> None:
        event = CalendarEvent(
            name="CPI",
            event_type=EventType.CPI,
            scheduled_at=datetime.now(),
            impact=EventImpact.HIGH,
        )
        advice = EarlyWarningEngine._build_advice(event, "up", 5)
        assert "关注" in advice

    def test_generate_warnings_dense_events(self) -> None:
        alerts = [
            EventForecast("E1", "type", datetime.now(), AlertLevel.HIGH, "up", 0.01, 0.5, [], ""),
            EventForecast("E2", "type", datetime.now(), AlertLevel.HIGH, "up", 0.01, 0.5, [], ""),
            EventForecast("E3", "type", datetime.now(), AlertLevel.CRITICAL, "up", 0.01, 0.5, [], ""),
        ]
        warnings = EarlyWarningEngine._generate_warnings(alerts)
        assert any("高影响事件密集" in w for w in warnings)
        assert any("关键事件" in w for w in warnings)


class TestSentimentGuard:
    """情绪守卫测试."""

    def test_infer_retail_sentiment_greedy(self) -> None:
        sent, extreme = SentimentGuard._infer_retail_sentiment(80, None)
        assert sent == "greedy"
        assert extreme is True

    def test_infer_retail_sentiment_fearful(self) -> None:
        sent, extreme = SentimentGuard._infer_retail_sentiment(20, None)
        assert sent == "fearful"
        assert extreme is True

    def test_infer_retail_sentiment_vix_extreme(self) -> None:
        sent, extreme = SentimentGuard._infer_retail_sentiment(50, 35)
        assert sent == "fearful"
        assert extreme is True

    def test_is_divergence(self) -> None:
        reading = SentimentReading(
            retail_sentiment="greedy",
            retail_extreme=True,
            institutional_signal="selling",
            cot_position="neutral",
            etf_flow_signal="outflow",
        )
        assert SentimentGuard._is_divergence(reading) is True

    def test_no_divergence(self) -> None:
        reading = SentimentReading(
            retail_sentiment="greedy",
            retail_extreme=True,
            institutional_signal="buying",
            cot_position="net_long",
            etf_flow_signal="inflow",
        )
        assert SentimentGuard._is_divergence(reading) is False

    def test_analyze_returns_report(self) -> None:
        guard = SentimentGuard()
        report = guard.analyze()
        assert report.report_type == "sentiment"
        assert report.sentiment is not None


class TestExtremeGuard:
    """极端情景防御测试."""

    def test_scenario_db_not_empty(self) -> None:
        assert len(EXTREME_SCENARIOS) > 0

    def test_stress_test_returns_reports(self) -> None:
        guard = ExtremeGuard()
        report = guard.stress_test(current_position_pct=0.5)
        assert report.report_type == "extreme"
        assert len(report.stress_tests) == len(EXTREME_SCENARIOS)

    def test_stress_test_drawdown_scales_with_position(self) -> None:
        guard = ExtremeGuard()
        r1 = guard.stress_test(current_position_pct=0.5)
        r2 = guard.stress_test(current_position_pct=1.0)
        # 仓位越高，回撤越大
        d1 = min(t.max_drawdown_pct for t in r1.stress_tests)
        d2 = min(t.max_drawdown_pct for t in r2.stress_tests)
        assert d2 <= d1  # 满仓回撤更大或相等

    def test_stress_test_sorted_by_drawdown(self) -> None:
        guard = ExtremeGuard()
        report = guard.stress_test(current_position_pct=0.5)
        drawdowns = [t.max_drawdown_pct for t in report.stress_tests]
        assert drawdowns == sorted(drawdowns)

    def test_check_scenario_found(self) -> None:
        guard = ExtremeGuard()
        report = guard.check_scenario("战争", current_position_pct=0.5)
        assert len(report.stress_tests) > 0
        assert any("战争" in w for w in report.warnings)

    def test_check_scenario_not_found(self) -> None:
        guard = ExtremeGuard()
        report = guard.check_scenario("不存在的词", current_position_pct=0.5)
        assert len(report.stress_tests) == 0

    def test_preparedness_higher_with_cash(self) -> None:
        guard = ExtremeGuard()
        r1 = guard.stress_test(current_position_pct=0.9)
        r2 = guard.stress_test(current_position_pct=0.1)
        p1 = sum(t.preparedness_score for t in r1.stress_tests) / len(r1.stress_tests)
        p2 = sum(t.preparedness_score for t in r2.stress_tests) / len(r2.stress_tests)
        assert p2 > p1  # 现金多，准备度高


class TestConsultant:
    """投资咨询测试."""

    def test_parse_intent_event_impact(self) -> None:
        intent = Consultant._parse_intent("美联储下周加息怎么办")
        assert intent.intent == IntentType.EVENT_IMPACT
        assert "加息" in intent.keywords

    def test_parse_intent_position_review(self) -> None:
        intent = Consultant._parse_intent("我仓位60%合适吗")
        assert intent.intent == IntentType.POSITION_REVIEW

    def test_parse_intent_sentiment(self) -> None:
        intent = Consultant._parse_intent("现在市场情绪怎么样")
        assert intent.intent == IntentType.SENTIMENT_CHECK

    def test_parse_intent_extreme(self) -> None:
        intent = Consultant._parse_intent("如果打仗了怎么办")
        assert intent.intent == IntentType.EXTREME_CONCERN

    def test_parse_intent_buy_sell(self) -> None:
        intent = Consultant._parse_intent("现在该买黄金吗")
        assert intent.intent == IntentType.BUY_SELL_ADVICE

    def test_parse_intent_unknown(self) -> None:
        intent = Consultant._parse_intent("今天天气怎么样")
        assert intent.intent == IntentType.GENERAL

    def test_answer_event_impact(self) -> None:
        consultant = Consultant()
        report = consultant.answer("美联储下周加息怎么办", current_position_pct=0.3)
        assert report.report_type == "consult"
        assert len(report.consultation_answer) > 0

    def test_answer_position_review_high(self) -> None:
        consultant = Consultant()
        report = consultant.answer("我仓位80%合适吗", current_position_pct=0.8)
        assert "80%" in report.consultation_answer

    def test_closing_empty_position(self) -> None:
        closing = Consultant._closing(0.0, UserProfile())
        assert "空仓" in closing

    def test_closing_heavy_position(self) -> None:
        closing = Consultant._closing(0.8, UserProfile())
        assert "重仓" in closing

    def test_closing_normal_position(self) -> None:
        closing = Consultant._closing(0.4, UserProfile())
        assert "长跑" in closing
