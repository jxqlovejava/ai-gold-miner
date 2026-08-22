"""Tests for signals/base.py — Signal, SignalBundle, and enums."""
from __future__ import annotations

from datetime import datetime

from gold_miner.signals.base import (
    DIMENSION_LABELS,
    Signal,
    SignalBundle,
    SignalDirection,
    SignalStrength,
)


class TestSignalDirection:
    def test_enum_values(self) -> None:
        assert SignalDirection.BULLISH.value == "bullish"
        assert SignalDirection.BEARISH.value == "bearish"
        assert SignalDirection.NEUTRAL.value == "neutral"

    def test_enum_membership(self) -> None:
        assert SignalDirection("bullish") == SignalDirection.BULLISH
        assert SignalDirection("bearish") == SignalDirection.BEARISH
        assert SignalDirection("neutral") == SignalDirection.NEUTRAL


class TestSignalStrength:
    def test_enum_values(self) -> None:
        assert SignalStrength.STRONG.value == "strong"
        assert SignalStrength.MODERATE.value == "moderate"
        assert SignalStrength.WEAK.value == "weak"

    def test_enum_membership(self) -> None:
        assert SignalStrength("strong") == SignalStrength.STRONG
        assert SignalStrength("moderate") == SignalStrength.MODERATE
        assert SignalStrength("weak") == SignalStrength.WEAK


class TestSignal:
    def test_default_timestamp_is_set(self) -> None:
        """Signal should auto-assign a timestamp when not provided."""
        signal = Signal(name="test", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5)
        assert isinstance(signal.timestamp, datetime)

    def test_default_metadata_is_empty_dict(self) -> None:
        signal = Signal(name="test", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5)
        assert signal.metadata == {}

    def test_default_description_is_empty_string(self) -> None:
        signal = Signal(name="test", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5)
        assert signal.description == ""

    def test_all_fields(self) -> None:
        ts = datetime(2025, 1, 15, 10, 30, 0)
        signal = Signal(
            name="RSI超卖",
            dimension="technical",
            direction=SignalDirection.BULLISH,
            strength=SignalStrength.MODERATE,
            score=0.4,
            description="RSI < 30 超卖反弹",
            timestamp=ts,
            metadata={"rsi_value": 25.0},
        )
        assert signal.name == "RSI超卖"
        assert signal.dimension == "technical"
        assert signal.direction == SignalDirection.BULLISH
        assert signal.strength == SignalStrength.MODERATE
        assert signal.score == 0.4
        assert signal.description == "RSI < 30 超卖反弹"
        assert signal.timestamp == ts
        assert signal.metadata == {"rsi_value": 25.0}


class TestSignalBundle:
    def test_empty_bundle(self) -> None:
        bundle = SignalBundle()
        assert bundle.signals == []
        assert bundle.composite_score == 0.0
        assert bundle.confidence == 0.0

    def test_add_signal(self) -> None:
        bundle = SignalBundle()
        signal = Signal(name="test", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5)
        bundle.add(signal)
        assert len(bundle.signals) == 1
        assert bundle.signals[0] == signal

    def test_add_multiple(self) -> None:
        bundle = SignalBundle()
        s1 = Signal(name="a", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5)
        s2 = Signal(name="b", dimension="news", direction=SignalDirection.BEARISH, strength=SignalStrength.WEAK, score=-0.2)
        bundle.add(s1)
        bundle.add(s2)
        assert len(bundle.signals) == 2

    def test_by_dimension_returns_matching(self) -> None:
        bundle = SignalBundle()
        s1 = Signal(name="a", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5)
        s2 = Signal(name="b", dimension="news", direction=SignalDirection.BEARISH, strength=SignalStrength.WEAK, score=-0.2)
        s3 = Signal(name="c", dimension="technical", direction=SignalDirection.NEUTRAL, strength=SignalStrength.MODERATE, score=0.0)
        bundle.add(s1)
        bundle.add(s2)
        bundle.add(s3)

        tech_signals = bundle.by_dimension("technical")
        assert len(tech_signals) == 2
        assert s1 in tech_signals
        assert s3 in tech_signals

    def test_by_dimension_empty_when_no_match(self) -> None:
        bundle = SignalBundle()
        s = Signal(name="a", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5)
        bundle.add(s)
        assert bundle.by_dimension("fundamental") == []

    def test_bullish_count(self) -> None:
        bundle = SignalBundle()
        bundle.add(Signal(name="a", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5))
        bundle.add(Signal(name="b", dimension="news", direction=SignalDirection.BEARISH, strength=SignalStrength.WEAK, score=-0.2))
        bundle.add(Signal(name="c", dimension="sentiment", direction=SignalDirection.BULLISH, strength=SignalStrength.MODERATE, score=0.3))
        assert bundle.bullish_count() == 2

    def test_bullish_count_zero_when_none(self) -> None:
        bundle = SignalBundle()
        bundle.add(Signal(name="a", dimension="technical", direction=SignalDirection.BEARISH, strength=SignalStrength.STRONG, score=-0.5))
        assert bundle.bullish_count() == 0

    def test_bearish_count(self) -> None:
        bundle = SignalBundle()
        bundle.add(Signal(name="a", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5))
        bundle.add(Signal(name="b", dimension="news", direction=SignalDirection.BEARISH, strength=SignalStrength.WEAK, score=-0.2))
        assert bundle.bearish_count() == 1

    def test_bearish_count_zero_when_none(self) -> None:
        bundle = SignalBundle()
        bundle.add(Signal(name="a", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5))
        assert bundle.bearish_count() == 0

    def test_both_counts_with_neutral(self) -> None:
        """Neutral signals should not count as bullish or bearish."""
        bundle = SignalBundle()
        bundle.add(Signal(name="a", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5))
        bundle.add(Signal(name="b", dimension="news", direction=SignalDirection.NEUTRAL, strength=SignalStrength.MODERATE, score=0.0))
        assert bundle.bullish_count() == 1
        assert bundle.bearish_count() == 0

    # ── dimension_direction_summary ──

    def test_dimension_direction_summary_basic(self) -> None:
        """3个维度: tech=bullish, news=bearish, sentiment=neutral → dominant 正确."""
        bundle = SignalBundle()
        bundle.add(Signal(name="a", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5))
        bundle.add(Signal(name="b", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.MODERATE, score=0.3))
        bundle.add(Signal(name="c", dimension="news", direction=SignalDirection.BEARISH, strength=SignalStrength.STRONG, score=-0.6))
        bundle.add(Signal(name="d", dimension="sentiment", direction=SignalDirection.NEUTRAL, strength=SignalStrength.WEAK, score=0.0))

        summary = bundle.dimension_direction_summary()
        assert summary["technical"]["dominant"] == "bullish"
        assert summary["technical"]["bullish"] == 2
        assert summary["technical"]["bearish"] == 0
        assert summary["news"]["dominant"] == "bearish"
        assert summary["news"]["bullish"] == 0
        assert summary["news"]["bearish"] == 1
        assert summary["sentiment"]["dominant"] == "insufficient_data"
        assert summary["sentiment"]["insufficient_data"] is True

    def test_dimension_direction_summary_insufficient_data(self) -> None:
        """所有信号都是中性 → 维度标记为 insufficient_data."""
        bundle = SignalBundle()
        bundle.add(Signal(name="a", dimension="technical", direction=SignalDirection.NEUTRAL, strength=SignalStrength.WEAK, score=0.0))
        bundle.add(Signal(name="b", dimension="news", direction=SignalDirection.NEUTRAL, strength=SignalStrength.WEAK, score=0.0))

        summary = bundle.dimension_direction_summary()
        assert summary["technical"]["dominant"] == "insufficient_data"
        assert summary["technical"]["insufficient_data"] is True
        assert summary["news"]["dominant"] == "insufficient_data"

    def test_dimension_direction_summary_tie(self) -> None:
        """同维度看多=看空 → dispute（多空分歧是信息，不是数据缺失）."""
        bundle = SignalBundle()
        bundle.add(Signal(name="a", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.MODERATE, score=0.4))
        bundle.add(Signal(name="b", dimension="technical", direction=SignalDirection.BEARISH, strength=SignalStrength.MODERATE, score=-0.4))

        summary = bundle.dimension_direction_summary()
        assert summary["technical"]["dominant"] == "dispute"
        assert summary["technical"]["dispute"] is True
        assert summary["technical"]["insufficient_data"] is False

    def test_dimension_direction_summary_empty_bundle(self) -> None:
        """空 SignalBundle → 返回空 dict."""
        bundle = SignalBundle()
        assert bundle.dimension_direction_summary() == {}

    def test_dimension_direction_summary_avg_score(self) -> None:
        """avg_score 应为维度内所有信号的均值."""
        bundle = SignalBundle()
        bundle.add(Signal(name="a", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.6))
        bundle.add(Signal(name="b", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.MODERATE, score=0.2))

        summary = bundle.dimension_direction_summary()
        assert summary["technical"]["avg_score"] == 0.4  # (0.6+0.2)/2

    # ── dimension_direction_counts ──

    def test_dimension_direction_counts_mixed(self) -> None:
        """2看多 + 1看空 + 0分歧 + 1数据不足."""
        bundle = SignalBundle()
        bundle.add(Signal(name="a", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5))
        bundle.add(Signal(name="b", dimension="fundamental", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.8))
        bundle.add(Signal(name="c", dimension="news", direction=SignalDirection.BEARISH, strength=SignalStrength.MODERATE, score=-0.5))
        bundle.add(Signal(name="d", dimension="sentiment", direction=SignalDirection.NEUTRAL, strength=SignalStrength.WEAK, score=0.0))

        bull, bear, dispute, insuf = bundle.dimension_direction_counts()
        assert bull == 2
        assert bear == 1
        assert dispute == 0
        assert insuf == 1  # sentiment is neutral-only

    def test_dimension_direction_counts_all_insufficient(self) -> None:
        """所有维度都数据不足."""
        bundle = SignalBundle()
        bundle.add(Signal(name="a", dimension="technical", direction=SignalDirection.NEUTRAL, strength=SignalStrength.WEAK, score=0.0))
        bundle.add(Signal(name="b", dimension="news", direction=SignalDirection.NEUTRAL, strength=SignalStrength.WEAK, score=0.0))

        bull, bear, dispute, insuf = bundle.dimension_direction_counts()
        assert bull == 0
        assert bear == 0
        assert dispute == 0
        assert insuf == 2

    def test_dimension_direction_counts_empty(self) -> None:
        """空 bundle."""
        bundle = SignalBundle()
        bull, bear, dispute, insuf = bundle.dimension_direction_counts()
        assert bull == 0
        assert bear == 0
        assert dispute == 0
        assert insuf == 0

    def test_dimension_direction_counts_with_dispute(self) -> None:
        """含平手维度 → 分歧单独计数，不计入有效方向."""
        bundle = SignalBundle()
        bundle.add(Signal(name="a", dimension="sentiment", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5))
        bundle.add(Signal(name="b", dimension="sentiment", direction=SignalDirection.BEARISH, strength=SignalStrength.STRONG, score=-0.5))
        bundle.add(Signal(name="c", dimension="fundamental", direction=SignalDirection.BULLISH, strength=SignalStrength.MODERATE, score=0.3))

        bull, bear, dispute, insuf = bundle.dimension_direction_counts()
        assert bull == 1      # fundamental
        assert bear == 0
        assert dispute == 1   # sentiment 平手
        assert insuf == 0

    def test_dimension_direction_summary_count_edge_weak_avg(self) -> None:
        """计数一边倒但均分在噪音带内 → 不确认方向（insufficient_data）.

        2多1空, 均分 (0.05+0.05-0.05)/3 ≈ 0.017 < 噪音带 0.10 → 强度不足不确认.
        """
        bundle = SignalBundle()
        bundle.add(Signal(name="a", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.WEAK, score=0.05))
        bundle.add(Signal(name="b", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.WEAK, score=0.05))
        bundle.add(Signal(name="c", dimension="technical", direction=SignalDirection.BEARISH, strength=SignalStrength.WEAK, score=-0.05))

        summary = bundle.dimension_direction_summary()
        assert summary["technical"]["dominant"] == "insufficient_data"
        assert summary["technical"]["insufficient_data"] is True
        assert summary["technical"]["dispute"] is False

    def test_dimension_direction_summary_count_edge_strong_avg(self) -> None:
        """计数一边倒且均分走出噪音带 → 确认方向.

        2多1空, 均分 (0.3+0.2-0.1)/3 ≈ 0.133 >= 0.10 → bullish.
        """
        bundle = SignalBundle()
        bundle.add(Signal(name="a", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.MODERATE, score=0.3))
        bundle.add(Signal(name="b", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.MODERATE, score=0.2))
        bundle.add(Signal(name="c", dimension="technical", direction=SignalDirection.BEARISH, strength=SignalStrength.WEAK, score=-0.1))

        summary = bundle.dimension_direction_summary()
        assert summary["technical"]["dominant"] == "bullish"
        assert summary["technical"]["insufficient_data"] is False

    def test_dimension_direction_summary_bearish_strong_avg(self) -> None:
        """看空一边倒且均分走出噪音带 → bearish."""
        bundle = SignalBundle()
        bundle.add(Signal(name="a", dimension="news", direction=SignalDirection.BEARISH, strength=SignalStrength.STRONG, score=-0.6))
        bundle.add(Signal(name="b", dimension="news", direction=SignalDirection.BEARISH, strength=SignalStrength.MODERATE, score=-0.3))
        bundle.add(Signal(name="c", dimension="news", direction=SignalDirection.NEUTRAL, strength=SignalStrength.WEAK, score=0.0))

        summary = bundle.dimension_direction_summary()
        assert summary["news"]["dominant"] == "bearish"
        assert summary["news"]["insufficient_data"] is False

    def test_dimension_direction_summary_neutral_dilutes_avg(self) -> None:
        """大量中性信号稀释均分 → 即使有方向信号也不确认方向.

        avg = 0.8/11 ≈ 0.073 < 0.10 → insufficient_data（监控类维度常见）.
        """
        bundle = SignalBundle()
        bundle.add(Signal(name="a", dimension="monitor", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.8))
        for i in range(10):
            bundle.add(Signal(name=f"n{i}", dimension="monitor", direction=SignalDirection.NEUTRAL, strength=SignalStrength.WEAK, score=0.0))

        summary = bundle.dimension_direction_summary()
        assert summary["monitor"]["dominant"] == "insufficient_data"
        assert summary["monitor"]["avg_score"] == 0.07  # 0.8/11 四舍五入

    # ── format_dimension_table ──

    def test_format_dimension_table_contains_dimension_names(self) -> None:
        """表格应包含中文维度名称和汇总行（输出语言铁律：维度名必须中文）."""
        bundle = SignalBundle()
        bundle.add(Signal(name="a", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5))
        bundle.add(Signal(name="b", dimension="news", direction=SignalDirection.BEARISH, strength=SignalStrength.MODERATE, score=-0.3))

        table = bundle.format_dimension_table()
        assert "技术面" in table
        assert "消息面" in table
        # 不得泄漏英文 dimension key
        assert "technical" not in table
        assert "news" not in table
        assert "有效维度方向对比" in table  # 汇总行必须有

    def test_format_dimension_table_all_known_dimensions_in_chinese(self) -> None:
        """所有已知维度 key 在表格中均显示中文标签，无英文泄漏."""
        bundle = SignalBundle()
        for dim in DIMENSION_LABELS:
            bundle.add(Signal(
                name=f"sig_{dim}", dimension=dim,
                direction=SignalDirection.NEUTRAL, strength=SignalStrength.WEAK, score=0.0,
            ))

        table = bundle.format_dimension_table()
        for dim, label in DIMENSION_LABELS.items():
            assert label in table, f"维度 {dim} 应显示中文「{label}」"
            assert dim not in table, f"维度 {dim} 不得以英文出现在表格中"

    def test_format_dimension_table_dual_totals(self) -> None:
        """双口径汇总: 维度数对比 + 信号数对比并存, 揭示维度粒度掩盖的背离.

        场景: 资金流 4 子项同归 sentiment 维度, 维度口径看空但信号口径看多,
        双行并列为用户揭示这种背离.
        """
        bundle = SignalBundle()
        # sentiment 维度内 2 多 3 空 → 维度主导看空
        bundle.add(Signal(name="etf_flow", dimension="sentiment", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.6))
        bundle.add(Signal(name="comex", dimension="sentiment", direction=SignalDirection.BULLISH, strength=SignalStrength.WEAK, score=0.1))
        bundle.add(Signal(name="cot", dimension="sentiment", direction=SignalDirection.BEARISH, strength=SignalStrength.STRONG, score=-0.8))
        bundle.add(Signal(name="bank", dimension="sentiment", direction=SignalDirection.BEARISH, strength=SignalStrength.MODERATE, score=-0.5))
        bundle.add(Signal(name="mood", dimension="sentiment", direction=SignalDirection.BEARISH, strength=SignalStrength.WEAK, score=-0.2))
        # 另一维度 1 多 → 维度看多
        bundle.add(Signal(name="gold_demand", dimension="fundamental", direction=SignalDirection.BULLISH, strength=SignalStrength.MODERATE, score=0.3))

        table = bundle.format_dimension_table()
        # 维度口径: 看多 1 维 vs 看空 1 维
        assert "有效维度方向对比: 看多 1维 vs 看空 1维" in table
        # 信号口径: 3 多 3 空
        assert "有效信号方向对比: 看多 3个 vs 看空 3个" in table

    def test_format_dimension_table_empty_bundle(self) -> None:
        """空 bundle → 返回占位字符串."""
        bundle = SignalBundle()
        assert bundle.format_dimension_table() == "(无信号)"

    def test_format_dimension_table_shows_dispute(self) -> None:
        """平手维度在表格中显示 ⚠️ 分歧，且汇总行标注分歧维度数."""
        bundle = SignalBundle()
        bundle.add(Signal(name="a", dimension="sentiment", direction=SignalDirection.BULLISH, strength=SignalStrength.MODERATE, score=0.4))
        bundle.add(Signal(name="b", dimension="sentiment", direction=SignalDirection.BEARISH, strength=SignalStrength.MODERATE, score=-0.4))
        bundle.add(Signal(name="c", dimension="fundamental", direction=SignalDirection.BULLISH, strength=SignalStrength.MODERATE, score=0.3))

        table = bundle.format_dimension_table()
        assert "⚠️ 分歧" in table
        assert "（1维分歧）" in table
