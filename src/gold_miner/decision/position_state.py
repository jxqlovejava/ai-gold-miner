"""持仓状态决策 — 将 PortfolioManager 原始方向映射为持仓动作.

仅做多积存金场景：不做空；弱信号不新开仓；结合已有持仓给出
hold/add/reduce/stop/stand_aside。
"""
from __future__ import annotations

from typing import Any


_ACTION_CN = {
    "hold": "持有",
    "add": "加仓",
    "reduce": "减仓",
    "stop": "止损离场",
    "stand_aside": "观望",
}


def _signal_type(position_pct: float, abs_score: float, actionable: bool) -> str:
    """根据最终可执行仓位强度标注信号类型（Kelly/阈值过滤之后）."""
    if not actionable or position_pct <= 0:
        if abs_score < 0.3:
            return "无信号"
        return "弱信号" if abs_score < 0.5 else "中等信号"
    if position_pct > 0.5:
        return "强信号"
    if position_pct > 0.2:
        return "中等信号"
    if position_pct > 0:
        return "弱信号"
    return "无信号"


def _extract_positions(portfolio: dict[str, Any]) -> tuple[float, float, float | None, float | None]:
    """返回 (grams, avg_cost, hard_stop, secondary_stop).

    avg_cost 对多仓按克数加权；止损价优先取主仓（gold_jd / 含最多克数的仓位）.
    """
    positions = portfolio.get("positions") or {}
    if not isinstance(positions, dict) or not positions:
        return 0.0, 0.0, None, None

    # 主仓：优先 gold_jd，否则克数最大
    ordered: list[tuple[str, dict[str, Any]]] = []
    if "gold_jd" in positions and isinstance(positions["gold_jd"], dict):
        ordered.append(("gold_jd", positions["gold_jd"]))
    rest = sorted(
        (
            (k, v)
            for k, v in positions.items()
            if k != "gold_jd" and isinstance(v, dict)
        ),
        key=lambda kv: float(kv[1].get("grams") or 0),
        reverse=True,
    )
    ordered.extend(rest)

    total_grams = 0.0
    cost_sum = 0.0
    hard_stop: float | None = None
    secondary_stop: float | None = None
    primary_avg: float | None = None

    for i, (_key, pos) in enumerate(ordered):
        g = float(pos.get("grams") or 0)
        if g <= 0:
            continue
        ac = float(pos.get("avg_cost") or 0)
        total_grams += g
        cost_sum += g * ac
        if i == 0 or primary_avg is None:
            primary_avg = ac if ac > 0 else primary_avg
            if pos.get("hard_stop") is not None:
                hard_stop = float(pos["hard_stop"])
            if pos.get("secondary_stop") is not None:
                secondary_stop = float(pos["secondary_stop"])

    if total_grams <= 0:
        return 0.0, 0.0, hard_stop, secondary_stop

    # 未实现盈亏以主仓成本为主，无主仓时用加权均价
    avg_cost = primary_avg if primary_avg and primary_avg > 0 else (cost_sum / total_grams)
    return total_grams, avg_cost, hard_stop, secondary_stop


def resolve_position_state(
    portfolio: dict[str, Any],
    current_price: float,
    raw_decision: dict[str, Any],
    *,
    long_only: bool = True,
) -> dict[str, Any]:
    """将原始 PM 决策映射为持仓感知动作.

    Args:
        portfolio: portfolio.yaml 结构（positions / limits）
        current_price: 当前金价（CNY/g）
        raw_decision: PortfolioManager 输出
            direction, position_pct, composite_score, confidence(可选)
        long_only: 仅做多时禁止 short 执行方向

    Returns:
        动作字典（action / action_cn / direction / position_pct / ...）
    """
    limits = portfolio.get("limits") or {}
    total_funds = float(limits.get("total_funds") or 0)
    max_gold_raw = limits.get("max_gold_pct", 80)
    max_gold_pct = float(max_gold_raw)
    if max_gold_pct > 1.0:
        max_gold_pct = max_gold_pct / 100.0

    grams, avg_cost, hard_stop, secondary_stop = _extract_positions(portfolio)
    has_position = grams > 0
    current_gold_value = grams * float(current_price) if current_price > 0 else 0.0
    current_gold_pct = (current_gold_value / total_funds) if total_funds > 0 else 0.0
    unrealized_pnl_pct = (
        (float(current_price) - avg_cost) / avg_cost if avg_cost > 0 else 0.0
    )

    direction_raw = str(raw_decision.get("direction") or "neutral")
    composite_score = float(raw_decision.get("composite_score") or 0.0)
    confidence = raw_decision.get("confidence")
    if confidence is None:
        # PM 输出常含 bull/bear confidence；取较高者作边缘置信代理
        confidence = max(
            float(raw_decision.get("bull_confidence") or 0.0),
            float(raw_decision.get("bear_confidence") or 0.0),
        )
    confidence = float(confidence or 0.0)
    position_pct_raw = float(raw_decision.get("position_pct") or 0.0)

    near_hard_stop = hard_stop is not None and current_price > 0 and current_price <= hard_stop
    near_secondary_stop = (
        secondary_stop is not None and current_price > 0 and current_price <= secondary_stop
    )

    # long_only: short / bearish_bias → 有仓减仓意图，无仓观望；执行方向永不 short
    bearish_intent = (
        direction_raw == "short"
        or composite_score <= -0.3
        or bool(raw_decision.get("bearish_bias"))
    )
    if long_only and direction_raw == "short":
        exec_direction = "neutral"
    elif direction_raw == "short" and not long_only:
        exec_direction = "short"
    elif direction_raw == "long":
        exec_direction = "long"
    else:
        exec_direction = "neutral"

    def _result(
        action: str,
        *,
        direction: str,
        position_pct: float,
        target_gold_pct: float,
        reason: str,
        signal_type: str | None = None,
    ) -> dict[str, Any]:
        actionable = action in ("add", "reduce", "stop") and position_pct > 0
        st = signal_type or _signal_type(position_pct, abs(composite_score), actionable)
        return {
            "action": action,
            "action_cn": _ACTION_CN[action],
            "direction": direction if direction != "short" or not long_only else "neutral",
            "position_pct": round(float(position_pct), 4),
            "target_gold_pct": round(float(target_gold_pct), 4),
            "current_gold_pct": round(current_gold_pct, 4),
            "unrealized_pnl_pct": round(unrealized_pnl_pct, 4),
            "grams": round(grams, 4),
            "avg_cost": round(avg_cost, 4),
            "near_hard_stop": near_hard_stop,
            "near_secondary_stop": near_secondary_stop,
            "reason": reason,
            "signal_type": st,
        }

    # 1) 硬止损 / 次级止损
    if has_position and near_hard_stop:
        return _result(
            "stop",
            direction="neutral",
            position_pct=1.0,
            target_gold_pct=0.0,
            reason=f"价格 {current_price:.2f} ≤ 硬止损 {hard_stop:.2f}，止损离场",
            signal_type="强信号",
        )
    if has_position and near_secondary_stop:
        reduce_frac = 0.5 if not near_hard_stop else 1.0
        target = max(0.0, current_gold_pct * (1.0 - reduce_frac))
        return _result(
            "reduce" if reduce_frac < 1.0 else "stop",
            direction="neutral",
            position_pct=reduce_frac,
            target_gold_pct=target,
            reason=f"价格 {current_price:.2f} ≤ 次级止损 {secondary_stop:.2f}，减仓/止损",
            signal_type="强信号",
        )

    # 2) 弱边缘：无新仓 / 有仓持有
    weak_edge = (
        abs(composite_score) < 0.3
        or confidence < 0.4
        or position_pct_raw < 0.05
    )
    # 明确空头意图时不走弱边缘 hold 短路（有仓应减仓）
    if weak_edge and not bearish_intent:
        if has_position:
            return _result(
                "hold",
                direction="long" if current_gold_pct > 0 else "neutral",
                position_pct=0.0,
                target_gold_pct=current_gold_pct,
                reason=(
                    f"弱信号(score={composite_score:+.2f}, conf={confidence:.0%}, "
                    f"pos={position_pct_raw:.0%})，已有持仓→持有"
                ),
            )
        return _result(
            "stand_aside",
            direction="neutral",
            position_pct=0.0,
            target_gold_pct=0.0,
            reason=(
                f"弱信号(score={composite_score:+.2f}, conf={confidence:.0%}, "
                f"pos={position_pct_raw:.0%})，无持仓→观望"
            ),
        )

    # 3) 偏空：有仓减仓，无仓观望
    if bearish_intent:
        if has_position:
            # 减仓比例：用 raw position 或 |score| 映射，默认 0.3
            reduce_frac = min(max(position_pct_raw, abs(composite_score), 0.2), 1.0)
            target = max(0.0, current_gold_pct * (1.0 - reduce_frac))
            return _result(
                "reduce",
                direction="neutral" if long_only else "short",
                position_pct=reduce_frac,
                target_gold_pct=target,
                reason=(
                    f"偏空(score={composite_score:+.2f}, raw_dir={direction_raw})，"
                    f"建议减仓 {reduce_frac:.0%}"
                ),
            )
        return _result(
            "stand_aside",
            direction="neutral",
            position_pct=0.0,
            target_gold_pct=0.0,
            reason=f"偏空但无持仓，long_only 不新开空→观望",
        )

    # 4) 偏多
    long_edge = composite_score >= 0.3 or exec_direction == "long"
    if long_edge and not weak_edge:
        underwater_badly = unrealized_pnl_pct <= -0.15
        room = max_gold_pct - current_gold_pct

        if not has_position:
            add_pct = min(max(position_pct_raw, 0.0), max_gold_pct, 0.2)
            return _result(
                "add",
                direction="long",
                position_pct=add_pct,
                target_gold_pct=add_pct,
                reason=f"偏多且无持仓(score={composite_score:+.2f})，建议建仓 {add_pct:.0%}",
            )

        # 已有持仓：深度浮亏或空间不足 → 持有；否则可小幅加仓
        if underwater_badly:
            return _result(
                "hold",
                direction="long",
                position_pct=0.0,
                target_gold_pct=current_gold_pct,
                reason=f"偏多但浮亏 {unrealized_pnl_pct:.1%} 较深，不加仓、持有观望反弹",
            )

        if room > 0.02 and composite_score >= 0.3 and position_pct_raw >= 0.05:
            add_pct = min(position_pct_raw, room, 0.1)
            return _result(
                "add",
                direction="long",
                position_pct=add_pct,
                target_gold_pct=min(current_gold_pct + add_pct, max_gold_pct),
                reason=(
                    f"偏多且有加仓空间(room={room:.0%}, score={composite_score:+.2f})，"
                    f"建议加仓 {add_pct:.0%}"
                ),
            )

        return _result(
            "hold",
            direction="long",
            position_pct=0.0,
            target_gold_pct=current_gold_pct,
            reason=f"偏多已有持仓(score={composite_score:+.2f})，维持持有不追高",
        )

    # 5) 默认
    if has_position:
        return _result(
            "hold",
            direction="long" if current_gold_pct > 0 else "neutral",
            position_pct=0.0,
            target_gold_pct=current_gold_pct,
            reason="信号不明确，维持持有",
        )
    return _result(
        "stand_aside",
        direction="neutral",
        position_pct=0.0,
        target_gold_pct=0.0,
        reason="信号不明确且无持仓，观望",
    )
