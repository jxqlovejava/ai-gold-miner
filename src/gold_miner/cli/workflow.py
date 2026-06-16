"""Workflow command handler."""

from __future__ import annotations

import argparse
import sys

from gold_miner.config import settings
from gold_miner.workflows.base import WorkflowContext
from gold_miner.workflows.registry import get_registry


def run_workflow(args: argparse.Namespace) -> None:
    """工作流命令: gold-miner workflow <name> [--list] [--dry-run]."""
    registry = get_registry()

    # --list: 列出所有工作流
    if getattr(args, 'workflow_list', False):
        print("=" * 50)
        print("可用工作流")
        print("=" * 50)
        for wf in registry.get_all():
            aliases = ", ".join(sorted(wf.aliases)) if wf.aliases else "无"
            print(f"\n  {wf.name}")
            print(f"    描述: {wf.description}")
            print(f"    别名: {aliases}")
        print()
        return

    # 需要名称
    if not args.workflow_name:
        print("错误: 请提供工作流名称 或 使用 --workflow-list 列出所有工作流")
        print("用法: gold-miner workflow <name> [--workflow-dry-run]")
        print("      gold-miner workflow --workflow-list")
        sys.exit(1)

    # 解析工作流
    try:
        workflow = registry.resolve(args.workflow_name)
    except ValueError as e:
        print(f"错误: {e}")
        sys.exit(1)

    # 构建上下文
    ctx = WorkflowContext(
        args={
            "days": args.days,
            "with_news": args.news,
            "with_sentiment": args.sentiment,
            "deep": args.deep,
            "risk_profile": args.risk or settings.risk_profile,
        },
        dry_run=getattr(args, 'workflow_dry_run', False),
    )

    # dry-run
    if ctx.dry_run:
        print(f"\n[DRY-RUN] 工作流: {workflow.name}")
        print(f"描述: {workflow.description}")
        print("\n执行步骤:")
        for step in workflow.dry_run_steps(ctx):
            print(f"  {step}")
        print("\n(dry-run 模式: 未执行实际网络调用)")
        return

    # 执行工作流
    print(f"\n执行工作流: {workflow.name}")
    print(f"{'='*50}")
    result = workflow.run(ctx)

    for msg in result.messages:
        print(f"  {msg}")

    if result.success:
        print(f"\n工作流完成: {workflow.name}")
    else:
        print(f"\n工作流失败: {workflow.name}")
        sys.exit(1)
