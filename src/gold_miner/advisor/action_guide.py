"""决策指令系统 — 瘦适配器，委托给 AnalysisPipeline.

将 AnalysisPipeline.run() 的结果映射为 AdvisorReport/ActionInstruction。
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from gold_miner.advisor.core import (
    ActionInstruction,
    ActionType,
    AdvisorReport,
    PositionSize,
)
from gold_miner.pipeline.analysis import AnalysisContext, AnalysisPipeline


def _pct_to_position_size(pct: float) -> PositionSize:
    if pct >= 0.7:
        return PositionSize.FULL
    if pct >= 0.5:
        return PositionSize.HEAVY
    if pct >= 0.3:
        return PositionSize.MODERATE
    if pct >= 0.1:
        return PositionSize.LIGHT
    return PositionSize.EMPTY


def _action_from_decision(decision: dict[str, Any], current_position: float) -> ActionType:
    """Map pipeline action to ActionType."""
    action = decision.get("action", "hold")
    if action == "add":
        return ActionType.ADD if current_position > 0 else ActionType.BUY
    if action == "reduce":
        return ActionType.REDUCE
    if action == "sell":
        return ActionType.SELL
    return ActionType.HOLD


def run_pipeline_and_report(
    current_position_pct: float = 0.0,
    avg_cost: float = 0.0,
    *,
    with_news: bool = True,
    with_sentiment: bool = True,
) -> AdvisorReport:
    """委托给 AnalysisPipeline 并映射结果为 AdvisorReport.

    这是 ActionGuide.generate() 的直接替代。
    """
    logger.info("[ActionGuide→Pipeline] 委托给 AnalysisPipeline...")

    ctx = AnalysisContext(
        days=30,
        with_news=with_news,
        with_sentiment=with_sentiment,
    )
    pipeline = AnalysisPipeline()
    result = pipeline.run(ctx)

    fd = result.final_decision
    bundle = result.bundle

    # 映射操作类型
    action = _action_from_decision(fd, current_position_pct)

    # 构建指令
    instruction = ActionInstruction(
        action=action,
        position_size=_pct_to_position_size(fd.get("position_pct", 0)),
        target_pct=round(float(fd.get("position_pct", 0)), 2),
        entry_price=(
            result.minsheng_accumulation_price or result.current_price
            if action in (ActionType.BUY, ActionType.ADD) else None
        ),
        stop_loss=round(float(fd.get("stop_loss", 0)), 2) if fd.get("stop_loss", 0) else None,
        take_profit=round(float(fd.get("take_profit", 0)), 2) if fd.get("take_profit", 0) else None,
        urgency="high" if abs(bundle.composite_score) > 0.5 else "normal",
        reason=(
            f"综合评分: {bundle.composite_score:+.2f} (置信度 {bundle.confidence:.0%}) "
            f"| 信号: {bundle.bullish_count()}多/{bundle.bearish_count()}空"
        ),
        risk_note=_build_risk_note(result),
        doctrine_refs=_build_doctrine_refs(result),
    )

    # 收集警告
    warnings: list[str] = []
    if result.doctrine_result:
        for v in getattr(result.doctrine_result, "violations", []):
            if getattr(v.rule, "severity", "") == "block" and not v.passed:
                warnings.append(f"🚫 军规拦截: {v.message}")
            elif getattr(v.rule, "severity", "") == "warn" and not v.passed:
                warnings.append(f"⚠️ 军规提醒: {v.message}")

    # 来源汇总
    dims = {s.dimension for s in bundle.signals}
    source_labels = sorted(dim.name if hasattr(dim, "name") else str(dim) for dim in dims)

    return AdvisorReport(
        report_type="action_guide",
        instruction=instruction,
        confidence=bundle.confidence,
        sources=source_labels or ["AnalysisPipeline"],
        warnings=warnings,
    )


def _build_risk_note(result: Any) -> str:
    """从分析结果构建风险提示."""
    notes: list[str] = []
    if hasattr(result, "profile_match") and result.profile_match:
        pm = result.profile_match
        if not pm.get("within_limits", True):
            notes.append("⚠️ 超出仓位限额")

    if hasattr(result, "conditional_order_review") and result.conditional_order_review:
        changes = [r for r in result.conditional_order_review if r.get("suggested_action") != "保留"]
        if changes:
            notes.append(f"📝 {len(changes)}个条件单建议调整(见报告)")

    return "; ".join(notes) if notes else "无显著风险"


def _build_doctrine_refs(result: Any) -> list[str]:
    """从分析结果提取军规引用."""
    refs: list[str] = []
    if hasattr(result, "doctrine_result") and result.doctrine_result:
        for v in getattr(result.doctrine_result, "violations", []):
            if hasattr(v.rule, "id") and not v.passed:
                refs.append(v.rule.id)
    return refs


# 保留 ActionGuide 类名向后兼容，委托给新函数
class ActionGuide:
    """行动指令生成器 — 委托给 AnalysisPipeline."""

    def generate(
        self,
        current_position_pct: float = 0.0,
        avg_cost: float = 0.0,
        strategy_preference: Any = None,
        with_news: bool = True,
        with_sentiment: bool = True,
    ) -> AdvisorReport:
        return run_pipeline_and_report(
            current_position_pct=current_position_pct,
            avg_cost=avg_cost,
            with_news=with_news,
            with_sentiment=with_sentiment,
        )
