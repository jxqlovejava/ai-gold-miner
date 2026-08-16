"""交易时段判断 — 休市门禁回归测试 (2026-08-16 新增).

覆盖: 交易日判断 (周末/节假日/调休) + 民生积存金交易时段
(交易日 9:05 开盘 — 次日 02:00 收盘, 含夜盘 21:00-02:00).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from gold_miner.data.trading_hours import (
    is_accumulation_trading_time,
    is_cn_trading_day,
    next_cn_trading_day,
)

BJ = timezone(timedelta(hours=8))

# 关键日期 (北京时间):
#   2026-08-14 周五 / 08-15 周六 / 08-16 周日 / 08-17 周一 / 08-21 周五
#   2026-09-25 周五 (中秋节假日) / 2026-10-01 周四 (国庆节假日)


def _bj(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=BJ)


class TestIsCnTradingDay:
    def test_weekday_is_trading_day(self):
        assert is_cn_trading_day(_bj(2026, 8, 14, 10))  # 周五
        assert is_cn_trading_day(_bj(2026, 8, 17, 10))  # 周一

    def test_weekend_not_trading_day(self):
        assert not is_cn_trading_day(_bj(2026, 8, 15, 10))  # 周六
        assert not is_cn_trading_day(_bj(2026, 8, 16, 10))  # 周日

    def test_holiday_not_trading_day(self):
        # 2026-09-25 周五 中秋; 2026-10-01 周四 国庆 — 即使工作日也休市
        assert not is_cn_trading_day(_bj(2026, 9, 25, 10))
        assert not is_cn_trading_day(_bj(2026, 10, 1, 10))

    def test_timezone_conversion(self):
        # UTC 输入的对应北京时间判定 (UTC 02:00 周六 = 北京 10:00 周六)
        utc_sat = datetime(2026, 8, 15, 2, 0, tzinfo=timezone.utc)
        assert not is_cn_trading_day(utc_sat)
        # UTC 周一 01:00 = 北京周一 09:00 → 交易日
        utc_mon = datetime(2026, 8, 17, 1, 0, tzinfo=timezone.utc)
        assert is_cn_trading_day(utc_mon)


class TestIsAccumulationTradingTime:
    # ── 当日时段 ──
    def test_open_boundary_905(self):
        assert is_accumulation_trading_time(_bj(2026, 8, 17, 9, 5))   # 周一 9:05 开盘
        assert not is_accumulation_trading_time(_bj(2026, 8, 17, 9, 4))  # 开盘前

    def test_day_session(self):
        assert is_accumulation_trading_time(_bj(2026, 8, 17, 12, 0))    # 午盘
        assert is_accumulation_trading_time(_bj(2026, 8, 17, 21, 0))    # 夜盘开盘
        assert is_accumulation_trading_time(_bj(2026, 8, 17, 23, 59))   # 夜盘

    def test_overnight_gap_closed(self):
        # 02:00-09:05 休市 (冻结价)
        assert not is_accumulation_trading_time(_bj(2026, 8, 17, 3, 0))
        assert not is_accumulation_trading_time(_bj(2026, 8, 17, 9, 0))

    # ── 凌晨段 (前一交易日夜盘延续) ──
    def test_night_session_extends_to_next_day(self):
        # 周二 01:00 = 周一(交易日)夜盘延续
        assert is_accumulation_trading_time(_bj(2026, 8, 18, 1, 0))
        # 周二 02:00 = 收盘边界 (02:00 整不属于交易时段)
        assert not is_accumulation_trading_time(_bj(2026, 8, 18, 2, 0))

    def test_friday_night_extends_into_saturday(self):
        # 周六 01:59 = 周五夜盘延续
        assert is_accumulation_trading_time(_bj(2026, 8, 15, 1, 59))
        # 周六 02:00 收盘 → 休市
        assert not is_accumulation_trading_time(_bj(2026, 8, 15, 2, 0))
        # 周六 10:00 → 休市
        assert not is_accumulation_trading_time(_bj(2026, 8, 15, 10, 0))

    def test_sunday_has_no_night_session(self):
        # 周日 01:00 不属于任何交易时段 (周一是新交易日, 0-2点无夜盘)
        assert not is_accumulation_trading_time(_bj(2026, 8, 16, 1, 0))
        assert not is_accumulation_trading_time(_bj(2026, 8, 16, 23, 0))

    def test_monday_early_morning_not_trading(self):
        # 周一 00:30 — 周末后首个凌晨无夜盘
        assert not is_accumulation_trading_time(_bj(2026, 8, 17, 0, 30))

    # ── 节假日 ──
    def test_holiday_weekday_closed(self):
        # 中秋 (9/25 周五) 全天休市
        assert not is_accumulation_trading_time(_bj(2026, 9, 25, 10, 0))
        assert not is_accumulation_trading_time(_bj(2026, 9, 25, 22, 0))


class TestNextCnTradingDay:
    def test_skip_weekend(self):
        # 周五 → 下一交易日是周一
        fri = _bj(2026, 8, 14, 15, 0)
        assert next_cn_trading_day(fri).strftime("%Y-%m-%d") == "2026-08-17"

    def test_skip_holiday(self):
        # 中秋 9/25 (周五) → 下一个交易日 9/28 (周一)
        holiday = _bj(2026, 9, 25, 10, 0)
        nxt = next_cn_trading_day(holiday)
        assert nxt.strftime("%Y-%m-%d") == "2026-09-28"
        assert nxt.weekday() == 0  # 周一
