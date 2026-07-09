"""信号基类与通用类型."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from gold_miner.compat import StrEnum
from typing import Any


class SignalDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class SignalStrength(StrEnum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"


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
    ) -> "DimensionConsensus":
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

        if has_consensus:
            direction = "bullish" if bullish > bearish else "bearish"
        else:
            direction = "none"

        return DimensionConsensus(
            active_dimensions=active,
            bullish_dimensions=bullish,
            bearish_dimensions=bearish,
            consensus_direction=direction,
            consensus_ratio=ratio,
            has_consensus=has_consensus,
        )
