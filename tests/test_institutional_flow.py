"""机构资金流评估与禁止加仓闸门测试."""
from __future__ import annotations

from gold_miner.decision.institutional_flow import (
    InstitutionalFlowAssessment,
    apply_institutional_outflow_gate,
    assess_institutional_flow,
    signals_indicate_etf_flow_available,
    signals_indicate_institutional_selling,
)
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength


def _sig(
    name: str,
    score: float,
    *,
    direction: SignalDirection | None = None,
    source: str = "",
    is_real_flow: bool | None = None,
) -> Signal:
    if direction is None:
        if score > 0:
            direction = SignalDirection.BULLISH
        elif score < 0:
            direction = SignalDirection.BEARISH
        else:
            direction = SignalDirection.NEUTRAL
    meta: dict = {}
    if source:
        meta["source"] = source
    if is_real_flow is not None:
        meta["is_real_flow"] = is_real_flow
    return Signal(
        name=name,
        dimension="sentiment",
        direction=direction,
        strength=SignalStrength.MODERATE,
        score=score,
        description=name,
        metadata=meta,
    )


class TestAssessInstitutionalFlow:
    def test_empty_signals_unknown_no_block(self) -> None:
        a = assess_institutional_flow([])
        assert a.status == "unknown"
        assert a.block_add is False
        assert a.has_real_data is False

    def test_proxy_etf_ignored(self) -> None:
        a = assess_institutional_flow([
            _sig(
                "国内黄金ETF成交放量(proxy)",
                -0.1,
                source="gold_etf_volume_proxy",
                is_real_flow=False,
            ),
        ])
        assert a.status == "unknown"
        assert a.block_add is False

    def test_strong_intl_etf_outflow_blocks(self) -> None:
        a = assess_institutional_flow([
            _sig(
                "国际黄金ETF大幅流出",
                -0.8,
                source="gld_holdings_tonnes",
                is_real_flow=True,
            ),
        ])
        assert a.status == "outflow"
        assert a.block_add is True
        assert a.has_real_data is True

    def test_etf_plus_cot_outflow_blocks(self) -> None:
        a = assess_institutional_flow([
            _sig("国际黄金ETF资金流出", -0.5, source="gld_holdings_tonnes", is_real_flow=True),
            _sig("COT聪明钱减仓", -0.6, source="cot_report"),
        ])
        assert a.status == "outflow"
        assert a.block_add is True

    def test_composite_bearish_blocks(self) -> None:
        a = assess_institutional_flow([
            _sig(
                "聪明钱综合信号",
                -0.45,
                source="smart_money_composite",
            ),
        ])
        assert a.block_add is True
        assert a.status == "outflow"

    def test_intl_inflow_allows_add(self) -> None:
        a = assess_institutional_flow([
            _sig(
                "国际黄金ETF大幅流入",
                0.8,
                source="gld_holdings_tonnes",
                is_real_flow=True,
            ),
            _sig("COT聪明钱加仓", 0.5, source="cot_report"),
        ])
        assert a.status == "inflow"
        assert a.block_add is False

    def test_strong_inflow_offsets_mild_outflow(self) -> None:
        a = assess_institutional_flow([
            _sig("COT聪明钱减仓", -0.4, source="cot_report"),
            _sig(
                "国际黄金ETF大幅流入",
                0.9,
                source="gld_holdings_tonnes",
                is_real_flow=True,
            ),
        ])
        # 有强流入对冲时不应 block
        assert a.block_add is False


class TestApplyInstitutionalOutflowGate:
    def test_blocks_add_to_hold_when_has_position(self) -> None:
        assessment = InstitutionalFlowAssessment(
            status="outflow",
            net_score=-0.5,
            block_add=True,
            reasons=["国际黄金ETF/机构大幅净流出"],
            has_real_data=True,
        )
        decision = {
            "action": "add",
            "action_cn": "加仓",
            "direction": "long",
            "position_pct": 0.1,
            "current_gold_pct": 0.1,
            "position_state": {"grams": 20.0},
        }
        out = apply_institutional_outflow_gate(decision, assessment)
        assert out["action"] == "hold"
        assert out["action_cn"] == "持有"
        assert out["position_pct"] == 0.0
        assert "禁止加仓" in out.get("institutional_gate", "")
        assert "禁止加仓" in out.get("risk_override", "")

    def test_blocks_add_to_stand_aside_when_empty(self) -> None:
        assessment = InstitutionalFlowAssessment(
            status="outflow",
            net_score=-0.5,
            block_add=True,
            reasons=["COT+ETF"],
            has_real_data=True,
        )
        decision = {
            "action": "add",
            "action_cn": "加仓",
            "direction": "long",
            "position_pct": 0.15,
            "current_gold_pct": 0.0,
            "position_state": {"grams": 0.0},
        }
        out = apply_institutional_outflow_gate(decision, assessment)
        assert out["action"] == "stand_aside"
        assert out["direction"] == "neutral"
        assert out["position_pct"] == 0.0

    def test_hold_not_forced_to_reduce(self) -> None:
        assessment = InstitutionalFlowAssessment(
            status="outflow",
            net_score=-0.4,
            block_add=True,
            reasons=["机构流出"],
            has_real_data=True,
        )
        decision = {
            "action": "hold",
            "action_cn": "持有",
            "direction": "long",
            "position_pct": 0.0,
            "current_gold_pct": 0.1,
            "position_state": {"grams": 21.0},
        }
        out = apply_institutional_outflow_gate(decision, assessment)
        assert out["action"] == "hold"
        assert out["direction"] == "long"

    def test_no_block_when_assessment_allows(self) -> None:
        assessment = InstitutionalFlowAssessment(
            status="inflow",
            net_score=0.4,
            block_add=False,
            has_real_data=True,
        )
        decision = {
            "action": "add",
            "action_cn": "加仓",
            "direction": "long",
            "position_pct": 0.1,
            "current_gold_pct": 0.1,
            "position_state": {"grams": 10.0},
        }
        out = apply_institutional_outflow_gate(decision, assessment)
        assert out["action"] == "add"
        assert out["position_pct"] == 0.1
        assert "institutional_gate" not in out


class TestDoctrineHelpers:
    def test_institutional_selling_flag(self) -> None:
        a = InstitutionalFlowAssessment(status="outflow", has_real_data=True)
        assert signals_indicate_institutional_selling(a) is True
        b = InstitutionalFlowAssessment(status="outflow", has_real_data=False)
        assert signals_indicate_institutional_selling(b) is False

    def test_etf_flow_available(self) -> None:
        assert signals_indicate_etf_flow_available([
            _sig("国际黄金ETF资金流入", 0.4, source="gld_holdings_tonnes", is_real_flow=True),
        ]) is True
        assert signals_indicate_etf_flow_available([
            _sig("国内黄金ETF成交放量(proxy)", -0.1, is_real_flow=False),
        ]) is False
