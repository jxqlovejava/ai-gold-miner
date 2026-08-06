"""EventDrivenSignalGenerator 事件驱动信号测试.

覆盖 2026-08-06 修复的两个核心缺陷：
1. PMI/PPI 类型缺失映射 → 数据事件被错误判为中性负分（ADP/ISM 等）
2. NEUTRAL 方向被按负分计算 → 系统性拉低 event 维度
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from gold_miner.data.calendar import CalendarEvent, EventImpact, EventType
from gold_miner.signals.base import SignalDirection
from gold_miner.signals.event_driven import (
    EventDrivenSignalGenerator,
    _classify_outcome,
    _extract_number,
    _infer_event_direction,
)

_UTC = timezone.utc
_BASE = datetime(2026, 8, 5, 12, 0, 0, tzinfo=_UTC)


@pytest.fixture
def generator() -> EventDrivenSignalGenerator:
    return EventDrivenSignalGenerator()


# ---------------------------------------------------------------------------
# _extract_number: 健壮数值提取
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("实际 +4.4万 (预期6.5-7.5万...)", 44000.0),  # 中文"万"单位 + 描述文本
        ("6.5万", 65000.0),
        ("55.6(预期54.0,前值53.3)", 55.6),  # 无单位指数
        ("3.3%", 3.3),
        ("+123K", 123000.0),
        ("2.5M", 2500000.0),
        ("无数据", None),
        ("", None),
    ],
)
def test_extract_number(text: str, expected: float | None) -> None:
    assert _extract_number(text) == expected


# ---------------------------------------------------------------------------
# _classify_outcome: PMI/PPI 数据事件正确分类
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("event_type", "actual", "forecast", "expected"),
    [
        (EventType.PMI, "实际 +4.4万 (预期6.5-7.5万...)", "6.5万", "below_forecast"),
        (EventType.PMI, "55.6(预期54.0)", "54.0", "above_forecast"),
        (EventType.PPI, "3.1%", "3.0%", "above_forecast"),
        (EventType.CPI, "2.8%", "3.0%", "below_forecast"),
        (EventType.PMI, "无法解析文本", "54.0", "in_line"),
    ],
)
def test_classify_outcome(event_type, actual, forecast, expected) -> None:
    assert _classify_outcome(event_type, actual, forecast) == expected


# ---------------------------------------------------------------------------
# _infer_event_direction: PMI/PPI 方向推断
# ---------------------------------------------------------------------------


def test_infer_direction_pmi_below_forecast() -> None:
    """ADP 弱于预期 → 就业降温 → 利好黄金（bullish）."""
    d = _infer_event_direction(
        EventType.PMI, "below_forecast",
        forecast="6.5万", actual="+4.4万",
    )
    assert d == SignalDirection.BULLISH


def test_infer_direction_ism_above_forecast() -> None:
    """ISM 制造业超预期 → 经济强劲 → 加息预期 → 利空黄金（bearish）."""
    d = _infer_event_direction(
        EventType.PMI, "above_forecast",
        forecast="54.0", actual="55.6",
    )
    assert d == SignalDirection.BEARISH


def test_infer_direction_ppi_above_forecast() -> None:
    d = _infer_event_direction(
        EventType.PPI, "above_forecast",
        forecast="3.0%", actual="3.1%",
    )
    assert d == SignalDirection.BEARISH


# ---------------------------------------------------------------------------
# 核心 bug 回归: NEUTRAL 方向不得为负分 / 数据事件方向正确
# ---------------------------------------------------------------------------


def test_post_event_adp_weak_is_bullish_positive_score(generator) -> None:
    """ADP +4.4万 弱于预期 6.5万 → bullish，score 为正（修复前为 neutral -0.70）."""
    event = CalendarEvent(
        name="ADP就业数据(7月)",
        event_type=EventType.PMI,
        scheduled_at=_BASE,
        impact=EventImpact.HIGH,
        actual="实际 +4.4万 (预期6.5-7.5万) 远低于预期",
        forecast="6.5万",
        previous="9.8万",
    )
    signals = generator.generate_post_event_signals([(event, event.actual, event.forecast)])
    assert len(signals) == 1
    s = signals[0]
    assert s.direction == SignalDirection.BULLISH
    assert s.score > 0


def test_post_event_fomc_hold_is_neutral_zero_score(generator) -> None:
    """FOMC 按兵不动（hold）→ neutral，score=0，不得为负（修复前 -0.70）."""
    event = CalendarEvent(
        name="FOMC利率决议",
        event_type=EventType.FED_RATE,
        scheduled_at=_BASE,
        impact=EventImpact.HIGH,
        actual="维持3.50-3.75%不变",
        forecast="3.50-3.75%",
    )
    signals = generator.generate_post_event_signals([(event, event.actual, event.forecast)])
    assert len(signals) == 1
    s = signals[0]
    assert s.direction == SignalDirection.NEUTRAL
    assert s.score == 0.0


def test_post_event_score_sign_matches_direction(generator) -> None:
    """核心修复：分数符号必须与方向一致（修复前 neutral 方向也乘 -1 得负分）.

    - bullish → score > 0
    - bearish → score < 0
    - neutral → score == 0
    """
    # ISM 服务业超预期 → bearish → 负分（方向正确，非"中性负分"）
    ism = CalendarEvent(
        name="ISM服务业PMI",
        event_type=EventType.PMI,
        scheduled_at=_BASE,
        impact=EventImpact.MEDIUM,
        actual="54.1(预期53.5)",
        forecast="53.5",
    )
    s_ism = generator.generate_post_event_signals(
        [(ism, ism.actual, ism.forecast)]
    )[0]
    assert s_ism.direction == SignalDirection.BEARISH
    assert s_ism.score < 0

    # ISM 服务业低于预期 → bullish → 正分
    ism_weak = CalendarEvent(
        name="ISM服务业PMI",
        event_type=EventType.PMI,
        scheduled_at=_BASE,
        impact=EventImpact.MEDIUM,
        actual="53.0(预期53.5)",
        forecast="53.5",
    )
    s_weak = generator.generate_post_event_signals(
        [(ism_weak, ism_weak.actual, ism_weak.forecast)]
    )[0]
    assert s_weak.direction == SignalDirection.BULLISH
    assert s_weak.score > 0
