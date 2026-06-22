"""Tests for Workflow engine."""

import pytest

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


class TestWorkflowRegistry:
    def test_register_and_resolve(self):
        registry = WorkflowRegistry()
        wf = PreMarketWorkflow()
        registry.register(wf)

        resolved = registry.resolve("pre-market")
        assert resolved.name == "pre-market"

    def test_resolve_by_alias(self):
        registry = WorkflowRegistry()
        wf = IntraDayWorkflow()
        registry.register(wf)

        # alias match
        resolved = registry.resolve("intra-day")
        assert resolved.name == "intra-day"

        resolved = registry.resolve("盘中")
        assert resolved.name == "intra-day"

    def test_resolve_case_insensitive(self):
        registry = WorkflowRegistry()
        wf = DailyWorkflow()
        registry.register(wf)

        resolved = registry.resolve("DAILY")
        assert resolved.name == "daily"

        resolved = registry.resolve("Daily")
        assert resolved.name == "daily"

    def test_resolve_prefix_match(self):
        registry = WorkflowRegistry()
        registry.register(PreMarketWorkflow())
        registry.register(PostMarketWorkflow())

        resolved = registry.resolve("pre")
        assert resolved.name == "pre-market"

    def test_resolve_ambiguous_raises(self):
        registry = WorkflowRegistry()
        registry.register(PreMarketWorkflow())
        registry.register(PostMarketWorkflow())

        with pytest.raises(ValueError, match="有歧义"):
            registry.resolve("market")

    def test_resolve_not_found_raises(self):
        registry = WorkflowRegistry()
        registry.register(DailyWorkflow())

        with pytest.raises(ValueError, match="未找到"):
            registry.resolve("nonexistent")

    def test_list_workflows(self):
        registry = WorkflowRegistry()
        registry.register(PreMarketWorkflow())
        registry.register(IntraDayWorkflow())

        names = registry.list_workflows()
        assert "pre-market" in names
        assert "intra-day" in names
        assert len(names) == 2

    def test_get_all(self):
        registry = WorkflowRegistry()
        registry.register(WeeklyReviewWorkflow())
        registry.register(PostTradeWorkflow())

        all_wfs = registry.get_all()
        assert len(all_wfs) == 2


class TestBuiltinWorkflows:
    def test_pre_market_dry_run(self):
        wf = PreMarketWorkflow()
        ctx = WorkflowContext(dry_run=True)
        result = wf.run(ctx)
        assert result.success is True
        assert len(result.messages) > 0

    def test_intra_day_dry_run(self):
        wf = IntraDayWorkflow()
        ctx = WorkflowContext(dry_run=True)
        result = wf.run(ctx)
        assert result.success is True

    def test_post_market_dry_run(self):
        wf = PostMarketWorkflow()
        ctx = WorkflowContext(dry_run=True)
        result = wf.run(ctx)
        assert result.success is True

    def test_daily_dry_run(self):
        wf = DailyWorkflow()
        ctx = WorkflowContext(dry_run=True)
        result = wf.run(ctx)
        assert result.success is True

    def test_post_trade_dry_run(self):
        wf = PostTradeWorkflow()
        ctx = WorkflowContext(dry_run=True)
        result = wf.run(ctx)
        assert result.success is True

    def test_weekly_review_dry_run(self):
        wf = WeeklyReviewWorkflow()
        ctx = WorkflowContext(dry_run=True)
        result = wf.run(ctx)
        assert result.success is True

    def test_all_workflows_have_name_and_aliases(self):
        workflows = [
            PreMarketWorkflow(),
            IntraDayWorkflow(),
            PostMarketWorkflow(),
            DailyWorkflow(),
            PostTradeWorkflow(),
            WeeklyReviewWorkflow(),
        ]
        for wf in workflows:
            assert wf.name
            assert isinstance(wf.aliases, set)
            assert wf.description

    def test_all_workflows_have_dry_run_steps(self):
        workflows = [
            PreMarketWorkflow(),
            IntraDayWorkflow(),
            PostMarketWorkflow(),
            DailyWorkflow(),
            PostTradeWorkflow(),
            WeeklyReviewWorkflow(),
        ]
        ctx = WorkflowContext(dry_run=True)
        for wf in workflows:
            steps = wf.dry_run_steps(ctx)
            assert isinstance(steps, list)
            assert len(steps) > 0


class TestRegistryGlobal:
    def test_get_registry_returns_registry(self):
        registry = get_registry()
        assert isinstance(registry, WorkflowRegistry)

    def test_get_registry_has_builtin_workflows(self):
        registry = get_registry()
        names = registry.list_workflows()
        assert "pre-market" in names
        assert "intra-day" in names
        assert "post-market" in names
        assert "daily" in names
        assert "post-trade" in names
        assert "weekly-review" in names
        assert "long-term" in names
        assert len(names) == 7

    def test_resolve_builtin_by_alias(self):
        registry = get_registry()

        # Chinese aliases
        assert registry.resolve("盘前").name == "pre-market"
        assert registry.resolve("盘中").name == "intra-day"
        assert registry.resolve("盘后").name == "post-market"
        assert registry.resolve("日度").name == "daily"
        assert registry.resolve("交易后").name == "post-trade"
        assert registry.resolve("周度").name == "weekly-review"

        # English aliases
        assert registry.resolve("pre").name == "pre-market"
        assert registry.resolve("intraday").name == "intra-day"
        assert registry.resolve("postmarket").name == "post-market"
        assert registry.resolve("week").name == "weekly-review"


class TestWorkflowContext:
    def test_default_context(self):
        ctx = WorkflowContext()
        assert ctx.dry_run is False
        assert isinstance(ctx.args, dict)

    def test_context_with_args(self):
        ctx = WorkflowContext(args={"days": 60, "deep": True})
        assert ctx.args["days"] == 60
        assert ctx.args["deep"] is True
