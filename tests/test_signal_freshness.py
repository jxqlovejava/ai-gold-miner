"""信号时效/真实性披露回归测试 — 事故 2026-09-17.

同一天查出三类「聪明钱」信号的可信度问题:

1. **GLD** — SPDR 官方数据真实, 但 6h 磁盘缓存跨越了发布时刻, 报告连续两轮
   显示 09/14→09/15 的 "+2.86吨" 而当期真值已是 09/15→09/16 的 "+1.71吨"。
   (修复见 tests/test_gld_holdings.py::TestFromDbStalenessGuard)
2. **COT** — CFTC 数据真实且当期最新, 但文案不含日期, 读起来像实时持仓。
   周报 as-of 周二、周五发布, 天然滞后 2~9 天。
3. **13F** — 上游恒返回空, 全部数据来自 `_fallback_summary()` 字面量, 每日不变,
   却以「机构持仓」名义持续给聪明钱维度 +0.35 分; 且被标成「当前季度」
   (9/17 标 Q3 2026, 而 Q3 的 13F 要到 11/14 才申报)。

本文件覆盖 2 与 3。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from gold_miner.data.institutional_13f import (
    Institutional13FFetcher,
    InstitutionalSummary,
)
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength
from gold_miner.signals.cot_signal import CotSignalGenerator
from gold_miner.signals.institutional_signal import InstitutionalSignalGenerator


class FakeCotFetcher:
    """可注入的假 COT fetcher."""

    def __init__(self, report_date: datetime | None, status: str = "ok") -> None:
        self._report_date = report_date
        self._status = status
        self.calls = 0

    def fetch_net_position(self, weeks: int = 4) -> dict:
        self.calls += 1
        return {
            "status": self._status,
            "report_date": self._report_date.isoformat() if self._report_date else None,
        }


def _signal(desc: str = "非商业净多仓连续增加: 231,960手") -> Signal:
    return Signal(
        name="COT聪明钱加仓",
        dimension="smart_money",
        direction=SignalDirection.BULLISH,
        strength=SignalStrength.MODERATE,
        score=0.31,
        description=desc,
        metadata={"source": "cot_report"},
    )


class TestCotReportDateStamp:
    def _gen(self, fetcher) -> CotSignalGenerator:
        gen = object.__new__(CotSignalGenerator)
        gen.fetcher = fetcher
        return gen

    def test_stamps_data_date_on_every_signal(self) -> None:
        # 2026-09-08 是当周 CFTC 的 as-of 周二 (周五发布)
        gen = self._gen(FakeCotFetcher(datetime(2026, 9, 8)))
        out = gen._stamp_report_date([_signal(), _signal("另一条")])
        assert all(s.description.endswith(", 数据日期09-08") for s in out)

    def test_stamps_metadata_report_date(self) -> None:
        gen = self._gen(FakeCotFetcher(datetime(2026, 9, 8)))
        out = gen._stamp_report_date([_signal()])
        assert out[0].metadata["report_date"] == "2026-09-08T00:00:00"

    def test_does_not_override_existing_report_date(self) -> None:
        gen = self._gen(FakeCotFetcher(datetime(2026, 9, 8)))
        s = _signal()
        s.metadata["report_date"] = "2026-09-01T00:00:00"
        gen._stamp_report_date([s])
        assert s.metadata["report_date"] == "2026-09-01T00:00:00"

    def test_stamps_once_without_calling_fetcher_twice(self) -> None:
        fetcher = FakeCotFetcher(datetime(2026, 9, 8))
        gen = self._gen(fetcher)
        gen._stamp_report_date([_signal(), _signal(), _signal()])
        assert fetcher.calls == 1

    def test_stale_beyond_14_days_gets_warning(self) -> None:
        """周报正常滞后 ≤9 天; 超 14 天才说明漏了发布, 避免正常运行天天误报."""
        gen = self._gen(FakeCotFetcher(datetime.now() - timedelta(days=20)))
        out = gen._stamp_report_date([_signal()])
        assert "⚠️滞后20天" in out[0].description

    def test_normal_weekly_lag_has_no_warning(self) -> None:
        gen = self._gen(FakeCotFetcher(datetime.now() - timedelta(days=9)))
        out = gen._stamp_report_date([_signal()])
        assert "⚠️" not in out[0].description
        assert "数据日期" in out[0].description

    def test_empty_signal_list_is_passthrough(self) -> None:
        fetcher = FakeCotFetcher(datetime(2026, 9, 8))
        assert self._gen(fetcher)._stamp_report_date([]) == []
        assert fetcher.calls == 0, "空列表不应触发取数"

    @pytest.mark.parametrize(
        "fetcher",
        [
            FakeCotFetcher(None, status="no_data"),
            FakeCotFetcher(datetime(2026, 9, 8), status="error"),
        ],
    )
    def test_missing_or_failed_summary_leaves_signals_unchanged(self, fetcher) -> None:
        s = _signal()
        before = s.description
        out = self._gen(fetcher)._stamp_report_date([s])
        assert out[0].description == before

    def test_unparseable_date_leaves_signals_unchanged(self) -> None:
        fetcher = FakeCotFetcher(datetime(2026, 9, 8))
        fetcher._report_date = "not-a-date"  # type: ignore[assignment]
        s = _signal()
        before = s.description
        out = self._gen(fetcher)._stamp_report_date([s])
        assert out[0].description == before

    def test_fetcher_exception_is_swallowed(self) -> None:
        class Boom:
            def fetch_net_position(self, weeks: int = 4) -> dict:
                raise RuntimeError("网络炸了")

        s = _signal()
        before = s.description
        out = self._gen(Boom())._stamp_report_date([s])
        assert out[0].description == before, "取数失败不应连带丢掉信号"


class TestLatestFiledQuarter:
    """13F 须于季末后 45 天内申报 —— 不能用「当前季度」标注."""

    @pytest.mark.parametrize(
        ("day", "expected"),
        [
            (datetime(2026, 9, 17), "Q2 2026"),   # 事故日: 旧逻辑给 Q3 2026 (不可能已申报)
            (datetime(2026, 8, 14), "Q2 2026"),   # Q2 末 +45d 当天 → 可用
            (datetime(2026, 8, 13), "Q1 2026"),   # +44d → 还不可用
            (datetime(2026, 5, 20), "Q1 2026"),
            (datetime(2026, 3, 1), "Q4 2025"),    # 跨年回退
            (datetime(2026, 1, 1), "Q3 2025"),
        ],
    )
    def test_45_day_filing_window(self, day: datetime, expected: str) -> None:
        assert Institutional13FFetcher._latest_filed_quarter(day) == expected

    def test_never_returns_a_future_unfiled_quarter(self) -> None:
        """任何日期下, 返回的季度末都必须已过 45 天申报窗口."""
        for month in range(1, 13):
            day = datetime(2026, month, 15)
            label = Institutional13FFetcher._latest_filed_quarter(day)
            q = int(label[1])
            year = int(label.split()[1])
            end_month = q * 3
            q_end = (
                datetime(year, 12, 31)
                if end_month == 12
                else datetime(year, end_month + 1, 1) - timedelta(days=1)
            )
            assert (day - q_end).days >= 45, f"{day.date()} 得到 {label}, 申报窗未过"


class TestFallbackSummaryIsMarked:
    def test_fallback_is_flagged_as_placeholder(self) -> None:
        s = Institutional13FFetcher()._fallback_summary()
        assert s.is_placeholder is True

    def test_fallback_quarter_is_a_filed_quarter(self) -> None:
        s = Institutional13FFetcher()._fallback_summary()
        assert s.quarter == Institutional13FFetcher._latest_filed_quarter()
        assert not s.quarter.startswith("QQ")

    def test_fetch_latest_quarter_always_falls_back(self) -> None:
        """上游 _fetch_whalewisdom 恒返回 [] —— 这个事实本身要被锁定, 防止
        有人误以为 13F 信号来自真实 filing."""
        assert Institutional13FFetcher()._fetch_whalewisdom() == []
        assert Institutional13FFetcher().fetch_latest_quarter().is_placeholder is True


class TestMarkPlaceholder:
    def _sig(self, score: float = 0.15) -> Signal:
        return Signal(
            name="13F机构净增持黄金",
            dimension="smart_money",
            direction=SignalDirection.BULLISH,
            strength=SignalStrength.WEAK,
            score=score,
            description="4家增持 vs 3家减持，机构方向偏多",
            metadata={"source": "13f_institutional"},
        )

    def test_placeholder_zeroes_score_and_discloses(self) -> None:
        out = InstitutionalSignalGenerator._mark_placeholder([self._sig()], True, "Q2 2026")
        s = out[0]
        assert s.score == 0.0, "占位常量不含信息, 唯一诚实的分数是 0"
        assert s.description.startswith("[占位数据·非真实13F]")
        assert s.metadata["is_real_data"] is False
        assert s.metadata["quarter"] == "Q2 2026"

    def test_real_data_score_untouched(self) -> None:
        out = InstitutionalSignalGenerator._mark_placeholder([self._sig(0.15)], False, "Q2 2026")
        assert out[0].score == 0.15
        assert not out[0].description.startswith("[占位")
        assert out[0].metadata["is_real_data"] is True

    def test_signal_is_kept_not_deleted(self) -> None:
        """保留信号而非删除 —— 静默消失会让人误以为机构持仓「无异常」."""
        out = InstitutionalSignalGenerator._mark_placeholder([self._sig()], True, "Q2 2026")
        assert len(out) == 1

    def test_empty_list(self) -> None:
        assert InstitutionalSignalGenerator._mark_placeholder([], True, "Q2 2026") == []


class Test13FSignalIntegration:
    def _gen(self, summary: InstitutionalSummary) -> InstitutionalSignalGenerator:
        class FakeFetcher:
            def fetch_latest_quarter(self) -> InstitutionalSummary:
                return summary

        gen = object.__new__(InstitutionalSignalGenerator)
        gen.inst_13f_fetcher = FakeFetcher()
        return gen

    def _summary(self, placeholder: bool) -> InstitutionalSummary:
        s = Institutional13FFetcher()._fallback_summary()
        return s if placeholder else InstitutionalSummary(
            quarter=s.quarter,
            total_institutions=s.total_institutions,
            net_gold_bullish=s.net_gold_bullish,
            net_gold_bearish=s.net_gold_bearish,
            top_buyers=s.top_buyers,
            is_placeholder=False,
        )

    def test_placeholder_signals_are_neutralised(self) -> None:
        sigs = self._gen(self._summary(True))._institutional_13f_signals()
        assert sigs, "占位路径也应产出信号 (供报告显式披露)"
        assert all(s.score == 0.0 for s in sigs)
        assert all(s.metadata["is_real_data"] is False for s in sigs)

    def test_real_signals_keep_their_scores(self) -> None:
        sigs = self._gen(self._summary(False))._institutional_13f_signals()
        assert sigs
        assert all(s.score != 0.0 for s in sigs)
        assert all(s.metadata["is_real_data"] is True for s in sigs)

    def test_no_double_q_in_description(self) -> None:
        """事故症状: 模板 f"Q{quarter}" + quarter 已含 Q → "QQ3 2026"."""
        sigs = self._gen(self._summary(True))._institutional_13f_signals()
        assert not any("QQ" in s.description for s in sigs)

    def test_berkshire_signal_carries_quarter(self) -> None:
        sigs = self._gen(self._summary(True))._institutional_13f_signals()
        bh = [s for s in sigs if "Berkshire" in s.name]
        if bh:
            assert bh[0].metadata["quarter"] == Institutional13FFetcher._latest_filed_quarter()
