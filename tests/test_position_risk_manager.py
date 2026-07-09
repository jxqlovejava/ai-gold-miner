"""持仓风险分层管理测试."""
from __future__ import annotations

import pytest

from gold_miner.strategy.position_risk_manager import (
    PositionRiskManager,
    StagedOrder,
)
from gold_miner.strategy.trailing_stop import TrailingStopSignal


def _make_signal(stop_price: float, track: str = "loss") -> TrailingStopSignal:
    return TrailingStopSignal(
        timestamp=None,  # type: ignore[arg-type]
        current_price=stop_price - 1,
        cost_basis=500.0,
        highest_high=530.0,
        atr=11.0,
        profit_multiplier=2.5,
        loss_multiplier=3.0,
        stop_price=stop_price,
        track=track,
        triggered=True,
        action="reduce_half",
        reason="test",
    )


def test_default_split():
    """默认按 7:3 拆分核心仓和机动仓."""
    mgr = PositionRiskManager(
        total_grams=100.0, avg_cost=500.0, hard_stop=350.0, secondary_stop=450.0
    )
    assert mgr.core_grams == 70.0
    assert mgr.tactical_grams == 30.0


def test_explicit_split():
    """显式指定核心/机动仓."""
    mgr = PositionRiskManager(
        total_grams=100.0,
        avg_cost=500.0,
        hard_stop=350.0,
        secondary_stop=450.0,
        core_grams=80.0,
    )
    assert mgr.core_grams == 80.0
    assert mgr.tactical_grams == 20.0


def test_invalid_split_raises():
    """拆分之和不等于总持仓应报错."""
    with pytest.raises(ValueError, match="核心仓"):
        PositionRiskManager(
            total_grams=100.0,
            avg_cost=500.0,
            hard_stop=350.0,
            secondary_stop=450.0,
            core_grams=60.0,
            tactical_grams=50.0,
        )


def test_staged_orders_structure():
    """分级止损订单结构正确."""
    mgr = PositionRiskManager(
        total_grams=100.0,
        avg_cost=500.0,
        hard_stop=350.0,
        secondary_stop=450.0,
        core_grams=80.0,
    )
    signal = _make_signal(460.0)
    orders = mgr.staged_orders(signal)

    assert len(orders) == 3
    assert all(isinstance(o, StagedOrder) for o in orders)

    # 第一单：机动仓一半
    assert orders[0].action == "reduce_half_tactical"
    assert orders[0].grams == round(20.0 / 2, 4)
    assert orders[0].trigger_price == 460.0

    # 第二单：剩余机动仓
    assert orders[1].action == "close_tactical"
    assert orders[1].grams == round(20.0 - orders[0].grams, 4)
    assert orders[1].trigger_price == 450.0

    # 第三单：核心仓
    assert orders[2].action == "close_core"
    assert orders[2].grams == 80.0
    assert orders[2].trigger_price == 350.0


def test_summary():
    """summary 返回持仓结构."""
    mgr = PositionRiskManager(
        total_grams=100.0,
        avg_cost=500.0,
        hard_stop=350.0,
        secondary_stop=450.0,
        core_grams=80.0,
    )
    s = mgr.summary()
    assert s["total_grams"] == 100.0
    assert s["core_grams"] == 80.0
    assert s["tactical_grams"] == 20.0


def test_from_yaml(tmp_path):
    """从 yaml 配置文件加载."""
    yaml_path = tmp_path / "portfolio.yaml"
    yaml_path.write_text(
        """
positions:
  gold_jd:
    instrument: 积存金
    platform: 京东金融
    grams: 100.0
    avg_cost: 500.0
    hard_stop: 350
    secondary_stop: 450
    warn_line: 360
    split:
      core: 80.0
      tactical: 20.0
limits:
  total_funds: 200000
""",
        encoding="utf-8",
    )

    mgr = PositionRiskManager.from_yaml(yaml_path)
    assert mgr.total_grams == 100.0
    assert mgr.core_grams == 80.0
    assert mgr.tactical_grams == 20.0
    assert mgr.hard_stop == 350.0
    assert mgr.secondary_stop == 450.0


def test_missing_stop_prices_raise(tmp_path):
    """yaml 中缺少 hard_stop / secondary_stop 应报错."""
    yaml_path = tmp_path / "portfolio.yaml"
    yaml_path.write_text(
        """
positions:
  gold_jd:
    instrument: 积存金
    grams: 100.0
    avg_cost: 500.0
    split:
      core: 80.0
      tactical: 20.0
""",
        encoding="utf-8",
    )

    with pytest.raises(KeyError):
        PositionRiskManager.from_yaml(yaml_path)
