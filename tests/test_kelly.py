"""测试凯利公式仓位计算."""
from __future__ import annotations


from gold_miner.strategy.kelly import kelly_position


class TestKellyPosition:
    def test_strong_bullish_high_confidence(self):
        """强看多 + 高置信度 → 凯利应该给较高仓位."""
        result = kelly_position(composite_score=0.50, confidence=0.85)
        assert result.raw_kelly > 0
        assert result.quarter_kelly > 0
        assert result.suggested_pct > 0.01

    def test_weak_bullish_low_confidence(self):
        """弱看多 + 低置信度 → 凯利应该给很低的仓位."""
        result = kelly_position(composite_score=0.10, confidence=0.50)
        assert result.suggested_pct < 0.05
        assert result.quarter_kelly < 0.20

    def test_bearish_zero_position(self):
        """偏空信号 → 不做空，仓位归零."""
        result = kelly_position(composite_score=-0.50, confidence=0.80)
        assert result.raw_kelly == 0.0
        assert result.suggested_pct == 0.0
        assert not result.is_actionable()

    def test_extreme_signal_capped(self):
        """极端强信号应触及硬上限."""
        result = kelly_position(composite_score=0.95, confidence=0.95, atr_pct=0.01)
        assert result.capped
        assert result.suggested_pct == 0.20

    def test_high_volatility_reduces_position(self):
        """高波动 → 低仓位."""
        low_vol = kelly_position(composite_score=0.30, confidence=0.80, atr_pct=0.01)
        high_vol = kelly_position(composite_score=0.30, confidence=0.80, atr_pct=0.05)
        assert high_vol.suggested_pct < low_vol.suggested_pct
