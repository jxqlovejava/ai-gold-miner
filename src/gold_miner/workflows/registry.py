"""工作流注册表 — 支持模糊别名解析."""

from __future__ import annotations

from gold_miner.workflows.base import Workflow


class WorkflowRegistry:
    """工作流注册表 — 支持精确匹配、大小写不敏感、前缀匹配."""

    def __init__(self) -> None:
        self._workflows: dict[str, Workflow] = {}
        self._alias_map: dict[str, str] = {}  # alias -> canonical name

    def register(self, workflow: Workflow) -> None:
        """注册工作流."""
        self._workflows[workflow.name] = workflow
        for alias in workflow.aliases:
            self._alias_map[alias.lower()] = workflow.name

    def resolve(self, name: str) -> Workflow:
        """解析工作流名称.

        匹配顺序:
            1. 精确匹配 (canonical name)
            2. 大小写不敏感匹配 (canonical name)
            3. 别名精确匹配
            4. 别名大小写不敏感匹配
            5. 前缀匹配 (canonical name)
            6. 别名前缀匹配

        如果存在歧义 (多个匹配), 抛出 ValueError 并列出候选.
        """
        name_lower = name.lower()

        # 1. 精确匹配 canonical name
        if name in self._workflows:
            return self._workflows[name]

        # 2. 大小写不敏感 canonical name
        for wf_name, wf in self._workflows.items():
            if wf_name.lower() == name_lower:
                return wf

        # 3. 别名精确匹配
        if name in self._alias_map:
            return self._workflows[self._alias_map[name]]

        # 4. 别名大小写不敏感匹配
        if name_lower in self._alias_map:
            return self._workflows[self._alias_map[name_lower]]

        # 5. 前缀匹配 (canonical name)
        prefix_matches = [
            wf for wf_name, wf in self._workflows.items()
            if wf_name.lower().startswith(name_lower)
        ]

        # 5.5 子串匹配 (canonical name contains query)
        substring_matches = [
            wf for wf_name, wf in self._workflows.items()
            if name_lower in wf_name.lower() and wf not in prefix_matches
        ]

        # 6. 别名前缀匹配
        alias_prefix_matches = []
        for alias, canonical in self._alias_map.items():
            if alias.startswith(name_lower):
                wf = self._workflows[canonical]
                if wf not in alias_prefix_matches and wf not in prefix_matches and wf not in substring_matches:
                    alias_prefix_matches.append(wf)

        # 6.5 别名子串匹配
        alias_substring_matches = []
        for alias, canonical in self._alias_map.items():
            if name_lower in alias and alias not in self._alias_map and wf not in alias_prefix_matches:
                wf = self._workflows[canonical]
                if wf not in alias_substring_matches and wf not in prefix_matches and wf not in substring_matches and wf not in alias_prefix_matches:
                    alias_substring_matches.append(wf)

        all_matches = prefix_matches + substring_matches + alias_prefix_matches + alias_substring_matches

        if len(all_matches) == 1:
            return all_matches[0]

        if len(all_matches) > 1:
            candidates = [f"{wf.name} ({', '.join(sorted(wf.aliases))})" for wf in all_matches]
            raise ValueError(
                f"工作流名称 '{name}' 有歧义，匹配到多个: {', '.join(candidates)}"
            )

        # 无匹配
        available = self.list_workflows()
        raise ValueError(
            f"未找到工作流 '{name}'. 可用工作流: {', '.join(available)}"
        )

    def list_workflows(self) -> list[str]:
        """列出所有工作流名称 (canonical)."""
        return sorted(self._workflows.keys())

    def get_all(self) -> list[Workflow]:
        """获取所有已注册工作流实例."""
        return list(self._workflows.values())


def _register_builtin(registry: WorkflowRegistry) -> None:
    """注册所有内置工作流."""
    from gold_miner.workflows.builtin import (
        DailyWorkflow,
        IntraDayWorkflow,
        PostMarketWorkflow,
        PostTradeWorkflow,
        PreMarketWorkflow,
        WeeklyReviewWorkflow,
    )

    registry.register(PreMarketWorkflow())
    registry.register(IntraDayWorkflow())
    registry.register(PostMarketWorkflow())
    registry.register(DailyWorkflow())
    registry.register(PostTradeWorkflow())
    registry.register(WeeklyReviewWorkflow())


# 全局注册表实例
_default_registry: WorkflowRegistry | None = None


def get_registry() -> WorkflowRegistry:
    """获取全局注册表 (懒加载)."""
    global _default_registry
    if _default_registry is None:
        _default_registry = WorkflowRegistry()
        _register_builtin(_default_registry)
    return _default_registry
