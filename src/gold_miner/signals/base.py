"""信号基类与通用类型."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class SignalDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class SignalStrength(str, Enum):
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
