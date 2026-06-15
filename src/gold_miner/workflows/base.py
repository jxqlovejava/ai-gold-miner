"""Workflow 基类定义."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from gold_miner.storage import get_store


@dataclass
class WorkflowContext:
    """工作流执行上下文."""

    store: Any = field(default_factory=lambda: get_store())
    settings: Any = None
    args: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False


@dataclass
class WorkflowResult:
    """工作流执行结果."""

    success: bool = True
    messages: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


class Workflow(ABC):
    """工作流抽象基类."""

    name: str = ""
    aliases: set[str] = set()
    description: str = ""

    @abstractmethod
    def run(self, ctx: WorkflowContext) -> WorkflowResult:
        """执行工作流."""
        ...

    def dry_run_steps(self, ctx: WorkflowContext) -> list[str]:
        """返回 dry-run 步骤描述. 子类可覆盖."""
        return [f"{self.name}: {self.description}"]
