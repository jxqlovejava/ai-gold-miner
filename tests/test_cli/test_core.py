"""CLI core tests."""

from __future__ import annotations

import pytest

from gold_miner.cli.core import main, setup_logging


def test_setup_logging_runs():
    """日志配置不应抛异常."""
    setup_logging()


def test_main_no_command_exits(monkeypatch, capsys):
    """无命令时应打印帮助并退出."""
    monkeypatch.setattr("sys.argv", ["gold-miner"])
    with pytest.raises(SystemExit):
        main()
    captured = capsys.readouterr()
    assert "usage:" in (captured.out + captured.err)
    assert "scan" in (captured.out + captured.err)


def _capture_scan_args(monkeypatch):
    """捕获 main() 传给 run_scan 的 kwargs."""
    import gold_miner.cli.core as core

    captured: dict = {}

    def fake_run_scan(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(core, "run_scan", fake_run_scan)
    return captured


def test_main_scan_passes_news_sentiment(monkeypatch):
    """--news/--sentiment/--report-file 应传递给 run_scan."""
    captured = _capture_scan_args(monkeypatch)
    monkeypatch.setattr(
        "sys.argv",
        ["gold-miner", "scan", "--days", "30", "--news", "--sentiment",
         "--report-file", "/tmp/x.log"],
    )
    main()
    assert captured["with_news"] is True
    assert captured["with_sentiment"] is True
    assert captured["report_file"] == "/tmp/x.log"
    assert captured["days"] == 30


def test_main_scan_defaults_news_off(monkeypatch):
    """未显式传 --news/--sentiment 时默认关闭（需 API key 的功能不开）. """
    captured = _capture_scan_args(monkeypatch)
    monkeypatch.setattr("sys.argv", ["gold-miner", "scan"])
    main()
    assert captured["with_news"] is False
    assert captured["with_sentiment"] is False
