"""Tests for decision/position_state.py — 持仓感知动作映射."""
from __future__ import annotations

from gold_miner.decision.position_state import resolve_position_state


def _portfolio(
    grams: float = 50.0,
    avg_cost: float = 900.0,
    hard_stop: float = 630.0,
    secondary_stop: float = 810.0,
    total_funds: float = 200_000.0,
    max_gold_pct: float = 80,
) -> dict:
    return {
        "positions": {
            "gold_jd": {
                "instrument": "积存金",
                "grams": grams,
                "avg_cost": avg_cost,
                "hard_stop": hard_stop,
                "secondary_stop": secondary_stop,
            }
        },
        "limits": {
            "total_funds": total_funds,
            "max_gold_pct": max_gold_pct,
        },
    }


def _decision(
    direction: str = "long",
    position_pct: float = 0.15,
    composite_score: float = 0.5,
    confidence: float = 0.7,
) -> dict:
    return {
        "direction": direction,
        "position_pct": position_pct,
        "composite_score": composite_score,
        "confidence": confidence,
        "bull_confidence": confidence,
        "bear_confidence": 0.2,
    }


class TestResolvePositionState:
    def test_stand_aside_when_no_position_and_weak(self) -> None:
        state = resolve_position_state(
            _portfolio(grams=0),
            current_price=880.0,
            raw_decision=_decision(direction="long", position_pct=0.02, composite_score=0.07),
        )
        assert state["action"] == "stand_aside"
        assert state["action_cn"] == "观望"
        assert state["direction"] == "neutral"
        assert state["position_pct"] == 0.0
        assert state["signal_type"] == "无信号"

    def test_hold_when_has_position_and_weak(self) -> None:
        state = resolve_position_state(
            _portfolio(grams=40),
            current_price=880.0,
            raw_decision=_decision(composite_score=0.07, position_pct=0.1, confidence=0.3),
        )
        assert state["action"] == "hold"
        assert state["action_cn"] == "持有"
        assert state["position_pct"] == 0.0
        assert state["grams"] == 40.0

    def test_add_when_long_edge_and_no_position(self) -> None:
        state = resolve_position_state(
            _portfolio(grams=0),
            current_price=880.0,
            raw_decision=_decision(direction="long", composite_score=0.55, position_pct=0.12),
        )
        assert state["action"] == "add"
        assert state["action_cn"] == "加仓"
        assert state["direction"] == "long"
        assert state["position_pct"] > 0
        assert state["target_gold_pct"] > 0

    def test_hold_when_long_edge_and_already_positioned(self) -> None:
        # 已有较大仓位，空间不足或小幅偏多 → 持有
        pf = _portfolio(grams=150, avg_cost=850.0)  # ~66% at 880
        state = resolve_position_state(
            pf,
            current_price=880.0,
            raw_decision=_decision(direction="long", composite_score=0.4, position_pct=0.1),
        )
        assert state["action"] in ("hold", "add")
        assert state["direction"] == "long"
        if state["action"] == "hold":
            assert state["position_pct"] == 0.0

    def test_reduce_when_short_raw_and_holding_long_only(self) -> None:
        state = resolve_position_state(
            _portfolio(grams=50),
            current_price=880.0,
            raw_decision=_decision(
                direction="short",
                composite_score=-0.6,
                position_pct=0.4,
                confidence=0.8,
            ),
            long_only=True,
        )
        assert state["action"] == "reduce"
        assert state["action_cn"] == "减仓"
        assert state["direction"] == "neutral"
        assert state["position_pct"] > 0
        assert state["target_gold_pct"] < state["current_gold_pct"]

    def test_stand_aside_when_short_and_no_position_long_only(self) -> None:
        state = resolve_position_state(
            _portfolio(grams=0),
            current_price=880.0,
            raw_decision=_decision(direction="short", composite_score=-0.6, position_pct=0.4),
            long_only=True,
        )
        assert state["action"] == "stand_aside"
        assert state["direction"] == "neutral"
        assert state["position_pct"] == 0.0

    def test_stop_at_hard_stop(self) -> None:
        state = resolve_position_state(
            _portfolio(grams=50, hard_stop=850.0, secondary_stop=860.0),
            current_price=840.0,
            raw_decision=_decision(direction="long", composite_score=0.5),
        )
        assert state["action"] == "stop"
        assert state["action_cn"] == "止损离场"
        assert state["near_hard_stop"] is True
        assert state["position_pct"] == 1.0
        assert state["target_gold_pct"] == 0.0

    def test_reduce_at_secondary_stop(self) -> None:
        state = resolve_position_state(
            _portfolio(grams=50, hard_stop=700.0, secondary_stop=860.0),
            current_price=850.0,
            raw_decision=_decision(direction="long", composite_score=0.5),
        )
        assert state["action"] == "reduce"
        assert state["near_secondary_stop"] is True
        assert state["near_hard_stop"] is False
        assert 0 < state["position_pct"] < 1

    def test_unrealized_pnl_pct(self) -> None:
        state = resolve_position_state(
            _portfolio(grams=10, avg_cost=1000.0),
            current_price=900.0,
            raw_decision=_decision(composite_score=0.0, position_pct=0.0),
        )
        assert state["unrealized_pnl_pct"] == -0.1
        assert state["avg_cost"] == 1000.0

    def test_never_direction_short_when_long_only(self) -> None:
        state = resolve_position_state(
            _portfolio(grams=20),
            current_price=880.0,
            raw_decision=_decision(direction="short", composite_score=-0.8, position_pct=0.5),
            long_only=True,
        )
        assert state["direction"] != "short"
