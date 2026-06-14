"""持仓风险分层管理 — 核心仓 + 机动仓 + 分级止损.

把单一黄金仓位拆成:
- 核心仓: 长期持有, 只在硬止损 710 无条件离场
- 机动仓: 用于中短线风控, 在 ATR 浮亏轨和 900 二次止损位分批减仓

避免 1929 式崩盘中因"满仓单一品种 + 手动犹豫"导致重伤.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from gold_miner.strategy.trailing_stop import TrailingStopSignal


@dataclass(frozen=True)
class StagedOrder:
    """分级止损订单."""

    trigger_price: float
    action: str
    grams: float
    reason: str


class PositionRiskManager:
    """根据持仓拆分生成精确克数的分级止损方案."""

    def __init__(
        self,
        total_grams: float,
        avg_cost: float,
        core_grams: float | None = None,
        tactical_grams: float | None = None,
        hard_stop: float = 710.0,
        secondary_stop: float = 900.0,
    ) -> None:
        if total_grams <= 0:
            raise ValueError("total_grams 必须大于 0")
        if avg_cost <= 0:
            raise ValueError("avg_cost 必须大于 0")

        if core_grams is None and tactical_grams is None:
            core_grams = round(total_grams * 0.7, 4)
            tactical_grams = round(total_grams - core_grams, 4)
        elif core_grams is None:
            core_grams = round(total_grams - (tactical_grams or 0), 4)
            tactical_grams = round(tactical_grams or 0, 4)
        elif tactical_grams is None:
            tactical_grams = round(total_grams - core_grams, 4)
            core_grams = round(core_grams, 4)
        else:
            core_grams = round(core_grams, 4)
            tactical_grams = round(tactical_grams, 4)

        if abs(core_grams + tactical_grams - total_grams) > 1e-4:
            raise ValueError("核心仓 + 机动仓必须等于总持仓")

        self.total_grams = round(total_grams, 4)
        self.avg_cost = avg_cost
        self.core_grams = core_grams
        self.tactical_grams = tactical_grams
        self.hard_stop = hard_stop
        self.secondary_stop = secondary_stop

    @classmethod
    def from_yaml(cls, path: str | Path) -> "PositionRiskManager":
        """从 portfolio.yaml 加载配置."""
        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        pos = config.get("positions", {}).get("gold_jd", {})
        split = pos.get("split", {})

        return cls(
            total_grams=float(pos.get("grams", 0)),
            avg_cost=float(pos.get("avg_cost", 0)),
            core_grams=float(split["core"]) if "core" in split else None,
            tactical_grams=float(split["tactical"]) if "tactical" in split else None,
            hard_stop=float(pos.get("hard_stop", 710.0)),
            secondary_stop=float(pos.get("secondary_stop", 900.0)),
        )

    def staged_orders(self, signal: TrailingStopSignal | None = None) -> list[StagedOrder]:
        """生成当前应执行的分级止损订单.

        顺序:
        1. ATR 浮亏轨触发: 卖出机动仓一半
        2. 跌破 900: 卖出剩余机动仓
        3. 跌破硬止损 710: 清仓核心仓
        """
        orders: list[StagedOrder] = []
        atr_stop = signal.stop_price if signal else self.secondary_stop

        # 1) ATR 浮亏轨: 减机动仓一半
        half_tactical = round(self.tactical_grams / 2, 4)
        if half_tactical > 0:
            orders.append(
                StagedOrder(
                    trigger_price=round(atr_stop, 2),
                    action="reduce_half_tactical",
                    grams=half_tactical,
                    reason=(
                        f"ATR浮亏轨 {atr_stop:.2f} 触发, "
                        f"卖出机动仓一半 ({half_tactical}g)"
                    ),
                )
            )

        # 2) 二次止损 900: 清掉剩余机动仓
        remaining_tactical = round(self.tactical_grams - half_tactical, 4)
        if remaining_tactical > 0:
            orders.append(
                StagedOrder(
                    trigger_price=round(self.secondary_stop, 2),
                    action="close_tactical",
                    grams=remaining_tactical,
                    reason=(
                        f"跌破 {self.secondary_stop:.2f} 二次止损, "
                        f"清掉剩余机动仓 ({remaining_tactical}g)"
                    ),
                )
            )

        # 3) 硬止损: 清仓核心仓
        if self.core_grams > 0:
            orders.append(
                StagedOrder(
                    trigger_price=round(self.hard_stop, 2),
                    action="close_core",
                    grams=self.core_grams,
                    reason=(
                        f"触及硬止损 {self.hard_stop:.2f}, "
                        f"无条件清仓核心仓 ({self.core_grams}g)"
                    ),
                )
            )

        return orders

    def summary(self) -> dict[str, Any]:
        """返回当前持仓结构摘要."""
        return {
            "total_grams": self.total_grams,
            "avg_cost": self.avg_cost,
            "core_grams": self.core_grams,
            "tactical_grams": self.tactical_grams,
            "hard_stop": self.hard_stop,
            "secondary_stop": self.secondary_stop,
        }
