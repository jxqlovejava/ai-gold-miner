"""情景预案结构化触发条件 — 关键价 + 时间窗 + 证伪点 + 动作.

借鉴博主框架：情景预案不只给方向，还给出可证伪的具体触发条件
（如「4300 上方强调整 20 小时不破非农起涨点 → 上行；跌破 → 大调整」）。

每个触发点包含:
- key_price          关键价位（触发/证伪参考位）
- time_window        时间窗（如 20 小时 / 2 周）
- trigger_condition  剧本成立条件
- falsification      证伪点 —— 什么情况下剧本不成立
- implied_action     剧本成立时应执行的动作

供两处消费:
1. 中长期分析 (pipeline/long_term.py) — 输出结构化情景触发条件 + 条件单建议
2. 条件单审查 (pipeline/analysis.py _step_plan) — 把结构化触发点转成可执行建单建议
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class ScenarioTrigger:
    """单个结构化情景触发条件."""

    name: str
    trigger_condition: str
    falsification: str
    implied_action: str
    key_price: float | None = None
    time_window: str = ""
    direction: str = ""  # up | down | neutral

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _action_for(direction: str, position_pct: float, up: bool) -> str:
    """根据当前方向/仓位给出剧本成立时的动作."""
    if up:
        if direction == "long" and position_pct > 0:
            return "维持多头/分批加仓，回踩关键位不破可加仓"
        if direction == "long":
            return "分批建仓（≤20% 单笔上限，等回踩确认）"
        if direction == "short":
            return "反弹减仓/兑现部分利润"
        return "维持观望，确认后再评估"
    # 下行证伪
    if direction == "long" and position_pct > 0:
        return "暂停加仓，跌破证伪点考虑减仓避险"
    if direction == "long":
        return "不急于抄底，等止跌信号再评估"
    if direction == "short":
        return "维持减仓，不必恐慌追空"
    return "观望，等方向明确"


def build_scenario_triggers(
    *,
    direction: str,
    position_pct: float,
    current_spot: float,
    start_point: float | None = None,
    time_window: str = "20小时",
    upside_confirmation_pct: float = 0.02,
) -> list[ScenarioTrigger]:
    """构建情景预案结构化触发条件.

    参照「关键价上方强调整 N 小时不破起涨点 → 上行」的判据结构，
    固定输出 上行确认 / 上行证伪 两个对偶触发点，避免单向叙事。

    Args:
        direction: 当前方向 long | short | neutral
        position_pct: 当前建议仓位比例
        current_spot: 现价（国际金价 USD/oz 或积存金元/克均可，保持一致即可）
        start_point: 起涨点/关键位，默认现价的 97%
        time_window: 强势调整时间窗，默认 20 小时（博主式判据）
        upside_confirmation_pct: 上行确认相对现价的幅度，默认 +2%
    """
    if current_spot <= 0:
        return []

    start = start_point or round(current_spot * 0.97, 1)
    up_price = round(current_spot * (1 + upside_confirmation_pct), 1)

    return [
        ScenarioTrigger(
            name="上行确认",
            direction="up",
            key_price=up_price,
            time_window=time_window,
            trigger_condition=(
                f"现价 {current_spot:,.0f} 上方强势整理，站稳 {up_price:,.0f} "
                f"并持续 {time_window} 不破起涨点 {start:,.0f}"
            ),
            falsification=f"跌破起涨点 {start:,.0f}，上行剧本证伪，转入大级别调整",
            implied_action=_action_for(direction, position_pct, up=True),
        ),
        ScenarioTrigger(
            name="上行证伪(下行)",
            direction="down",
            key_price=start,
            time_window="即时",
            trigger_condition=f"价格跌破起涨点 {start:,.0f}",
            falsification="跌破后未收复，确认调整级别扩大，多头剧本不成立",
            implied_action=_action_for(direction, position_pct, up=False),
        ),
    ]


def conditional_order_suggestions_from_triggers(
    triggers: list[ScenarioTrigger],
) -> list[dict[str, Any]]:
    """将情景预案触发条件转为可执行的条件单建议.

    上行确认 → 限价/突破条件单参考价；
    上行证伪 → 止损/减仓条件单参考价。
    """
    suggestions: list[dict[str, Any]] = []
    for t in triggers:
        if t.key_price is None or t.key_price <= 0:
            continue
        if t.direction == "up":
            suggestions.append({
                "type": "limit_buy",
                "direction": "buy",
                "trigger_price": t.key_price,
                "time_window": t.time_window,
                "note": f"{t.name}: {t.trigger_condition}",
            })
        elif t.direction == "down":
            suggestions.append({
                "type": "stop_loss",
                "direction": "reduce",
                "trigger_price": t.key_price,
                "time_window": t.time_window,
                "note": f"证伪点: {t.falsification}",
            })
    return suggestions
