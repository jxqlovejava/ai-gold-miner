"""中长期分析 CLI 命令 — gold-miner longterm."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gold_miner.config import settings
from gold_miner.workflows.base import WorkflowContext
from gold_miner.workflows.long_term import LongTermWorkflow


def run_longterm(args: argparse.Namespace) -> None:
    """执行中长期金价分析工作流."""
    workflow = LongTermWorkflow()
    ctx = WorkflowContext(
        args={
            "horizon": args.horizon,
            "risk_profile": args.risk or settings.risk_profile,
        },
        dry_run=args.dry_run,
    )

    if args.dry_run:
        print(f"\n[DRY-RUN] 工作流: {workflow.name}")
        print(f"描述: {workflow.description}")
        print("\n执行步骤:")
        for step in workflow.dry_run_steps(ctx):
            print(f"  {step}")
        print("\n(dry-run 模式: 未执行实际网络调用)")
        return

    print(f"\n执行中长期分析 ({args.horizon} 个月视角)")
    print("=" * 60)
    result = workflow.run(ctx)

    for msg in result.messages:
        print(f"  {msg}")

    if result.success and result.data.get("long_term_analysis"):
        analysis = result.data["long_term_analysis"]
        print("\n--- 战略建议 ---")
        summary = analysis.get("summary", {})
        print(f"动作: {summary.get('action', '观望')}")
        print(f"目标仓位: {summary.get('target_position_pct', 0):.0%}")
        print(f"整体置信度: {summary.get('confidence', 0):.0%}")

        print("\n--- Munger 模型 ---")
        for model in analysis.get("munger_models", []):
            print(f"  · {model}")

        print("\n--- 触发条件 ---")
        for trigger in analysis.get("trigger_conditions", []):
            print(f"  · {trigger}")

        print("\n--- 再平衡规则 ---")
        for rule in analysis.get("rebalancing_rules", []):
            print(f"  · {rule}")

        print("\n--- 情景矩阵 ---")
        matrix = analysis.get("scenario_matrix", {})
        if matrix:
            print(f"当前价格: ${matrix.get('base_price', 0):,.0f}/oz")
            print(f"预期价格: ${matrix.get('expected_price', 0):,.0f}/oz "
                  f"({matrix.get('weighted_expected_change_pct', 0):+.1f}%)")
            for s in matrix.get("scenarios", []):
                print(
                    f"  {s.get('name', '')}: 概率 {s.get('probability_pct', 0)}%, "
                    f"变动 {s.get('gold_change_pct', 0):+.1f}%, "
                    f"区间 ${s.get('gold_low', 0):,.0f} - ${s.get('gold_high', 0):,.0f}"
                )

        if args.output:
            output_path = Path(args.output)
            output_path.write_text(
                json.dumps(analysis, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"\n报告已保存: {output_path}")
    elif not result.success:
        print("\n工作流执行失败")
