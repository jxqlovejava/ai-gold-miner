"""信号基类与通用类型."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from gold_miner.compat import StrEnum


class SignalDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class SignalStrength(StrEnum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"


class FactType(StrEnum):
    """信号的事实/解释分类.

    用于区分不可争议的客观数据与需要主观判断的因果推断，
    避免把「解释」当作「事实」引用。
    """

    FACT = "fact"                    # 不可争议的客观数据: 价格、成交量、官方数值
    INTERPRETATION = "interpretation"  # 因果推断: "X 因为 Y 上涨"
    PROJECTION = "projection"         # 预测/展望: "预计 X 将..."
    OPINION = "opinion"               # 机构/分析师主观观点


@dataclass
class Signal:
    name: str
    dimension: str
    direction: SignalDirection
    strength: SignalStrength
    score: float
    description: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    fact_type: FactType = FactType.INTERPRETATION  # 默认保守: 解释而非事实

    @property
    def is_fact(self) -> bool:
        return self.fact_type == FactType.FACT

    @property
    def needs_verification(self) -> bool:
        """需要额外验证的信号类型."""
        return self.fact_type in (FactType.PROJECTION, FactType.OPINION)

    def fact_label(self) -> str:
        """返回中文标签."""
        labels = {
            FactType.FACT: "🔵 事实",
            FactType.INTERPRETATION: "🟡 解释",
            FactType.PROJECTION: "🔮 预测",
            FactType.OPINION: "💬 观点",
        }
        return labels.get(self.fact_type, "❓ 未知")


@dataclass(frozen=True)
class DimensionConsensus:
    """多维度信号共识检测结果."""

    active_dimensions: int       # 有信号的维度数
    bullish_dimensions: int      # 多数信号偏多的维度数
    bearish_dimensions: int      # 多数信号偏空的维度数
    consensus_direction: str     # "bullish" | "bearish" | "none"
    consensus_ratio: float       # 同向维度 / 活跃维度
    has_consensus: bool          # 是否达成共识


@dataclass
class SignalBundle:
    signals: list[Signal] = field(default_factory=list)
    composite_score: float = 0.0
    confidence: float = 0.0

    def add(self, signal: Signal) -> None:
        self.signals.append(signal)

    def by_dimension(self, dimension: str) -> list[Signal]:
        return [s for s in self.signals if s.dimension == dimension]

    def bullish_count(self) -> int:
        return sum(1 for s in self.signals if s.direction == SignalDirection.BULLISH)

    def bearish_count(self) -> int:
        return sum(1 for s in self.signals if s.direction == SignalDirection.BEARISH)

    def dimensional_consensus(
        self,
        min_active_dimensions: int = 4,
        consensus_ratio_threshold: float = 0.75,
    ) -> DimensionConsensus:
        """检测多维度信号是否形成方向共识.

        每个维度内取多数方向，然后统计各维度之间的方向一致性。
        当活跃维度数 ≥ min_active_dimensions 且同向比例 ≥ consensus_ratio_threshold
        时判定为共识形成。

        Args:
            min_active_dimensions: 最少活跃维度数（默认 4）
            consensus_ratio_threshold: 同向比例阈值（默认 0.75）
        """
        if not self.signals:
            return DimensionConsensus(
                active_dimensions=0,
                bullish_dimensions=0,
                bearish_dimensions=0,
                consensus_direction="none",
                consensus_ratio=0.0,
                has_consensus=False,
            )

        # 按维度分组，每个维度取多数方向
        dim_direction: dict[str, str] = {}
        for dim in {s.dimension for s in self.signals}:
            signals_in_dim = self.by_dimension(dim)
            counter = Counter(
                s.direction.value for s in signals_in_dim
                if s.direction != SignalDirection.NEUTRAL
            )
            if not counter:
                continue
            # 取该维度最多信号的方向
            dim_direction[dim] = counter.most_common(1)[0][0]

        active = len(dim_direction)
        bullish = sum(1 for d in dim_direction.values() if d == "bullish")
        bearish = sum(1 for d in dim_direction.values() if d == "bearish")
        majority = max(bullish, bearish)
        ratio = majority / active if active > 0 else 0.0

        has_consensus = (
            active >= min_active_dimensions
            and ratio >= consensus_ratio_threshold
        )

        direction = ("bullish" if bullish > bearish else "bearish") if has_consensus else "none"

        return DimensionConsensus(
            active_dimensions=active,
            bullish_dimensions=bullish,
            bearish_dimensions=bearish,
            consensus_direction=direction,
            consensus_ratio=ratio,
            has_consensus=has_consensus,
        )

    def dimension_direction_summary(self) -> dict[str, dict]:
        """返回每个维度的方向摘要，包含各方向信号计数、主导方向、均分等。

        排除中性信号的维度标记为 insufficient_data。
        用于程序化生成维度总览表，避免手动计数错误。

        Returns:
            dict: key=维度名, value={
                "dominant": "bullish"|"bearish"|"insufficient_data",
                "bullish": int, "bearish": int, "neutral": int,
                "total": int, "avg_score": float,
                "insufficient_data": bool,
            }
        """
        if not self.signals:
            return {}

        summary: dict[str, dict] = {}
        for dim in sorted({s.dimension for s in self.signals}):
            signals_in_dim = self.by_dimension(dim)
            bull = sum(1 for s in signals_in_dim if s.direction == SignalDirection.BULLISH)
            bear = sum(1 for s in signals_in_dim if s.direction == SignalDirection.BEARISH)
            neutral = sum(1 for s in signals_in_dim if s.direction == SignalDirection.NEUTRAL)
            total = len(signals_in_dim)
            avg_score = sum(s.score for s in signals_in_dim) / total if total > 0 else 0.0

            # 排除中性信号后判断主导方向
            non_neutral = bull + bear
            if non_neutral == 0:
                dominant = "insufficient_data"
                insufficient = True
            elif bull > bear:
                dominant = "bullish"
                insufficient = False
            elif bear > bull:
                dominant = "bearish"
                insufficient = False
            else:
                # bull == bear (平手)
                dominant = "insufficient_data"
                insufficient = True

            summary[dim] = {
                "dominant": dominant,
                "bullish": bull,
                "bearish": bear,
                "neutral": neutral,
                "total": total,
                "avg_score": round(avg_score, 2),
                "insufficient_data": insufficient,
            }
        return summary

    def dimension_direction_counts(self) -> tuple[int, int, int]:
        """返回 (看多维度数, 看空维度数, 数据不足维度数)。

        基于 dimension_direction_summary() 的 dominant 字段计算，
        排除数据不足的维度后在有效维度间比较方向。

        Returns:
            tuple: (bullish_dimensions, bearish_dimensions, insufficient_data_dimensions)
        """
        summary = self.dimension_direction_summary()
        bullish = sum(1 for v in summary.values() if v["dominant"] == "bullish")
        bearish = sum(1 for v in summary.values() if v["dominant"] == "bearish")
        insufficient = sum(1 for v in summary.values() if v["insufficient_data"])
        return (bullish, bearish, insufficient)

    def format_dimension_table(self) -> str:
        """生成程序化维度方向总览表，LLM 可直接嵌入报告。

        表格包含每个维度的方向、看多/看空/中性信号数、均分，
        以及汇总行（有效维度间的方向对比）。

        Returns:
            str: 格式化的中文表格字符串
        """
        summary = self.dimension_direction_summary()
        if not summary:
            return "(无信号)"

        lines = [
            "┌──────────────────┬────────────────────┬──────┬──────┬──────┬────────┐",
            "│      维度        │        方向        │ 看多 │ 看空 │ 中性 │  均分  │",
            "├──────────────────┼────────────────────┼──────┼──────┼──────┼────────┤",
        ]

        dir_labels = {
            "bullish": "🟢 看多",
            "bearish": "🔴 看空",
            "insufficient_data": "🟡 数据不足",
        }

        for dim, info in summary.items():
            # 维度名截断至多16字符
            dim_display = dim[:16]
            dir_display = dir_labels.get(info["dominant"], info["dominant"])
            lines.append(
                f"│ {dim_display:<16} │ {dir_display:<18} │ "
                f"{info['bullish']:>4} │ {info['bearish']:>4} │ "
                f"{info['neutral']:>4} │ {info['avg_score']:>+6.2f} │"
            )

        lines.append("└──────────────────┴────────────────────┴──────┴──────┴──────┴────────┘")

        # 汇总行
        bull_dims, bear_dims, insuf_dims = self.dimension_direction_counts()
        active = bull_dims + bear_dims
        if active > 0:
            if bull_dims > bear_dims:
                consensus_note = f"看多 {bull_dims}维 vs 看空 {bear_dims}维"
            elif bear_dims > bull_dims:
                consensus_note = f"看空 {bear_dims}维 vs 看多 {bull_dims}维"
            else:
                consensus_note = f"看多 {bull_dims}维 vs 看空 {bear_dims}维 (平手)"
        else:
            consensus_note = "无有效方向维度"
        if insuf_dims > 0:
            consensus_note += f"（{insuf_dims}维数据不足）"

        lines.append(f"  有效维度方向对比: {consensus_note}")
        return "\n".join(lines)
