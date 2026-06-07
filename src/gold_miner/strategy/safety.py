"""安全边际计算 — 波动率/流动性/相关性缓冲."""

from dataclasses import dataclass
from enum import Enum


class MarginType(str, Enum):
    VOLATILITY = "volatility"
    LIQUIDITY = "liquidity"
    CORRELATION = "correlation"
    REGIME = "regime"


@dataclass
class SafetyMargin:
    margin_type: MarginType
    value: float  # 缓冲值 (仓位百分比扣减 or ATR倍数)
    threshold: float
    passed: bool
    description: str


class SafetyMarginCalculator:
    """安全边际计算器."""

    VOLATILITY_THRESHOLD = 0.02  # ATR/SMA > 2% → 加缓冲
    LIQUIDITY_SPREAD_THRESHOLD = 0.001  # 点差 > 0.1%
    CORRELATION_THRESHOLD = 0.7  # DXY相关性 > 0.7 → 极端相关

    def calculate(
        self,
        direction: str = "neutral",
        entry_price: float = 0.0,
        volatility: float = 0.01,
        spread_pct: float = 0.0005,
        dxy_correlation: float = 0.0,
        market_regime: str = "trending",
    ) -> list[SafetyMargin]:
        """计算所有安全边际."""
        margins: list[SafetyMargin] = []

        # 波动率边际
        margins.append(self._volatility_margin(volatility))

        # 流动性边际
        margins.append(self._liquidity_margin(spread_pct))

        # 相关性边际 (DXY-黄金负相关)
        if abs(dxy_correlation) > 0.3:
            margins.append(self._correlation_margin(dxy_correlation))

        # 市场状态边际
        margins.append(self._regime_margin(market_regime))

        return margins

    def _volatility_margin(self, volatility: float) -> SafetyMargin:
        passed = volatility <= self.VOLATILITY_THRESHOLD
        return SafetyMargin(
            margin_type=MarginType.VOLATILITY,
            value=round(volatility, 4),
            threshold=self.VOLATILITY_THRESHOLD,
            passed=passed,
            description=(
                f"波动率 {volatility:.2%} {'正常' if passed else '偏高'}"
                f"{'' if passed else ' -> 加0.5x ATR缓冲'}"
            ),
        )

    def _liquidity_margin(self, spread_pct: float) -> SafetyMargin:
        passed = spread_pct <= self.LIQUIDITY_SPREAD_THRESHOLD
        penalty = max(0, (spread_pct - self.LIQUIDITY_SPREAD_THRESHOLD) * 50)
        return SafetyMargin(
            margin_type=MarginType.LIQUIDITY,
            value=round(spread_pct, 5),
            threshold=self.LIQUIDITY_SPREAD_THRESHOLD,
            passed=passed,
            description=(
                f"点差 {spread_pct:.3%} {'正常' if passed else f'偏大 -> 仓位扣减 {penalty:.0%}'}"
            ),
        )

    def _correlation_margin(self, dxy_correlation: float) -> SafetyMargin:
        abs_corr = abs(dxy_correlation)
        passed = abs_corr <= self.CORRELATION_THRESHOLD
        return SafetyMargin(
            margin_type=MarginType.CORRELATION,
            value=round(dxy_correlation, 3),
            threshold=self.CORRELATION_THRESHOLD,
            passed=passed,
            description=(
                f"黄金-DXY相关性 {dxy_correlation:+.2f}"
                f"{'正常' if passed else ' 极端相关 -> 加0.3x ATR缓冲'}"
            ),
        )

    def _regime_margin(self, regime: str) -> SafetyMargin:
        regime_map = {
            "trending": ("正常", True, ""),
            "ranging": ("震荡市", False, " -> 收紧止损, 减小仓位"),
            "crisis": ("危机模式", False, " -> 大幅减仓, 宽止损"),
            "recovery": ("恢复期", True, ""),
        }
        label, passed, note = regime_map.get(regime, ("未知", True, ""))
        return SafetyMargin(
            margin_type=MarginType.REGIME,
            value=0.0,
            threshold=0.0,
            passed=passed,
            description=f"市场状态: {label}{note}",
        )
