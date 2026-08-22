"""多Agent辩论系统 — 多头 vs 空头 vs 风控."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gold_miner.signals.base import Signal, SignalBundle
from gold_miner.strategy.kelly import kelly_position

# 聪明钱资金流信号 source 白名单（与 dimensions.py  _SMART_MONEY_SOURCES 一致）
_SMART_MONEY_SOURCES: frozenset[str] = frozenset({
    "cot_report",
    "gld_holdings_tonnes",
    "gold_etf_price_proxy",
    "gold_etf_volume_proxy",
    "intl_gold_etf_volume_proxy",
    "domestic_intl_divergence",
    "btc_etf",
    "cross_etf",
    "bank_targets",
    "comex_large_traders",
    "13f_institutional",
    "smart_money_composite",
})


def _is_smart_money(sig: Signal) -> bool:
    return sig.metadata.get("source", "") in _SMART_MONEY_SOURCES


def _extract_smart_money_args(
    signals: list[Signal],
    direction: str = "bullish",
    max_args: int = 2,
) -> list[str]:
    """从信号列表中提取聪明钱相关的论据，保证资金流维度在辩论中不被淹没。"""
    filtered = [
        s for s in signals
        if _is_smart_money(s)
        and (s.score > 0 if direction == "bullish" else s.score < 0)
    ]
    filtered.sort(key=lambda s: abs(s.score), reverse=True)
    args: list[str] = []
    for s in filtered[:max_args]:
        args.append(f"[👔{s.name}] {s.description} (评分: {s.score:+.2f})")
    return args


@dataclass
class AgentOpinion:
    agent_name: str
    stance: str
    confidence: float
    arguments: list[str] = field(default_factory=list)
    smart_money_arguments: list[str] = field(default_factory=list)
    suggested_position_pct: float = 0.0


class BullAgent:
    NAME = "多头分析师"

    def analyze(self, bundle: SignalBundle) -> AgentOpinion:
        bullish_signals = [s for s in bundle.signals if s.score > 0]
        bearish_signals = [s for s in bundle.signals if s.score < 0]

        # 聪明钱论据 — 保证资金流维度不淹没在新闻/情绪信号中
        smart_money_args = _extract_smart_money_args(bullish_signals, "bullish", max_args=2)

        # 常规论据: 按 |score| 取前 N 个，但排除聪明钱（避免重复）
        non_smart = [s for s in bullish_signals if not _is_smart_money(s)]
        non_smart.sort(key=lambda x: abs(x.score), reverse=True)
        # 至少保留 1 个聪明钱论据位置
        reserved = min(1, len(smart_money_args))
        top_n = max(0, 3 - reserved)
        arguments: list[str] = smart_money_args[:reserved].copy()
        for s in non_smart[:top_n]:
            arguments.append(f"[{s.name}] {s.description} (评分: {s.score:+.2f})")

        bull_score = sum(s.score for s in bullish_signals)
        bear_score = sum(abs(s.score) for s in bearish_signals)
        total = bull_score + bear_score

        confidence = bull_score / total if total > 0 else 0.5
        stance = "bullish" if confidence > 0.55 else "neutral"
        suggested = min(confidence * 0.8, 0.8)

        return AgentOpinion(
            agent_name=self.NAME,
            stance=stance,
            confidence=confidence,
            arguments=arguments,
            smart_money_arguments=smart_money_args,
            suggested_position_pct=suggested,
        )


class BearAgent:
    NAME = "空头分析师"

    def analyze(self, bundle: SignalBundle) -> AgentOpinion:
        bearish_signals = [s for s in bundle.signals if s.score < 0]
        bullish_signals = [s for s in bundle.signals if s.score > 0]

        # 聪明钱论据 — 保证资金流维度不淹没在新闻/情绪信号中
        smart_money_args = _extract_smart_money_args(bearish_signals, "bearish", max_args=2)

        # 常规论据: 按 |score| 取前 N 个，但排除聪明钱（避免重复）
        non_smart = [s for s in bearish_signals if not _is_smart_money(s)]
        non_smart.sort(key=lambda x: abs(x.score), reverse=True)
        reserved = min(1, len(smart_money_args))
        top_n = max(0, 3 - reserved)
        arguments: list[str] = smart_money_args[:reserved].copy()
        for s in non_smart[:top_n]:
            arguments.append(f"[{s.name}] {s.description} (评分: {s.score:+.2f})")

        bear_score = sum(abs(s.score) for s in bearish_signals)
        bull_score = sum(s.score for s in bullish_signals)
        total = bear_score + bull_score

        confidence = bear_score / total if total > 0 else 0.5
        stance = "bearish" if confidence > 0.55 else "neutral"
        suggested = min(confidence * 0.8, 0.8)

        return AgentOpinion(
            agent_name=self.NAME,
            stance=stance,
            confidence=confidence,
            arguments=arguments,
            smart_money_arguments=smart_money_args,
            suggested_position_pct=suggested,
        )


def _signal_type_from_final(position_pct: float, abs_score: float) -> str:
    """按最终可执行仓位 + 综合评分标注信号（Kelly/阈值过滤之后）.

    Kelly 硬上限约 20%，单靠 position_pct 几乎到不了「强信号」，
    故与 |composite_score| 联合判定可执行强度。
    """
    if position_pct <= 0 or abs_score < 0.3:
        return "无信号"
    if position_pct > 0.5 or abs_score >= 0.7:
        return "强信号"
    if position_pct > 0.2 or abs_score >= 0.5:
        return "中等信号"
    return "弱信号"


class PortfolioManager:
    NAME = "投资经理"

    # 与 ScoringEngine.recommend 一致：|score|<0.3 不作为方向性交易
    SCORE_THRESHOLD = 0.3

    def decide(
        self,
        bull: AgentOpinion,
        bear: AgentOpinion,
        bundle: SignalBundle,
        risk_profile: str = "moderate",
        strategy_decision: Any | None = None,
        long_only: bool = True,
    ) -> dict[str, Any]:
        risk_multipliers = {"aggressive": 1.2, "moderate": 1.0, "conservative": 0.6}
        multiplier = risk_multipliers.get(risk_profile, 1.0)
        score = bundle.composite_score

        if bull.stance == "bullish" and bear.stance != "bearish":
            direction = "long"
            raw_position = bull.suggested_position_pct
        elif bear.stance == "bearish" and bull.stance != "bullish":
            # 内部可记 bearish_bias；long_only 时执行方向不为 short
            direction = "short"
            raw_position = bear.suggested_position_pct
        else:
            net_score = score
            if net_score > 0.2:
                direction = "long"
                raw_position = abs(net_score)
            elif net_score < -0.2:
                direction = "short"
                raw_position = abs(net_score)
            else:
                direction = "neutral"
                raw_position = 0.0

        bearish_bias = direction == "short"
        position_pct = min(raw_position * multiplier, 0.9)

        # Kelly 仓位参考（做多边缘才给出正仓；偏空时 suggested 常为 0）
        kelly = kelly_position(
            composite_score=score,
            confidence=bundle.confidence,
        )
        original_pos = position_pct
        if direction == "long":
            position_pct = (
                min(position_pct, kelly.suggested_pct)
                if kelly.is_actionable()
                else min(position_pct, 0.05)
            )
        elif direction == "short":
            # 空头意图仅保留减仓参考强度；Kelly 不做空
            position_pct = min(position_pct, 0.5)
        else:
            position_pct = 0.0

        # 弱综合分：不给方向性开仓；微弱负分≠减仓信号（须 |score|≥阈值）
        weak_score = abs(score) < self.SCORE_THRESHOLD
        if weak_score:
            direction = "neutral"
            position_pct = 0.0
            # 仅显著偏空才保留减仓意图，避免 -0.04 噪声触发 reduce
            bearish_bias = score <= -self.SCORE_THRESHOLD

        # long_only：永不返回 short；显著偏空由 bearish_bias 交给 position_state 决定是否减仓
        if long_only and direction == "short":
            direction = "neutral"
            position_pct = 0.0
            if not weak_score:
                bearish_bias = True

        result = {
            "direction": direction,
            "position_pct": round(position_pct, 2),
            "signal_type": _signal_type_from_final(position_pct, abs(score)),
            "risk_profile": risk_profile,
            "bull_confidence": round(bull.confidence, 2),
            "bear_confidence": round(bear.confidence, 2),
            "composite_score": round(score, 2),
            "long_only": long_only,
            "bearish_bias": bearish_bias,
            "kelly": {
                "raw": kelly.raw_kelly,
                "quarter": kelly.quarter_kelly,
                "suggested": kelly.suggested_pct,
                "edge": kelly.edge,
                "rationale": kelly.rationale,
            },
            "debate_summary": {
                "bull_args": bull.arguments,
                "bear_args": bear.arguments,
            },
        }
        if (
            direction == "long"
            and kelly.is_actionable()
            and kelly.suggested_pct < original_pos
        ):
            result["kelly_override"] = (
                f"Kelly 压降: {original_pos:.0%} → {kelly.suggested_pct:.0%}"
            )

        # 策略目标覆盖（弱分时仅允许 long/neutral 且需 position>0 才覆盖）
        if strategy_decision is not None and strategy_decision.position_pct > 0:
            strat_dir = strategy_decision.direction
            if long_only and strat_dir == "short":
                strat_dir = "neutral"
            if weak_score and abs(score) < self.SCORE_THRESHOLD:
                # 弱分：策略不得强行 long/short 开仓；仅记录策略元数据
                result["strategy_objective"] = strategy_decision.objective.value
                result["strategy_reason"] = (
                    f"[弱分未覆盖] {strategy_decision.reason}"
                )
                result["stop_loss"] = strategy_decision.stop_loss
                result["take_profit_levels"] = strategy_decision.take_profit_levels
                result["tp_weights"] = strategy_decision.tp_weights
            else:
                result["direction"] = strat_dir
                result["position_pct"] = round(
                    min(strategy_decision.position_pct, max(position_pct, 0.01) * 1.1),
                    2,
                )
                if long_only and result["direction"] == "short":
                    result["direction"] = "neutral"
                result["strategy_objective"] = strategy_decision.objective.value
                result["strategy_reason"] = strategy_decision.reason
                result["stop_loss"] = strategy_decision.stop_loss
                result["take_profit_levels"] = strategy_decision.take_profit_levels
                result["tp_weights"] = strategy_decision.tp_weights

        # 最终再保 long_only + 阈值，并按最终仓位重算 signal_type
        if long_only and result["direction"] == "short":
            result["direction"] = "neutral"
        final_score = abs(float(result["composite_score"]))
        if final_score < self.SCORE_THRESHOLD and result.get("strategy_objective") is None or (
            final_score < self.SCORE_THRESHOLD
            and result.get("strategy_reason", "").startswith("[弱分未覆盖]")
        ):
            result["direction"] = "neutral"
            result["position_pct"] = 0.0

        result["position_pct"] = round(float(result["position_pct"]), 2)
        result["signal_type"] = _signal_type_from_final(
            float(result["position_pct"]),
            final_score,
        )

        # --- 程序化决策理由 (无需 LLM) ---
        result["rationale"] = _build_decision_rationale(result, bull, bear, bundle)
        return result


def _build_decision_rationale(
    result: dict[str, Any],
    bull: AgentOpinion,
    bear: AgentOpinion,
    bundle: SignalBundle,
) -> str:
    """基于决策结果和信号上下文，生成程序化中文决策理由.

    所有逻辑来自信号评分数学 + 维度方向统计 + 置信度阈值判断，
    完全不依赖 LLM。产出可直接嵌入报告。
    """
    score = float(result.get("composite_score", 0))
    direction = result.get("direction", "neutral")
    position_pct = float(result.get("position_pct", 0))
    confidence = bundle.confidence

    # 维度统计 (看多, 看空, 分歧, 数据不足)
    bull_dims, bear_dims, disp_dims, insuf = bundle.dimension_direction_counts()
    active = bull_dims + bear_dims

    # 方向描述
    dir_cn = {"long": "做多", "short": "做空", "neutral": "观望"}

    # 评分级别
    if abs(score) >= 0.5:
        strength_desc = "强信号" if score > 0 else "强偏空"
    elif abs(score) >= 0.3:
        strength_desc = "中等信号" if score > 0 else "中等偏空"
    else:
        strength_desc = "弱信号"

    parts: list[str] = []

    # 1. 核心判断
    if direction == "long":
        parts.append(
            f"综合评分{score:+.2f}({strength_desc}), 置信度{confidence:.0%}, "
            f"建议{dir_cn[direction]}, 建议仓位{position_pct:.0%}"
        )
    elif direction == "neutral":
        parts.append(
            f"综合评分{score:+.2f}({strength_desc}), 置信度{confidence:.0%}, "
            f"信号不足以支撑方向性操作, 建议观望"
        )
    elif direction == "short":
        parts.append(
            f"综合评分{score:+.2f}(负偏), 置信度{confidence:.0%}, "
            f"风险管理偏空"
        )

    # 2. 维度多空对比
    disp_note = f"({disp_dims}维分歧)" if disp_dims > 0 else ""
    if active > 0:
        if bull_dims > bear_dims:
            parts.append(
                f"有效维度{bull_dims}维看多 vs {bear_dims}维看空"
                + disp_note
                + (f"({insuf}维数据不足)" if insuf > 0 else "")
            )
        elif bear_dims > bull_dims:
            parts.append(
                f"有效维度{bear_dims}维看空 vs {bull_dims}维看多"
                + disp_note
                + (f"({insuf}维数据不足)" if insuf > 0 else "")
            )
        elif bull_dims == bear_dims:
            parts.append(
                f"维度方向平手({bull_dims}多 vs {bear_dims}空)" + disp_note
            )
    elif disp_dims > 0:
        parts.append(f"无有效方向维度，{disp_dims}维分歧 → 观望")
    elif insuf > 0:
        parts.append(f"全部{insuf}维数据不足，无法判断方向")

    # 3. Agent 一致性
    if bull.stance == "bullish" and bear.stance != "bearish":
        parts.append("多头明确看多, 空头未反驳 → 方向明确")
    elif bear.stance == "bearish" and bull.stance != "bullish":
        parts.append("空头明确看空, 多头未反驳 → 偏空明确")
    elif bull.stance == "bullish" and bear.stance == "bearish":
        parts.append("多空分歧大 → 不确定性高, 不追单边")
    else:
        parts.append("多空均为中性 → 无明确方向信号")

    # 4. 仓位管理提示
    if position_pct <= 0:
        parts.append("当前无开仓建议 → 持有现有仓位不变或等待信号")
    elif position_pct <= 0.05:
        parts.append(f"建议仓位仅{position_pct:.0%} → 观察仓, 等信号确认后再加")
    elif position_pct >= 0.3:
        parts.append(f"建议仓位{position_pct:.0%} ≥ 30% → 注意单次下注上限")

    # 5. 弱信号特别说明
    if abs(score) < 0.3:
        parts.append(
            f"|score|={abs(score):.2f} < 阈值0.3 → 已有持仓维持, "
            f"不建议新开或加仓"
        )

    # 6. 置信度不足警告
    if confidence < 0.5:
        parts.append(
            f"置信度{confidence:.0%} < 50% → 信号可信度低, 不建议据此操作"
        )

    return "; ".join(parts)
