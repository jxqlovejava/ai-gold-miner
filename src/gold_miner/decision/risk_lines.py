"""派生风控线 — 从 avg_cost（单一真相源）派生，杜绝手工维护漂移。

事故背景（2026-09-17）
----------------------
新增买入把 ``avg_cost`` 从 983.27 改成 959.49，但 ``portfolio.yaml`` 里的
``hard_stop`` / ``warn_line`` / ``secondary_stop`` 仍是旧成本的派生值，且**没有任何
代码会重算它们**——两个读取点（``storage/local.py`` 与 ``sentinel/engine.py``）
都只是把 YAML 里的数字取出来用。

后果：``position_state.resolve_position_state`` 的判定
``current_price <= secondary_stop`` 变成 ``930.50 <= 934.11``，PM 据此输出「减仓」。
而按正确成本派生的 ``secondary_stop = 911.52``，``930.50 > 911.52``，**根本不该触发**。
一个陈旧数字直接制造了一个假的减仓信号。

根因：**派生值被当成独立真相源手工维护**。本模块把派生关系内化为程序——
读取方一律经 :func:`apply_derived_risk_lines` 覆盖，不再信任 YAML 中手写的派生字段。

比率可在 ``portfolio.yaml`` 的 ``limits.risk_ratios`` 覆盖（个人敏感参数），
未配置时用下方默认值（``-30%`` 见 CLAUDE.md 公开约束与 investor_profile.md）。
"""

from __future__ import annotations

import copy
from typing import Any

#: 各风控线相对成本价的比例（默认值，可被 limits.risk_ratios 覆盖）
RISK_LINE_RATIOS: dict[str, float] = {
    "hard_stop": 0.70,       # 成本价 -30% — 硬止损，无条件
    "warn_line": 0.90,       # 成本价 -10% — 预警线
    "secondary_stop": 0.95,  # 成本价 -5%  — 次级止损
}

#: 浮点比较容差（元/克）——小于此差值视为一致，避免四舍五入误报
_DRIFT_TOLERANCE = 0.01


def resolve_ratios(portfolio: dict[str, Any] | None = None) -> dict[str, float]:
    """取风控线比例：``limits.risk_ratios`` 覆盖优先，缺项回落到默认值。

    Args:
        portfolio: portfolio.yaml 解析结果，可为 None。

    Returns:
        与 :data:`RISK_LINE_RATIOS` 同键的完整比例表。
    """
    ratios = dict(RISK_LINE_RATIOS)
    if not portfolio:
        return ratios
    override = (portfolio.get("limits") or {}).get("risk_ratios") or {}
    for key, value in override.items():
        if key in ratios and isinstance(value, (int, float)):
            ratios[key] = float(value)
    return ratios


def derive_risk_lines(
    avg_cost: float,
    ratios: dict[str, float] | None = None,
) -> dict[str, float]:
    """从成本均价派生全部风控线。

    Args:
        avg_cost: 持仓成本均价（元/克）。非正数时返回空 dict。
        ratios: 可选比例覆盖，默认用 :data:`RISK_LINE_RATIOS`。

    Returns:
        ``{"hard_stop": ..., "warn_line": ..., "secondary_stop": ...}``，
        值保留两位小数（与 YAML 书写精度一致）。
    """
    if not avg_cost or avg_cost <= 0:
        return {}
    table = ratios or RISK_LINE_RATIOS
    return {
        name: round(float(avg_cost) * ratio, 2)
        for name, ratio in table.items()
    }


def apply_derived_risk_lines(portfolio: dict[str, Any]) -> dict[str, Any]:
    """返回新 dict：每个持仓的风控线按 ``avg_cost`` 重算覆盖。

    不修改入参（不可变风格）；只重建 ``positions`` 下的嵌套结构，
    其余键按引用共享。

    Args:
        portfolio: portfolio.yaml 解析结果。

    Returns:
        覆盖了派生风控线的新 dict；入参为空或结构异常时原样返回。
    """
    if not isinstance(portfolio, dict):
        return portfolio
    positions = portfolio.get("positions")
    if not isinstance(positions, dict):
        return portfolio

    ratios = resolve_ratios(portfolio)
    new_positions: dict[str, Any] = {}
    for name, pos in positions.items():
        if not isinstance(pos, dict):
            new_positions[name] = pos
            continue
        derived = derive_risk_lines(pos.get("avg_cost") or 0.0, ratios)
        new_positions[name] = {**pos, **derived} if derived else dict(pos)

    return {**portfolio, "positions": new_positions}


def check_risk_line_drift(portfolio: dict[str, Any]) -> list[str]:
    """比对 YAML 手写值与 ``avg_cost`` 派生值，返回不一致的告警文案。

    用于在加载点记录 warning，暴露「改了成本却忘了改风控线」的漂移。

    Args:
        portfolio: portfolio.yaml 解析结果（原始、未经 apply 覆盖）。

    Returns:
        每个漂移字段一条文案；无漂移返回空列表。
    """
    if not isinstance(portfolio, dict):
        return []
    positions = portfolio.get("positions")
    if not isinstance(positions, dict):
        return []

    ratios = resolve_ratios(portfolio)
    drifts: list[str] = []
    for name, pos in positions.items():
        if not isinstance(pos, dict):
            continue
        avg_cost = pos.get("avg_cost") or 0.0
        derived = derive_risk_lines(avg_cost, ratios)
        if not derived:
            continue
        for field, expected in derived.items():
            stored = pos.get(field)
            if stored is None:
                continue
            if abs(float(stored) - expected) > _DRIFT_TOLERANCE:
                drifts.append(
                    f"{name}.{field} 手写 {float(stored):.2f} ≠ 成本{avg_cost:.2f} 派生 "
                    f"{expected:.2f}（已按派生值覆盖）"
                )
    return drifts


def snapshot_for_audit(portfolio: dict[str, Any]) -> dict[str, Any]:
    """浅拷贝一份便于审计的结构（保留手写值，供 diff 用）。"""
    return copy.deepcopy(portfolio)
