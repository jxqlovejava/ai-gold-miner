"""COT 信号去重 (方案A) 回归测试.

背景 (2026-08-11): 同一底层事实「非商业净多变化」被两个信号重复加权 —
  - COT聪明钱减仓 (趋势信号, 4周趋势)  score -0.80
  - COT一致看空信号 (aligned 分歧, 单期) score -0.30
修复: 趋势信号与同向 aligned 分歧信号合并进趋势信号, 不单独发分歧信号;
divergence_* 背离信号 (方向与趋势相反) 或趋势信号缺失时独立触发。
"""
from __future__ import annotations

from gold_miner.signals.base import Signal, SignalDirection, SignalStrength
from gold_miner.signals.cot_signal import CotSignalGenerator


def _trend(direction: str, score: float) -> Signal:
    return Signal(
        name="COT聪明钱减仓" if direction == "bearish" else "COT聪明钱加仓",
        dimension="sentiment",
        direction=SignalDirection(direction),
        strength=SignalStrength.STRONG,
        score=score,
        description="非商业净多仓连续减少: 197,634手 (-27,366, -12.2%)",
        metadata={"source": "cot_report", "trend": direction},
    )


def _aligned(direction: str) -> Signal:
    return Signal(
        name="COT一致看空信号" if direction == "bearish" else "COT一致看多信号",
        dimension="sentiment",
        direction=SignalDirection(direction),
        strength=SignalStrength.MODERATE,
        score=-0.3 if direction == "bearish" else 0.3,
        description="非商业减仓 + 商业套保增加，多空一致看空"
        if direction == "bearish"
        else "非商业加仓 + 商业套保减少，多空一致看多",
        metadata={"source": "cot_report", "pattern": f"aligned_{direction}"},
    )


def _divergence(direction: str) -> Signal:
    """divergence_* 背离: 方向与聪明钱趋势相反."""
    return Signal(
        name="COT持仓背离: 商业减套保" if direction == "bullish" else "COT持仓背离: 商业加套保",
        dimension="sentiment",
        direction=SignalDirection(direction),
        strength=SignalStrength.WEAK,
        score=0.15 if direction == "bullish" else -0.15,
        description="聪明钱减仓但商业套保减少，Producer 端偏乐观"
        if direction == "bullish"
        else "聪明钱加仓但商业套保增加，Producer 端偏悲观",
        metadata={"source": "cot_report", "pattern": f"divergence_{direction}"},
    )


class TestMergeTrendAndDivergence:
    def test_bearish_trend_merges_aligned_bearish(self) -> None:
        """同向 aligned 分歧合并进趋势信号, 只输出 1 个信号, 提分 + 标注商业确认."""
        trend = _trend("bearish", -0.80)
        aligned = _aligned("bearish")
        merged = CotSignalGenerator._merge_trend_and_divergence(
            [trend], [aligned]
        )
        assert len(merged) == 1
        s = merged[0]
        assert s.direction is SignalDirection.BEARISH
        assert s.score == -0.95  # -0.80 + (-0.30) 合并, 提分
        assert s.metadata.get("commercial_confirmation") is True
        assert "商业套保同向确认" in s.description
        # 名称保持趋势信号, 不出现独立分歧信号名
        assert s.name == "COT聪明钱减仓"

    def test_bullish_trend_merges_aligned_bullish(self) -> None:
        trend = _trend("bullish", 0.80)
        aligned = _aligned("bullish")
        merged = CotSignalGenerator._merge_trend_and_divergence(
            [trend], [aligned]
        )
        assert len(merged) == 1
        assert merged[0].score == 0.95  # 0.80 + 0.30 合并
        assert merged[0].direction is SignalDirection.BULLISH

    def test_score_capped_at_095(self) -> None:
        trend = _trend("bearish", -0.80)
        aligned = _aligned("bearish")
        # 合并不超过 ±0.95, 防止极端分
        merged = CotSignalGenerator._merge_trend_and_divergence(
            [trend], [aligned]
        )
        assert abs(merged[0].score) <= 0.95

    def test_divergence_opposite_direction_stays_independent(self) -> None:
        """背离信号 (方向与趋势相反) 独立触发, 不被合并 — 携带独立信息."""
        trend = _trend("bearish", -0.80)  # 聪明钱减仓
        divergence = _divergence("bullish")  # 但商业减套保, Producer 偏乐观
        merged = CotSignalGenerator._merge_trend_and_divergence(
            [trend], [divergence]
        )
        assert len(merged) == 2  # 趋势 + 背离信号都保留
        names = {s.name for s in merged}
        assert names == {"COT聪明钱减仓", "COT持仓背离: 商业减套保"}
        trend_sig = [s for s in merged if s.name == "COT聪明钱减仓"][0]
        assert trend_sig.score == -0.80  # 未被提分 (没合并)

    def test_no_trend_emits_divergence_independently(self) -> None:
        """趋势信号缺失 → aligned 分歧信号独立触发."""
        aligned = _aligned("bearish")
        merged = CotSignalGenerator._merge_trend_and_divergence([], [aligned])
        assert len(merged) == 1
        assert merged[0].name == "COT一致看空信号"
        assert merged[0].score == -0.3
        assert "commercial_confirmation" not in merged[0].metadata

    def test_trend_missing_and_divergence_pattern(self) -> None:
        """趋势缺失时 divergence_* 也独立触发."""
        divergence = _divergence("bullish")
        merged = CotSignalGenerator._merge_trend_and_divergence([], [divergence])
        assert len(merged) == 1
        assert merged[0].name == "COT持仓背离: 商业减套保"
