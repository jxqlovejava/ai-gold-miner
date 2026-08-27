"""Scan command tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from gold_miner.cli.scan import _run_with_report, run_scan


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


def test_run_with_report_writes_print_output(tmp_path):
    """report_file 应捕获 print 输出到文件（Tee 落盘）. """
    report_file = str(tmp_path / "scan.log")

    def _run() -> str:
        print("测试报告行内容")
        return "ok"  # 模拟 run 成功（run() 返回 None 会被 _run_with_report 视为失败并删除 tmp）

    _run_with_report(_run, report_file)

    content = Path(report_file).read_text(encoding="utf-8")
    assert "测试报告行内容" in content


def test_run_with_report_none_runs_untouched(tmp_path):
    """report_file 为空时应原样运行且不创建文件. """
    _run_with_report(lambda: print("无落盘"), None)

    assert not list(tmp_path.glob("*.log"))
