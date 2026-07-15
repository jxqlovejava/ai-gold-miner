"""日历钟点规则 — 双重换算事故回归测试."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from gold_miner.data.calendar import CalendarEvent, EventCalendar, EventImpact, EventType
from gold_miner.data.calendar_time_rules import (
    check_event_clock,
    dual_clock_str,
    is_hearing_like,
    make_et_iso,
)


class TestHearingDoubleConvert:
    """2026-07-15 事故: 10:00 ET 误存 22:00 ET → 北京显示次日 10:00."""

    def test_wrong_warsh_hour_is_error(self):
        bad = datetime(2026, 7, 14, 22, 0, tzinfo=timezone(timedelta(hours=-4)))
        findings = check_event_clock(
            name="美联储主席沃什 众议院金融服务委员会听证会",
            event_type="fed_speech",
            scheduled_at=bad,
        )
        assert any(f.severity == "error" and f.code == "hearing_double_convert" for f in findings)

    def test_correct_warsh_hour_ok(self):
        good = datetime(2026, 7, 14, 10, 0, tzinfo=timezone(timedelta(hours=-4)))
        findings = check_event_clock(
            name="美联储主席沃什 众议院金融服务委员会听证会",
            event_type="fed_speech",
            scheduled_at=good,
        )
        errors = [f for f in findings if f.severity == "error"]
        assert errors == []

    def test_correct_bj_is_evening_same_day(self):
        good = datetime(2026, 7, 14, 10, 0, tzinfo=timezone(timedelta(hours=-4)))
        s = dual_clock_str(good)
        assert "2026-07-14 10:00" in s
        assert "2026-07-14 22:00" in s  # 北京同日晚上, 不是次日上午

    def test_wrong_bj_would_be_next_morning(self):
        bad = datetime(2026, 7, 14, 22, 0, tzinfo=timezone(timedelta(hours=-4)))
        s = dual_clock_str(bad)
        assert "2026-07-15 10:00" in s  # 错误形态: 北京次日上午


class TestDataReleaseHours:
    def test_cpi_0830_ok(self):
        dt = datetime(2026, 7, 14, 8, 30, tzinfo=timezone(timedelta(hours=-4)))
        findings = check_event_clock(name="美国CPI", event_type="cpi", scheduled_at=dt)
        assert not any(f.severity == "error" for f in findings)

    def test_cpi_evening_et_error(self):
        dt = datetime(2026, 7, 14, 20, 30, tzinfo=timezone(timedelta(hours=-4)))
        findings = check_event_clock(name="美国CPI", event_type="cpi", scheduled_at=dt)
        assert any(f.severity == "error" for f in findings)


class TestAddEventGuard:
    def test_add_event_rejects_hearing_double_convert(self, tmp_path):
        cal = EventCalendar(data_path=tmp_path / "cal.jsonl")
        bad = CalendarEvent(
            name="美联储主席 众议院金融服务委员会听证会",
            event_type=EventType.FED_SPEECH,
            scheduled_at=datetime(2026, 7, 14, 22, 0, tzinfo=timezone(timedelta(hours=-4))),
            impact=EventImpact.HIGH,
            source="test",
        )
        with pytest.raises(ValueError, match="钟点校验失败|hearing"):
            cal.add_event(bad)

    def test_add_event_accepts_correct_hearing(self, tmp_path):
        path = tmp_path / "cal.jsonl"
        cal = EventCalendar(data_path=path)
        good = CalendarEvent(
            name="美联储主席 众议院金融服务委员会听证会",
            event_type=EventType.FED_SPEECH,
            scheduled_at=datetime(2026, 7, 14, 10, 0, tzinfo=timezone(timedelta(hours=-4))),
            impact=EventImpact.HIGH,
            source="House notice",
        )
        cal.add_event(good)
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert "10:00:00-04:00" in text


class TestMakeEtIso:
    def test_july_is_edt(self):
        iso = make_et_iso(2026, 7, 14, 10, 0)
        assert iso.endswith("-04:00")
        assert "T10:00:00" in iso

    def test_is_hearing_like(self):
        assert is_hearing_like("众议院金融服务委员会听证会", "fed_speech")
        assert not is_hearing_like("ISM制造业PMI", "pmi")
