"""Workflows 包入口."""

from gold_miner.workflows.base import Workflow, WorkflowContext, WorkflowResult
from gold_miner.workflows.builtin import (
    DailyWorkflow,
    IntraDayWorkflow,
    PostMarketWorkflow,
    PostTradeWorkflow,
    PreMarketWorkflow,
    WeeklyReviewWorkflow,
)
from gold_miner.workflows.registry import WorkflowRegistry, get_registry

__all__ = [
    "Workflow",
    "WorkflowContext",
    "WorkflowResult",
    "WorkflowRegistry",
    "get_registry",
    "PreMarketWorkflow",
    "IntraDayWorkflow",
    "PostMarketWorkflow",
    "DailyWorkflow",
    "PostTradeWorkflow",
    "WeeklyReviewWorkflow",
]
