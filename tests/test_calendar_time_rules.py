"""日历钟点规则 — 双重换算事故回归测试."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from gold_miner.data.calendar import CalendarEvent, EventCalendar, EventImpact, EventType
from gold_miner.data.calendar_time_rules import (
    check_event_clock,
    check_relative_anchors,
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
        with pytest.raises(ValueError, match="校验失败|hearing"):
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


class TestDowCheck:
    """DOW (星期) 校验 — 防止「周三初请失业金」类错误."""

    def test_jobless_claims_must_be_thursday(self):
        """初请失业金必须周四."""
        from gold_miner.data.calendar_time_rules import check_event_dow

        # 周四 = OK
        thu = datetime(2026, 7, 23, 8, 30, tzinfo=timezone(timedelta(hours=-4)))
        findings = check_event_dow(name="初请失业金人数", event_type="nfp", scheduled_at=thu)
        assert not findings, f"周四应该通过, 但得到: {findings}"

    def test_jobless_claims_wrong_dow_warns(self):
        """初请失业金写周三→应报 warning."""
        from gold_miner.data.calendar_time_rules import check_event_dow

        wed = datetime(2026, 7, 22, 8, 30, tzinfo=timezone(timedelta(hours=-4)))
        findings = check_event_dow(name="初请失业金人数", event_type="nfp", scheduled_at=wed)
        assert len(findings) >= 1
        assert any("周三" in f.message for f in findings)

    def test_fomc_must_be_wednesday(self):
        """FOMC 决议日必须周三."""
        from gold_miner.data.calendar_time_rules import check_event_dow

        wed = datetime(2026, 7, 29, 14, 0, tzinfo=timezone(timedelta(hours=-4)))
        findings = check_event_dow(name="FOMC利率决议", event_type="fed_rate", scheduled_at=wed)
        assert not findings, f"周三 FOMC 应该通过"

    def test_fomc_wrong_dow_warns(self):
        """FOMC 写周四→应报 warning."""
        from gold_miner.data.calendar_time_rules import check_event_dow

        thu = datetime(2026, 7, 30, 14, 0, tzinfo=timezone(timedelta(hours=-4)))
        findings = check_event_dow(name="FOMC利率决议", event_type="fed_rate", scheduled_at=thu)
        assert len(findings) >= 1

    def test_saturday_event_is_error(self):
        """非 geo/monitor 事件安排在周六→应报 error."""
        from gold_miner.data.calendar_time_rules import check_event_dow

        sat = datetime(2026, 7, 18, 8, 30, tzinfo=timezone(timedelta(hours=-4)))
        findings = check_event_dow(name="美国CPI", event_type="cpi", scheduled_at=sat)
        assert len(findings) >= 1
        assert any(f.severity == "error" for f in findings)

    def test_add_event_rejects_wrong_dow(self, tmp_path):
        """add_event 应拒绝 DOW 错误的事件 (如初请周三)."""
        path = tmp_path / "cal.jsonl"
        cal = EventCalendar(data_path=path)
        bad = CalendarEvent(
            name="初请失业金人数",
            event_type=EventType.NFP,
            scheduled_at=datetime(2026, 7, 22, 8, 30, tzinfo=timezone(timedelta(hours=-4))),
            impact=EventImpact.MEDIUM,
            source="test",
        )
        with pytest.raises(ValueError, match="校验失败|weekend"):
            cal.add_event(bad)

    def test_add_event_accepts_correct_dow(self, tmp_path):
        """add_event 应接受 DOW 正确的事件."""
        path = tmp_path / "cal.jsonl"
        cal = EventCalendar(data_path=path)
        good = CalendarEvent(
            name="初请失业金人数",
            event_type=EventType.NFP,
            scheduled_at=datetime(2026, 7, 23, 8, 30, tzinfo=timezone(timedelta(hours=-4))),
            impact=EventImpact.MEDIUM,
            source="DOL",
        )
        cal.add_event(good)  # 不应抛异常
        assert path.exists()

    def test_expected_dow_name_override(self):
        """名称覆盖: 初请失业金→周四, 非农→周五."""
        from gold_miner.data.calendar_time_rules import expected_dow

        assert expected_dow("nfp", "初请失业金人数") == {3}  # Thu
        assert expected_dow("nfp", "非农就业") == {4}        # Fri
        assert expected_dow("nfp", "普通nfp事件") == {3}      # type default

    def test_dow_reference_table_output(self):
        """generate_dow_reference_table 输出 Markdown 表格."""
        from gold_miner.data.calendar_time_rules import generate_dow_reference_table

        ET = timezone(timedelta(hours=-4))

        def _next_dow_et(target_dow: int, hour: int, minute: int = 30) -> str:
            """下一个指定星期几(0=周一)的 ET ISO 时间 — 避免硬编码日期过期被过滤."""
            today = datetime.now(timezone.utc).astimezone(ET).date()
            days_ahead = (target_dow - today.weekday()) % 7 or 7  # 今天则取下周
            d = today + timedelta(days=days_ahead)
            return datetime(d.year, d.month, d.day, hour, minute, tzinfo=ET).isoformat()

        events = [
            {  # 初请: 周四 08:30 ET
                "name": "初请失业金人数",
                "event_type": "nfp",
                "scheduled_at": _next_dow_et(3, 8, 30),
            },
            {  # FOMC: 周三 14:00 ET
                "name": "FOMC利率决议",
                "event_type": "fed_rate",
                "scheduled_at": _next_dow_et(2, 14, 0),
            },
            {  # PCE: 周四 08:30 ET (官方通常周四)
                "name": "核心PCE物价指数",
                "event_type": "pce",
                "scheduled_at": _next_dow_et(3, 8, 30),
            },
        ]
        table = generate_dow_reference_table(events, days_ahead=30)
        assert "初请失业金" in table
        assert "周四" in table
        assert "周三" in table  # FOMC
        assert "✅" in table     # 全部通过校验
        assert not is_hearing_like("ISM制造业PMI", "pmi")


class TestRelativeAnchors:
    """方案 B (2026-08-14): 同月 CPI/PPI 相邻性 — 拦截"日期偏移但 DOW 放行"粗错误."""

    @staticmethod
    def _ev(name: str, etype: str, iso: str) -> dict:
        return {"name": name, "event_type": etype, "scheduled_at": iso}

    def test_same_month_adjacent_ok(self):
        # BLS 8月: CPI 8/12 + PPI 8/13 (隔1天) → 无警告
        evs = [
            self._ev("美国CPI", "cpi", "2026-08-12T08:30:00-04:00"),
            self._ev("美国PPI", "ppi", "2026-08-13T08:30:00-04:00"),
        ]
        findings = check_relative_anchors(evs)
        assert not [f for f in findings if f.severity == "warning"]

    def test_sep_ppi_before_cpi_ok(self):
        # 2026-09 特殊: PPI 9/10 先于 CPI 9/11 — 顺序不限, 仍相邻 → 无警告
        evs = [
            self._ev("美国CPI", "cpi", "2026-09-11T08:30:00-04:00"),
            self._ev("美国PPI", "ppi", "2026-09-10T08:30:00-04:00"),
        ]
        findings = check_relative_anchors(evs)
        assert not [f for f in findings if f.severity == "warning"]

    def test_gap_over_5_days_warns(self):
        # PPI 与 CPI 相隔 6 天 → 警告 (BLS 同周惯例被打破)
        evs = [
            self._ev("美国CPI", "cpi", "2026-08-12T08:30:00-04:00"),
            self._ev("美国PPI", "ppi", "2026-08-18T08:30:00-04:00"),
        ]
        findings = check_relative_anchors(evs)
        assert any(f.severity == "warning" and f.code == "cpi_ppi_gap" for f in findings)

    def test_cross_month_no_pair_skips(self):
        # PPI 与 CPI 发布月不同 → 无法配对, 跳过不误报
        evs = [
            self._ev("美国CPI", "cpi", "2026-08-12T08:30:00-04:00"),
            self._ev("美国PPI", "ppi", "2026-09-10T08:30:00-04:00"),
        ]
        findings = check_relative_anchors(evs)
        assert not [f for f in findings if f.severity == "warning"]

    def test_non_us_cpi_excluded(self):
        # UK CPI 不应干扰美国 PPI 配对
        evs = [
            self._ev("UK CPI(7月)", "cpi", "2026-08-19T02:00:00-04:00"),
            self._ev("美国PPI", "ppi", "2026-09-10T08:30:00-04:00"),
        ]
        findings = check_relative_anchors(evs)
        assert not [f for f in findings if f.severity == "warning"]
