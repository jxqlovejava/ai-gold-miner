"""持仓管理器 — 从YAML加载、市值追踪、军规自动检查."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml
from loguru import logger


@dataclass
class Position:
    instrument: str
    platform: str
    grams: float
    avg_cost: float
    hard_stop: float
    warn_line: float

    def market_value(self, current_price: float) -> float:
        return self.grams * current_price

    def pnl(self, current_price: float) -> float:
        return (current_price - self.avg_cost) * self.grams

    def pnl_pct(self, current_price: float) -> float:
        return (current_price - self.avg_cost) / self.avg_cost * 100

    def distance_to_stop(self, current_price: float) -> float:
        return (current_price - self.hard_stop) / current_price * 100


@dataclass
class PortfolioSnapshot:
    positions: list[Position]
    current_price: float
    total_value: float
    total_cost: float
    total_pnl: float
    total_pnl_pct: float
    cash: float
    gold_allocation_pct: float
    timestamp: datetime = field(default_factory=datetime.now)


class PortfolioTracker:
    """持仓追踪器 — 加载YAML配置，计算实时风险指标."""

    def __init__(self, config_path: str | None = None) -> None:
        self.config_path = Path(config_path or "data/portfolio.yaml")
        self.positions: list[Position] = []
        self.total_funds: float = 200_000
        self.max_gold_pct: float = 0.80
        self.max_single_pct: float = 0.20
        self.risk_profile: str = "balanced"
        self._load()

    def _load(self) -> None:
        if not self.config_path.exists():
            logger.warning(f"持仓配置文件不存在: {self.config_path}")
            return
        with open(self.config_path) as f:
            data = yaml.safe_load(f) or {}
        limits = data.get("limits", {})
        self.total_funds = limits.get("total_funds", 200_000)
        self.max_gold_pct = limits.get("max_gold_pct", 80) / 100
        self.max_single_pct = limits.get("max_single_pct", 20) / 100
        self.risk_profile = limits.get("risk_profile", "balanced")
        self.positions = []
        for key, cfg in data.get("positions", {}).items():
            self.positions.append(Position(
                instrument=cfg["instrument"],
                platform=cfg.get("platform", ""),
                grams=float(cfg["grams"]),
                avg_cost=float(cfg["avg_cost"]),
                hard_stop=float(cfg["hard_stop"]),
                warn_line=float(cfg["warn_line"]),
            ))

    def reload(self) -> None:
        self._load()

    def snapshot(self, current_price: float) -> PortfolioSnapshot:
        total_value = 0.0
        total_cost = 0.0
        for pos in self.positions:
            total_value += pos.market_value(current_price)
            total_cost += pos.grams * pos.avg_cost
        total_pnl = total_value - total_cost
        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0
        invested = total_cost
        cash = self.total_funds - invested
        gold_pct = (total_value / self.total_funds * 100) if self.total_funds else 0
        return PortfolioSnapshot(
            positions=self.positions,
            current_price=current_price,
            total_value=total_value,
            total_cost=total_cost,
            total_pnl=total_pnl,
            total_pnl_pct=total_pnl_pct,
            cash=cash,
            gold_allocation_pct=gold_pct,
        )

    def check_rules(self, current_price: float) -> list[str]:
        """检查r001-r015军规，返回违规项描述."""
        snap = self.snapshot(current_price)
        violations: list[str] = []

        if snap.gold_allocation_pct > self.max_gold_pct * 100:
            violations.append(f"r002: 黄金占比{snap.gold_allocation_pct:.0f}%超过上限{self.max_gold_pct*100:.0f}%")
        if snap.gold_allocation_pct > 50:
            violations.append(f"r003: 黄金占比{snap.gold_allocation_pct:.0f}%偏高，集中风险")
        if abs(snap.total_pnl_pct) > 10:
            violations.append(f"浮亏{snap.total_pnl_pct:.1f}%超过10%心理临界线")
        for pos in snap.positions:
            if current_price <= pos.warn_line:
                violations.append(f"r014: {pos.instrument}触发预警线{pos.warn_line}元/克")
            if current_price <= pos.hard_stop:
                violations.append(f"r014: {pos.instrument}触达硬止损{pos.hard_stop}元/克！")

        return violations

    def risk_summary(self, current_price: float) -> str:
        snap = self.snapshot(current_price)
        violations = self.check_rules(current_price)
        metrics = self.risk_metrics(current_price)
        lines = [
            f"持仓: {sum(p.grams for p in snap.positions):.1f}克 | "
            f"市值¥{snap.total_value:,.0f} | "
            f"盈亏{snap.total_pnl:+,.0f}元({snap.total_pnl_pct:+.1f}%)",
            f"黄金占比: {snap.gold_allocation_pct:.0f}% | "
            f"现金: ¥{snap.cash:,.0f} | "
            f"风险: {self.risk_profile}",
            f"VaR(95%): ¥{metrics.get('var_95', 0):,.0f} | "
            f"最大回撤: {metrics.get('max_drawdown_pct', 0):.2f}% | "
            f"夏普: {metrics.get('sharpe_ratio', 0):.2f}",
        ]
        if violations:
            lines.append("军规违规: " + "; ".join(violations))
        else:
            lines.append("军规检查通过")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 风险指标
    # ------------------------------------------------------------------

    def risk_metrics(self, current_price: float, price_history: list[float] | None = None) -> dict[str, float]:
        """计算风险指标: VaR / 最大回撤 / 夏普比率."""
        snap = self.snapshot(current_price)
        metrics: dict[str, float] = {
            "var_95": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe_ratio": 0.0,
            "current_drawdown_pct": 0.0,
        }

        # 当前回撤
        peak_cost = max(p.avg_cost for p in snap.positions) if snap.positions else current_price
        if peak_cost > 0:
            metrics["current_drawdown_pct"] = (current_price - peak_cost) / peak_cost * 100

        # VaR 95% 基于历史波动率
        if price_history and len(price_history) > 5:
            import numpy as np
            returns = np.diff(price_history) / np.array(price_history[:-1])
            if len(returns) > 0:
                volatility = float(np.std(returns))
                var_95 = 1.65 * volatility * snap.total_value  # 95%置信度
                metrics["var_95"] = abs(var_95)

                # 夏普 (假设无风险利率2%)
                excess = np.mean(returns) * 252 - 0.02
                if volatility > 0:
                    metrics["sharpe_ratio"] = float(excess / (volatility * np.sqrt(252)))

        # 最大回撤
        if price_history and len(price_history) > 2:
            import numpy as np
            peak = np.maximum.accumulate(np.array(price_history))
            drawdowns = (peak - np.array(price_history)) / peak * 100
            metrics["max_drawdown_pct"] = float(np.max(drawdowns))

        return metrics
