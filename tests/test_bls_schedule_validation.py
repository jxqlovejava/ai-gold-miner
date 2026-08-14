"""validate_bls_schedule.py 官方日历比对逻辑 — 2026-08-14 PPI 事故回归测试.

背景: PPI 被标成 08-14, 实际 BLS 为 08-13. DOW 校验 (ppi→周一至周五全放行)
无法拦截"日期错一天但星期合法". 方案 A 用 TradingEconomics 官方日历做日期级比对.
"""

from __future__ import annotations

from datetime import date

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from validate_bls_schedule import TeEvent, match_te_date  # noqa: E402


def _te(date_str: str, data_event: str) -> TeEvent:
    return TeEvent(date=date.fromisoformat(date_str), data_event=data_event, name="")


class TestMatchTeDate:
    """match_te_date: 同月匹配, 跨月/无候选 → None."""

    def test_same_month_match(self):
        events = [
            _te("2026-08-13", "ppi"),
            _te("2026-08-13", "ppi mom"),
            _te("2026-09-10", "ppi"),
        ]
        # 日历 8月 PPI → 匹配 TE 8/13 (同月最近)
        assert match_te_date(events, ["ppi"], date(2026, 8, 13)) == date(2026, 8, 13)

    def test_wrong_date_differs_returns_official(self):
        # 日历 8/14 (错误) → TE 同月只有 8/13 → 返回官方 8/13, 由调用方判 error
        events = [_te("2026-08-13", "ppi")]
        assert match_te_date(events, ["ppi"], date(2026, 8, 14)) == date(2026, 8, 13)

    def test_cross_month_returns_none(self):
        # 日历 9月 PPI, TE 只有 8月 PPI → 跨月不匹配, 返回 None (跳过非误报)
        events = [_te("2026-08-13", "ppi")]
        assert match_te_date(events, ["ppi"], date(2026, 9, 10)) is None

    def test_no_candidate_returns_none(self):
        events = [_te("2026-08-13", "ppi")]
        assert match_te_date(events, ["cpi"], date(2026, 8, 20)) is None

    def test_sep_ppi_matches_sep(self):
        # 9月特殊顺序: PPI 9/10 先于 CPI 9/11, 各自独立匹配
        events = [_te("2026-09-10", "ppi"), _te("2026-09-11", "cpi")]
        assert match_te_date(events, ["ppi"], date(2026, 9, 10)) == date(2026, 9, 10)
        assert match_te_date(events, ["cpi"], date(2026, 9, 11)) == date(2026, 9, 11)
