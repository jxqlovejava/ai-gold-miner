"""机构资金流评估与加仓闸门.

核心原则（散户视角）：
  机构总体净流出时，禁止新建仓 / 加仓；持有与减仓不受此闸门强制。

证据源（按可靠性）：
  - 国际黄金 ETF 持仓吨数（GLD 等，日频，T0/T1）
  - CFTC COT 非商业净仓（周频）
  - 聪明钱综合信号
  - 13F（季度，权重较低）
  - 国内 ETF 价格/成交量 proxy **不计入** 机构真流
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from gold_miner.signals.base import Signal, SignalDirection

# 不计入机构真流的 proxy 标记
_PROXY_SOURCES = frozenset({
    "gold_etf_price_proxy",
    "gold_etf_volume_proxy",
    "gld_volume_proxy",
})

# 名称关键字 → (方向权重倍率, 标签)
# 方向: 正=流入/加仓, 负=流出/减仓
_NAME_RULES: list[tuple[tuple[str, ...], float, str]] = [
    (("国际黄金ETF大幅流出",), -1.0, "intl_etf_strong_outflow"),
    (("国际黄金ETF资金流出",), -0.7, "intl_etf_outflow"),
    (("国际黄金ETF大幅流入",), 1.0, "intl_etf_strong_inflow"),
    (("国际黄金ETF资金流入",), 0.7, "intl_etf_inflow"),
    (("COT聪明钱减仓",), -0.85, "cot_outflow"),
    (("COT聪明钱加仓",), 0.85, "cot_inflow"),
    (("聪明钱综合信号",), 1.0, "smart_money_composite"),  # 方向看 score
    (("13F机构大举增持", "13F机构净增持"), 0.4, "13f_inflow"),
    (("13F机构净减持", "13F机构大举减持"), -0.4, "13f_outflow"),
]


@dataclass
class InstitutionalFlowAssessment:
    """机构资金流评估结果."""

    status: str = "unknown"  # outflow | inflow | neutral | unknown
    net_score: float = 0.0  # [-1, 1]，负=净流出
    block_add: bool = False
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    has_real_data: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_proxy(sig: Signal) -> bool:
    meta = sig.metadata or {}
    if meta.get("is_real_flow") is False:
        return True
    src = str(meta.get("source") or "")
    if src in _PROXY_SOURCES:
        return True
    name = sig.name or ""
    return "(proxy)" in name or "proxy" in name.lower() and "国际" not in name


def _evidence_from_signal(sig: Signal) -> dict[str, Any] | None:
    """从单条信号提取机构流证据；非机构/ proxy 返回 None."""
    if _is_proxy(sig):
        return None

    name = sig.name or ""
    score = float(sig.score or 0.0)
    meta = sig.metadata or {}
    source = str(meta.get("source") or "")

    # 聪明钱综合：直接用 score
    if "聪明钱综合" in name or source == "smart_money_composite":
        if abs(score) < 0.15:
            return None
        weight = min(abs(score), 1.0)
        direction = 1.0 if score > 0 else -1.0
        return {
            "name": name,
            "label": "smart_money_composite",
            "contribution": round(direction * weight, 3),
            "score": score,
            "source": source or "smart_money_composite",
        }

    for keys, mult, label in _NAME_RULES:
        if any(k in name for k in keys):
            # 名称规则已含方向；用 |score| 调节强度，缺省 0.5
            strength = abs(score) if abs(score) > 0.05 else 0.5
            if mult == 1.0 and label == "smart_money_composite":
                continue
            contribution = mult * min(strength, 1.0)
            # 若名称方向与 score 方向明显冲突，以 score 为准
            if score != 0 and contribution * score < 0 and abs(score) >= 0.2:
                contribution = score
            return {
                "name": name,
                "label": label,
                "contribution": round(contribution, 3),
                "score": score,
                "source": source or label,
            }

    # 元数据兜底：国际 ETF / COT / 13f
    if source in ("gld_holdings_tonnes", "intl_gold_etf", "cot_report", "13f_institutional"):
        if abs(score) < 0.1:
            return None
        return {
            "name": name,
            "label": source,
            "contribution": round(max(-1.0, min(1.0, score)), 3),
            "score": score,
            "source": source,
        }

    return None


def assess_institutional_flow(signals: Iterable[Signal]) -> InstitutionalFlowAssessment:
    """根据信号束评估机构净流入/流出，并决定是否禁止加仓."""
    evidence: list[dict[str, Any]] = []
    for sig in signals:
        if not isinstance(sig, Signal):
            continue
        ev = _evidence_from_signal(sig)
        if ev is not None:
            evidence.append(ev)

    if not evidence:
        return InstitutionalFlowAssessment(
            status="unknown",
            net_score=0.0,
            block_add=False,
            confidence=0.0,
            reasons=["无可用机构资金流信号（国际ETF/COT/聪明钱/13F）"],
            evidence=[],
            has_real_data=False,
        )

    contribs = [float(e["contribution"]) for e in evidence]
    net = sum(contribs) / len(contribs)
    net = max(-1.0, min(1.0, net))

    labels = {e["label"] for e in evidence}
    has_strong_outflow = any(
        e["label"] in ("intl_etf_strong_outflow",) or e["contribution"] <= -0.7
        for e in evidence
    )
    has_cot_out = any(e["label"] == "cot_outflow" for e in evidence)
    has_etf_out = any(
        e["label"] in ("intl_etf_outflow", "intl_etf_strong_outflow") for e in evidence
    )
    has_strong_inflow = any(e["contribution"] >= 0.7 for e in evidence)

    # 状态
    if net <= -0.2 or has_strong_outflow:
        status = "outflow"
    elif net >= 0.2:
        status = "inflow"
    else:
        status = "neutral"

    # 禁止加仓：明确净流出，且非被强流入对冲
    block_add = False
    reasons: list[str] = []
    if status == "outflow" and not has_strong_inflow:
        if has_strong_outflow:
            block_add = True
            reasons.append("国际黄金ETF/机构大幅净流出")
        elif has_etf_out and has_cot_out:
            block_add = True
            reasons.append("国际ETF流出 + COT聪明钱减仓共振")
        elif net <= -0.25 and len(evidence) >= 1:
            block_add = True
            reasons.append(f"机构资金净流评分 {net:+.2f}（偏流出）")
        elif any(e["label"] == "smart_money_composite" and e["contribution"] <= -0.3 for e in evidence):
            block_add = True
            reasons.append("聪明钱综合信号偏空")

    if not reasons:
        if status == "inflow":
            reasons.append(f"机构资金净流评分 {net:+.2f}（偏流入）")
        elif status == "neutral":
            reasons.append(f"机构资金净流评分 {net:+.2f}（中性）")
        else:
            reasons.append(f"机构资金净流评分 {net:+.2f}")

    conf = min(0.4 + 0.15 * len(evidence) + (0.2 if has_strong_outflow or has_strong_inflow else 0), 0.95)

    return InstitutionalFlowAssessment(
        status=status,
        net_score=round(net, 3),
        block_add=block_add,
        confidence=round(conf, 2),
        reasons=reasons,
        evidence=evidence,
        has_real_data=True,
    )


def apply_institutional_outflow_gate(
    decision: dict[str, Any],
    assessment: InstitutionalFlowAssessment,
) -> dict[str, Any]:
    """机构净流出时禁止加仓/新建仓；持有与减仓不动.

    修改字段：
      - action add → hold（有仓）或 stand_aside（无仓）
      - position_pct 置 0（加仓增量）
      - institutional_gate / risk_override 说明
    """
    adjusted = dict(decision)
    adjusted["institutional_flow"] = assessment.to_dict()

    if not assessment.block_add:
        return adjusted

    action = str(adjusted.get("action") or "")
    grams = float((adjusted.get("position_state") or {}).get("grams") or adjusted.get("grams") or 0)
    has_position = grams > 0 or float(adjusted.get("current_gold_pct") or 0) > 0

    gate_msg = "机构净流出，禁止加仓：" + "；".join(assessment.reasons)

    # 明确加仓动作
    if action == "add":
        if has_position:
            adjusted["action"] = "hold"
            adjusted["action_cn"] = "持有"
            adjusted["position_pct"] = 0.0
            adjusted["target_gold_pct"] = float(adjusted.get("current_gold_pct") or 0)
            adjusted["direction"] = "long"
        else:
            adjusted["action"] = "stand_aside"
            adjusted["action_cn"] = "观望"
            adjusted["position_pct"] = 0.0
            adjusted["target_gold_pct"] = 0.0
            adjusted["direction"] = "neutral"
        adjusted["institutional_gate"] = gate_msg
        prev = adjusted.get("risk_override")
        adjusted["risk_override"] = f"{prev}；{gate_msg}" if prev else gate_msg
        reason = adjusted.get("reason") or ""
        adjusted["reason"] = f"{reason} | {gate_msg}" if reason else gate_msg
        return adjusted

    # 无持仓但建议开仓（position_pct>0 且 long）
    pos_pct = float(adjusted.get("position_pct") or 0)
    direction = str(adjusted.get("direction") or "")
    if not has_position and pos_pct > 0 and direction == "long":
        adjusted["action"] = "stand_aside"
        adjusted["action_cn"] = "观望"
        adjusted["position_pct"] = 0.0
        adjusted["target_gold_pct"] = 0.0
        adjusted["direction"] = "neutral"
        adjusted["institutional_gate"] = gate_msg
        prev = adjusted.get("risk_override")
        adjusted["risk_override"] = f"{prev}；{gate_msg}" if prev else gate_msg
        return adjusted

    # 有持仓 hold 但 PM 仍给了正 position_pct 增量 — 清零增量
    if action in ("hold", "stand_aside") and pos_pct > 0 and direction == "long":
        adjusted["position_pct"] = 0.0
        adjusted["institutional_gate"] = gate_msg
        # 不强制改 action，只禁止「加仓额度」

    # 标注评估结果，即使未改动作（例如已经是 hold）
    if assessment.block_add and "institutional_gate" not in adjusted:
        adjusted["institutional_gate"] = gate_msg + "（当前无加仓动作，闸门待命）"

    return adjusted


def signals_indicate_institutional_selling(assessment: InstitutionalFlowAssessment) -> bool:
    """军规 r021/r024 用：是否机构在出货."""
    return assessment.status == "outflow" and assessment.has_real_data


def signals_indicate_etf_flow_available(signals: Iterable[Signal]) -> bool:
    """是否出现非 proxy 的 ETF 流向信号."""
    for sig in signals:
        if not isinstance(sig, Signal):
            continue
        if _is_proxy(sig):
            continue
        name = sig.name or ""
        src = str((sig.metadata or {}).get("source") or "")
        if "国际黄金ETF" in name or src in ("gld_holdings_tonnes", "intl_gold_etf"):
            return True
    return False
