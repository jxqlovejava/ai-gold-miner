"""测试经济日历 — 确保日期准确性."""
from __future__ import annotations

from datetime import datetime

from gold_miner.data.calendar import EventCalendar, EventImpact


class TestEventCalendarAutoload:
    """EventCalendar.__init__ 默认 autoload，避免直接用户拿到 n=0."""

    def test_autoload_populates_events(self):
        cal = EventCalendar()
        assert len(cal.events) > 0

    def test_autoload_false_starts_empty(self):
        cal = EventCalendar(autoload=False)
        assert cal.events == []
        assert cal._loaded_years == set()

    def test_load_fixed_calendar_idempotent_same_year(self):
        cal = EventCalendar()
        n = len(cal.events)
        assert n > 0
        year = datetime.now().year
        again = cal.load_fixed_calendar(year)
        assert len(cal.events) == n
        assert len(again) == n
        # 再次加载仍不翻倍
        cal.load_fixed_calendar(year)
        assert len(cal.events) == n

    def test_load_different_year_extends_without_doubling(self):
        cal = EventCalendar(autoload=False)
        e2026 = cal.load_fixed_calendar(2026)
        n1 = len(cal.events)
        assert len(e2026) == n1
        e2025 = cal.load_fixed_calendar(2025)
        assert len(e2025) > 0
        assert len(cal.events) == n1 + len(e2025)
        # 重复加载 2026 不增加
        cal.load_fixed_calendar(2026)
        assert len(cal.events) == n1 + len(e2025)


class TestEventCalendar2026:
    """2026 年日历日期验证.

    数据源：
      - BLS 官方年度发布日程 (CPI/PPI/NFP)
      - BEA 官方日程 (PCE)
      - ISM 官方日程 (PMI)
      - Federal Reserve 官方日程 (FOMC)

    关键验证：2026年6月
      - CPI: 6/10 (周三) — 用户验证
      - PPI: 6/11 (周四) — 用户验证
      - NFP: 6/5 (周五) — 每月第一个周五
      - FOMC: 6/17 (周三) — Fed 官方日程
    """

    def setup_method(self):
        self.cal = EventCalendar()
        self.events = self.cal.load_fixed_calendar(2026)

    def _find(self, name: str) -> list:
        return [e for e in self.events if e.name == name]

    # --- 6月关键事件 ---

    def test_june_cpi_date(self):
        """6月CPI应在6月10日 (BLS官方已确认)."""
        events = self._find("美国CPI")
        cpi_events = [e for e in events if e.scheduled_at.month == 6]
        assert len(cpi_events) == 1
        assert cpi_events[0].scheduled_at.day == 10

    def test_june_ppi_date(self):
        """6月PPI应在6月11日 (BLS官方已确认)."""
        events = self._find("美国PPI")
        ppi_events = [e for e in events if e.scheduled_at.month == 6]
        assert len(ppi_events) == 1
        assert ppi_events[0].scheduled_at.day == 11

    def test_june_nfp_date(self):
        """6月NFP应在每月第一个周五 (2026年6月5日)."""
        events = self._find("非农就业")
        nfp_events = [e for e in events if e.scheduled_at.month == 6]
        assert len(nfp_events) == 1
        assert nfp_events[0].scheduled_at.day == 5

    def test_june_fomc_date(self):
        """6月FOMC应在6月17日 (Fed官方日程)."""
        events = self._find("FOMC利率决议")
        fomc_events = [e for e in events if e.scheduled_at.month == 6]
        assert len(fomc_events) == 1
        assert fomc_events[0].scheduled_at.day == 17

    def test_june_pce_date(self):
        """6月PCE应在6月25日左右 (BEA官方日程)."""
        events = self._find("核心PCE物价指数")
        pce_events = [e for e in events if e.scheduled_at.month == 6]
        assert len(pce_events) == 1
        # BEA 通常在25-28日之间
        assert 24 <= pce_events[0].scheduled_at.day <= 28

    # --- 全年完整性 ---

    def test_has_all_12_months_cpi(self):
        events = self._find("美国CPI")
        months = sorted(e.scheduled_at.month for e in events)
        assert months == list(range(1, 13))

    def test_has_all_12_months_ppi(self):
        events = self._find("美国PPI")
        months = sorted(e.scheduled_at.month for e in events)
        assert months == list(range(1, 13))

    def test_has_all_8_fomc_meetings(self):
        events = self._find("FOMC利率决议")
        assert len(events) == 8

    def test_all_events_have_source(self):
        from gold_miner.data.calendar import EventType

        for e in self.events:
            # monitor 可由分析流程追加，source 允许为空；固定宏观日历必须有来源
            if e.event_type == EventType.MONITOR:
                continue
            assert e.source != "", f"Event {e.name} has no source"

    # --- 2025 年日历验证 ---

    def test_2025_june_cpi(self):
        """2025年6月CPI为6月11日 (BLS官方)."""
        cal = EventCalendar()
        events_2025 = cal.load_fixed_calendar(2025)
        events = [e for e in events_2025 if e.name == "美国CPI" and e.scheduled_at.month == 6]
        assert len(events) == 1
        assert events[0].scheduled_at.day == 11

    # --- 回退模式测试 ---

    def test_approximate_fallback(self):
        """无精确数据源的年份应回退到推算日期."""
        cal = EventCalendar()
        events = cal.load_fixed_calendar(2027)  # 无精确数据
        cpi_events = [e for e in events if e.name == "美国CPI"]
        assert len(cpi_events) == 12
        # 所有推算日期应标记为 approx
        for e in cpi_events:
            assert "approx" in e.source.lower() or "推算" in e.source

    # --- NFP 槽位不被初请挤占 (2026-09-03 事故回归) ---

    def test_jobless_claims_does_not_displace_nfp(self):
        """初请失业金复用 EventType.NFP，持久化进 JSONL 后不得挤掉该月非农.

        事故: 补查初请结果 → _persist_programmatic_updates 将初请写入 JSONL →
        _supplement_missing_categories 按 (nfp, month) 裸去重 → 9/4 非农消失。
        """
        # 每个月都必须有「非农」类事件（名称含"非农"），即便该月已有初请(nfp类型)
        nfp_events = [
            e for e in self.events
            if "初请" not in e.name and e.event_type.value == "nfp"
        ]
        months = {e.scheduled_at.month for e in nfp_events}
        assert months == set(range(1, 13)), f"非农缺失月份: {sorted(set(range(1, 13)) - months)}"
        # 9/4 (2026年9月第一个周五) 非农必须存在
        sep_nfp = [
            e for e in nfp_events
            if e.scheduled_at.month == 9 and e.scheduled_at.day == 4
        ]
        assert len(sep_nfp) == 1
        assert sep_nfp[0].impact == EventImpact.HIGH

    # --- 本周事件查询 ---

    def test_get_upcoming_finds_this_week(self):
        """get_upcoming 应返回本周事件."""
        # 2026年6月8日(周一)查询未来7天
        from datetime import timedelta
        from gold_miner.data.calendar import datetime as dt

        # 模拟在6月8日
        original_now = dt.now

        class MockDatetime(dt):
            @staticmethod
            def now(tz=None):
                return dt(2026, 6, 8, 12, 0, tzinfo=tz)

        # patch
        import gold_miner.data.calendar as mod
        saved = mod.datetime
        mod.datetime = MockDatetime

        try:
            cal = EventCalendar()
            cal.load_fixed_calendar(2026)
            upcoming = cal.get_upcoming(days=7)
            names = {e.name for e in upcoming}
            # 6/10 CPI 和 6/11 PPI 应该被找到
            assert "美国CPI" in names, f"CPI missing from upcoming, found: {names}"
            assert "美国PPI" in names, f"PPI missing from upcoming, found: {names}"
        finally:
            mod.datetime = saved
