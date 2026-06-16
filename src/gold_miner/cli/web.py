"""Web dashboard command handler."""

from __future__ import annotations

import argparse
import sys


def run_web(args: argparse.Namespace) -> None:
    """启动 Streamlit Web 仪表盘."""
    try:
        import streamlit.web.cli as stcli  # type: ignore[import-not-found]
    except ImportError:
        print("错误：Web 仪表盘需要 streamlit。请运行：")
        print("  pip install -e \".[web]\"")
        sys.exit(1)

    from pathlib import Path

    app_path = Path(__file__).parent.parent / "web" / "app.py"
    sys.argv = ["streamlit", "run", str(app_path), "--server.port", str(args.port)]
    stcli.main()
