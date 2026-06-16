"""Scan command tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from gold_miner.cli.scan import run_scan


def test_run_scan_demo_mode_sets_flags(monkeypatch):
    """Demo 模式应关闭 news/sentiment/deep."""
    monkeypatch.setattr("gold_miner.cli.scan.settings.demo_mode", True)

    with patch("gold_miner.cli.scan.AnalysisPipeline") as mock_pipeline:
        instance = mock_pipeline.return_value
        result = MagicMock()
        result.gold_df.empty = False
        instance.run.return_value = result
        run_scan(days=5)

        ctx = instance.run.call_args[0][0]
        assert ctx.with_news is False
        assert ctx.with_sentiment is False
        assert ctx.deep is False
