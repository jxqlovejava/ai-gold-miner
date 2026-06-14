"""持仓风险分层管理测试."""

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
        cost_basis=1014.42,
        highest_high=1070.0,
        atr=22.75,
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
    mgr = PositionRiskManager(total_grams=100.0, avg_cost=1014.42)
    assert mgr.core_grams == 70.0
    assert mgr.tactical_grams == 30.0


def test_explicit_split():
    """显式指定核心/机动仓."""
    mgr = PositionRiskManager(
        total_grams=109.1372, avg_cost=1014.42, core_grams=80.0
    )
    assert mgr.core_grams == 80.0
    assert mgr.tactical_grams == 29.1372


def test_invalid_split_raises():
    """拆分之和不等于总持仓应报错."""
    with pytest.raises(ValueError, match="核心仓"):
        PositionRiskManager(
            total_grams=100.0,
            avg_cost=1014.42,
            core_grams=60.0,
            tactical_grams=50.0,
        )


def test_staged_orders_structure():
    """分级止损订单结构正确."""
    mgr = PositionRiskManager(
        total_grams=109.1372, avg_cost=1014.42, core_grams=80.0
    )
    signal = _make_signal(946.16)
    orders = mgr.staged_orders(signal)

    assert len(orders) == 3
    assert all(isinstance(o, StagedOrder) for o in orders)

    # 第一单：机动仓一半
    assert orders[0].action == "reduce_half_tactical"
    assert orders[0].grams == round(29.1372 / 2, 4)
    assert orders[0].trigger_price == 946.16

    # 第二单：剩余机动仓
    assert orders[1].action == "close_tactical"
    assert orders[1].grams == round(29.1372 - orders[0].grams, 4)
    assert orders[1].trigger_price == 900.0

    # 第三单：核心仓
    assert orders[2].action == "close_core"
    assert orders[2].grams == 80.0
    assert orders[2].trigger_price == 710.0


def test_summary():
    """summary 返回持仓结构."""
    mgr = PositionRiskManager(
        total_grams=109.1372, avg_cost=1014.42, core_grams=80.0
    )
    s = mgr.summary()
    assert s["total_grams"] == 109.1372
    assert s["core_grams"] == 80.0
    assert s["tactical_grams"] == 29.1372


def test_from_yaml(tmp_path):
    """从 yaml 配置文件加载."""
    yaml_path = tmp_path / "portfolio.yaml"
    yaml_path.write_text(
        """
positions:
  gold_jd:
    instrument: 积存金
    platform: 京东金融
    grams: 109.1372
    avg_cost: 1014.42
    hard_stop: 710
    secondary_stop: 900
    split:
      core: 80.0
      tactical: 29.1372
limits:
  total_funds: 200000
""",
        encoding="utf-8",
    )

    mgr = PositionRiskManager.from_yaml(yaml_path)
    assert mgr.total_grams == 109.1372
    assert mgr.core_grams == 80.0
    assert mgr.tactical_grams == 29.1372
    assert mgr.hard_stop == 710.0
    assert mgr.secondary_stop == 900.0
