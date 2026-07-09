"""中长期工作流测试."""
from __future__ import annotations


from gold_miner.workflows.base import WorkflowContext
from gold_miner.workflows.long_term import LongTermWorkflow
from gold_miner.workflows.registry import get_registry


class TestLongTermWorkflow:
    def test_workflow_registered(self):
        registry = get_registry()
        wf = registry.resolve("long-term")
        assert wf.name == "long-term"

    def test_workflow_aliases(self):
        registry = get_registry()
        for alias in {"longterm", "中长期", "长期", "lt"}:
            wf = registry.resolve(alias)
            assert wf.name == "long-term"

    def test_dry_run(self):
        wf = LongTermWorkflow()
        ctx = WorkflowContext(args={"horizon": 12}, dry_run=True)
        result = wf.run(ctx)
        assert result.success
        assert len(result.messages) == 7
        assert "读取投资者画像" in result.messages[0]

    def test_actual_run(self, monkeypatch):
        monkeypatch.setattr("gold_miner.config.settings.llm_api_key", "")
        wf = LongTermWorkflow()
        ctx = WorkflowContext(args={"horizon": 12, "risk_profile": "moderate"}, dry_run=False)
        result = wf.run(ctx)
        assert result.success
        analysis = result.data.get("long_term_analysis", {})
        assert "summary" in analysis
        assert "scenario_matrix" in analysis
        assert "munger_models" in analysis
        assert "doctrine_result" in analysis
