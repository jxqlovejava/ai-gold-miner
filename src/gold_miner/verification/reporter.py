"""Markdown 验证报告生成器."""

from datetime import datetime
from pathlib import Path
from typing import Any

from gold_miner.config import settings
from gold_miner.events.models import PredictionState
from gold_miner.events.store import EventStore


class VerificationReporter:
    """生成人类可读的 Markdown 验证报告."""

    def __init__(
        self,
        store: EventStore | None = None,
        output_dir: Path | None = None,
    ) -> None:
        self.store = store or EventStore()
        self.output_dir = output_dir or (settings.data_path / "reports")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_cycle_report(
        self,
        resolution_result: dict[str, Any] | None = None,
    ) -> Path:
        """生成本轮结算报告."""
        now = datetime.now()
        filename = now.strftime("%Y-%m-%d") + "-verification-report.md"
        path = self.output_dir / filename

        states = self.store.all_states()
        settled = [s for s in states if s.settled]
        pending = [s for s in states if s.status == "pending"]
        awaiting = [s for s in states if s.status == "price_observed"]

        lines: list[str] = []
        _h(lines, f"预测验证报告 — {now.strftime('%Y-%m-%d %H:%M')}")
        lines.append("")

        # 概览
        _h(lines, "## 概览")
        lines.append("")
        lines.append(f"| 指标 | 值 |")
        lines.append(f"|------|----|")
        lines.append(f"| 总预测数 | {len(states)} |")
        lines.append(f"| 已结算 | {len(settled)} |")
        lines.append(f"| 待结算 | {len(pending)} |")
        lines.append(f"| 待人工确认 | {len(awaiting)} |")
        if settled:
            correct = sum(1 for s in settled if s.was_correct)
            lines.append(f"| 正确 | {correct} |")
            lines.append(f"| 错误 | {len(settled) - correct} |")
            lines.append(f"| 准确率 | {correct / len(settled):.1%} |")
            avg_ret = sum(s.actual_return or 0 for s in settled) / len(settled)
            lines.append(f"| 平均收益 | {avg_ret:+.2%} |")
        lines.append("")

        # 本轮自动结算
        if resolution_result and resolution_result.get("auto_settled"):
            _h(lines, "## 本轮自动结算")
            lines.append("")
            for pid in resolution_result["auto_settled"]:
                state = self.store.get_state(pid)
                if state:
                    lines.extend(_prediction_card(state))
            lines.append("")

        # 待人工确认
        if awaiting:
            _h(lines, "## 待人工确认")
            lines.append("")
            lines.append("以下中长期预测已到期并记录价格，但需人工确认结算：")
            lines.append("")
            for s in awaiting:
                lines.append(
                    f"- `{s.prediction_id[:12]}` {s.direction} "
                    f"(创建: {s.created_at.strftime('%m-%d') if s.created_at else '?'}, "
                    f"预测窗口: {s.horizon_days}天, "
                    f"入场: {s.current_price:.2f}, "
                    f"现价: {s.observed_price:.2f})"
                )
            lines.append("")
            lines.append(f"使用 `gold-miner verify --confirm <ID>` 确认结算。")
            lines.append("")

        # 分维度准确率
        if settled:
            _h(lines, "## 分维度准确率")
            lines.append("")
            lines.append(_dimension_accuracy_table(settled))
            lines.append("")

        # 最近已结算
        recent_settled = sorted(
            settled, key=lambda s: s.settled_at or datetime.min, reverse=True
        )[:5]
        if recent_settled:
            _h(lines, "## 最近结算")
            lines.append("")
            for s in recent_settled:
                lines.extend(_prediction_card(s))

        # 报告元数据
        lines.append("---")
        lines.append(f"*自动生成于 {now.isoformat()} | gold-miner event-store*")

        content = "\n".join(lines)
        path.write_text(content, encoding="utf-8")
        return path

    def generate_prediction_detail(self, prediction_id: str) -> str:
        """生成单个预测的详细报告."""
        state = self.store.get_state(prediction_id)
        if state is None:
            return f"预测 {prediction_id} 不存在"

        lines: list[str] = []
        _h(lines, f"预测详情: {prediction_id}")
        lines.append("")
        lines.extend(_prediction_card(state))
        lines.append("")
        _h(lines, "## 证据链")
        lines.append("")
        for i, snap in enumerate(state.evidence_snapshots, 1):
            lines.append(f"### 证据快照 #{i} ({snap.source_type})")
            lines.append("")
            lines.append(f"- 时间: {snap.timestamp.strftime('%Y-%m-%d %H:%M')}")
            lines.append(f"- 现货黄金: {snap.spot_gold:.2f}")
            if snap.dxy:
                lines.append(f"- 美元指数: {snap.dxy:.2f}")
            if snap.real_rate is not None:
                lines.append(f"- 实际利率: {snap.real_rate:.2f}%")
            if snap.gold_silver_ratio:
                lines.append(f"- 金银比: {snap.gold_silver_ratio:.1f}")
            lines.append(f"- 综合评分: {snap.composite_score:+.2f} (置信度: {snap.confidence:.0%})")
            lines.append("")

            if snap.signals_summary:
                lines.append("**信号明细:**")
                lines.append("")
                for sig in snap.signals_summary:
                    icon = "↑" if sig.score > 0 else "↓" if sig.score < 0 else "→"
                    lines.append(
                        f"  - {icon} [{sig.dimension}] {sig.name}: "
                        f"{sig.score:+.2f} — {sig.description}"
                    )
                lines.append("")

            if snap.source_refs:
                lines.append("**来源引用:**")
                for ref in snap.source_refs:
                    lines.append(f"  - [{ref.ref_type}] {ref.title} ({ref.url})" if ref.url else f"  - [{ref.ref_type}] {ref.title}")
                lines.append("")

        return "\n".join(lines)


def _prediction_card(state: PredictionState) -> list[str]:
    lines: list[str] = []
    status_icon = "✓" if state.was_correct else "✗" if state.was_correct is False else "○"
    lines.append(f"### {status_icon} {state.direction.upper()} | {state.source}")
    lines.append("")
    lines.append(f"| 字段 | 值 |")
    lines.append(f"|------|----|")
    lines.append(f"| 创建时间 | {state.created_at.strftime('%Y-%m-%d %H:%M') if state.created_at else '?'} |")
    lines.append(f"| 方向 | {state.direction} |")
    lines.append(f"| 综合评分 | {state.composite_score:+.2f} |")
    lines.append(f"| 置信度 | {state.confidence:.0%} |")
    lines.append(f"| 建议仓位 | {state.position_pct:.0%} |")
    lines.append(f"| 预测窗口 | {state.horizon_days} 天 |")
    lines.append(f"| 入场价格 | {state.current_price:.2f} |")
    if state.observed_price:
        lines.append(f"| 当前价格 | {state.observed_price:.2f} |")
    if state.settled:
        ret = state.actual_return or 0
        lines.append(f"| 实际收益 | {ret:+.2%} |")
        lines.append(f"| 结算方式 | {state.settled_by} |")
    lines.append(f"| 证据快照 | {len(state.evidence_snapshots)} 份 |")
    lines.append(f"| 状态 | {state.status} |")
    lines.append("")
    return lines


def _dimension_accuracy_table(settled: list[PredictionState]) -> str:
    from collections import defaultdict

    dim_correct: dict[str, list[bool]] = defaultdict(list)
    for s in settled:
        for snap in s.evidence_snapshots:
            for dim, score in snap.dimension_scores.items():
                was_right = (
                    (score > 0 and (s.actual_return or 0) > 0)
                    or (score < 0 and (s.actual_return or 0) < 0)
                )
                dim_correct[dim].append(was_right)

    lines = ["| 维度 | 准确率 | 样本数 |", "|------|--------|--------|"]
    for dim in sorted(dim_correct):
        items = dim_correct[dim]
        acc = sum(items) / len(items) if items else 0
        lines.append(f"| {dim} | {acc:.1%} | {len(items)} |")
    return "\n".join(lines)


def _h(lines: list[str], text: str, level: int = 1) -> None:
    lines.append("#" * level + " " + text)
