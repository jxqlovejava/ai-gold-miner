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
