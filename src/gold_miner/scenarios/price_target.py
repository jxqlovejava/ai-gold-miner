"""三情景目标区间结构化生成 + 传导链完整性校验.

背景 (2026-08-14 事故):
  分析报告中「看多突破」情景只写了「停火破裂→地缘避险→金价冲 965」的单层利多传导,
  漏掉了二阶效应「油价↑→通胀↑→联储被迫鹰派→实际利率↑→压制金价」。
  根因: 三情景目标区间预测完全靠分析者手写, 无代码强制传导链完整性。

本模块:
  1. build_price_target_matrix() — 基于现价/ATR/关键位生成结构化三情景矩阵
  2. validate_scenario_transmissions() — 对每个情景校验传导链完整性,
     地缘/油价类触发条件必须同时评估「避险利多」与「油价→通胀→联储→利率压制」二阶传导,
     并标注时间尺度分化 (短期脉冲 vs 中期回落), 防止把短期脉冲当可持续目标。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 地缘/油价类触发关键词 (命中即强制二阶传导检查)
GEOPOLITICAL_KEYWORDS: tuple[str, ...] = (
    "停火", "战争", "封锁", "霍尔木兹", "曼德海峡", "红海",
    "伊朗", "美伊", "以色列", "胡塞", "地缘", "冲突",
    "ceasefire", "war", "blockade", "Hormuz", "houthi", "iran",
    "oil", "油价", "原油", "能源",
)

# 二阶传导关键词 (油价→通胀→联储→利率压制)
SECOND_ORDER_KEYWORDS: tuple[str, ...] = (
    "油价", "原油", "通胀", "联储", "美联储", "鹰派", "实际利率",
    "oil", "inflation", "fed", "hawkish", "real rate",
    "加息", "维持利率", "利率上行",
)

# 时间尺度分化关键词 (防止把短期脉冲当可持续目标)
TIMESCALE_KEYWORDS: tuple[str, ...] = (
    "短期", "中期", "脉冲", "先冲后落", "回落", "分化",
    "short-term", "medium-term", "pulse", "pullback",
)


@dataclass
class PriceTargetScenario:
    """单个目标区间情景."""

    name: str
    direction: str                 # bullish / neutral / bearish
    probability_pct: float
    gold_low: float                # 积存金 元/g
    gold_high: float
    xauusd_low: float
    xauusd_high: float
    trigger_conditions: str        # 触发条件 (自然语言)
    transmission_channels: list[str] = field(default_factory=list)  # 每条含方向+时间尺度
    falsification: str = ""        # 证伪点
    reasoning: str = ""            # 推导依据 (关键位/关口/ATR)


def _round_pad(value: float, step: float = 1.0) -> float:
    """按 step 取整并保留一位小数, 供区间边界显示."""
    if value <= 0:
        return 0.0
    return round(round(value / step) * step, 1)


def build_price_target_matrix(
    current_price: float,
    atr: float,
    base_xauusd: float,
    scenarios: list[dict[str, Any]],
) -> list[PriceTargetScenario]:
    """基于现价/ATR/关键位生成结构化三情景矩阵.

    Args:
        current_price: 积存金现价 (元/g)
        atr: 14 日 ATR (元/g)
        base_xauusd: 国际金价 (USD/oz), 用于换算对应区间
        scenarios: 情景规格列表, 每项含
            name / direction / probability_pct / trigger_conditions /
            gold_delta_pct (相对现价 ±%) / transmission_channels / falsification
            其中 gold_delta_pct 用偏移百分比定义区间边界, 避免硬编码绝对价.

    Returns:
        list[PriceTargetScenario]: 每个情景带完整字段.
    """
    if current_price <= 0:
        return []

    # XAUUSD 与积存金的比例 (用于区间换算)
    ratio = base_xauusd / current_price if current_price > 0 and base_xauusd > 0 else 0.0

    out: list[PriceTargetScenario] = []
    for spec in scenarios:
        name = spec.get("name", "情景")
        direction = spec.get("direction", "neutral")
        prob = float(spec.get("probability_pct", 0))
        delta_low = float(spec.get("gold_delta_pct_low", 0)) / 100
        delta_high = float(spec.get("gold_delta_pct_high", 0)) / 100

        # 区间边界 = 现价 × (1 + 偏移). 保证 low<=high
        low = min(current_price * (1 + delta_low), current_price * (1 + delta_high))
        high = max(current_price * (1 + delta_low), current_price * (1 + delta_high))
        low = _round_pad(low)
        high = _round_pad(high)

        channels = list(spec.get("transmission_channels", []))
        scenario = PriceTargetScenario(
            name=name,
            direction=direction,
            probability_pct=prob,
            gold_low=low,
            gold_high=high,
            xauusd_low=_round_pad(low * ratio, 5) if ratio else 0.0,
            xauusd_high=_round_pad(high * ratio, 5) if ratio else 0.0,
            trigger_conditions=str(spec.get("trigger_conditions", "")),
            transmission_channels=channels,
            falsification=str(spec.get("falsification", "")),
            reasoning=str(spec.get("reasoning", "")),
        )
        out.append(scenario)

    return out


def _hits(text: str, keywords: tuple[str, ...]) -> bool:
    """文本是否命中关键词集合 (大小写不敏感)."""
    if not text:
        return False
    lowered = text.lower()
    return any(kw.lower() in lowered for kw in keywords)


def validate_scenario_transmissions(
    scenarios: list[PriceTargetScenario],
) -> list[str]:
    """校验每个情景的传导链完整性, 返回警告列表.

    规则:
      1. 地缘/油价类触发条件 (命中 GEOPOLITICAL_KEYWORDS) 且方向为 bullish/bearish:
         - 必须有 ≥1 条利多传导 (避险/通胀对冲)
         - 必须有 ≥1 条利空传导 (油价→通胀→联储→实际利率↑, 或美元走强)
         - 必须有时间尺度标注 (防止把短期脉冲当可持续目标)
      2. 任一缺失 → 追加一条警告.
      3. 非地缘情景 / 中性情景 → 跳过 (不误报).

    Returns:
        list[str]: 警告列表, 空表示全部通过.
    """
    warnings: list[str] = []

    for s in scenarios:
        if s.direction == "neutral":
            continue

        # 只有地缘/油价驱动的情景才强制二阶传导
        if not _hits(s.trigger_conditions, GEOPOLITICAL_KEYWORDS):
            continue

        channels = " ".join(s.transmission_channels)

        has_bullish = (
            "利多" in channels
            or "避险" in channels
            or "bullish" in channels
            or "+" in channels
            or any(
                kw in s.trigger_conditions and "利多" in s.trigger_conditions
                for kw in ("停火", "缓和", "降息")
            )
        )
        has_bearish = (
            "利空" in channels
            or "压制" in channels
            or "回落" in channels
            or "bearish" in channels
            or "-" in channels
            or _hits(channels, SECOND_ORDER_KEYWORDS)
        )
        has_timescale = _hits(channels, TIMESCALE_KEYWORDS) or _hits(
            s.trigger_conditions, TIMESCALE_KEYWORDS
        )

        if not has_bullish:
            warnings.append(
                f"情景[{s.name}] 缺利多传导: 地缘/油价触发需同时说明避险或通胀对冲的利多路径"
            )
        if not has_bearish:
            warnings.append(
                f"情景[{s.name}] 缺二阶传导: 未评估油价→通胀→联储鹰派→实际利率↑→压制金价 (或美元走强)"
            )
        if not has_timescale:
            warnings.append(
                f"情景[{s.name}] 缺时间尺度分化: 需标注短期/中期方向 (如先冲后落), 防止把短期脉冲当可持续目标"
            )

    return warnings
