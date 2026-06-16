"""Verify/doctor/setup command wrappers."""

from __future__ import annotations

import argparse
import sys

from gold_miner.doctor import run_doctor
from gold_miner.setup_cli import run_setup
from gold_miner.verification.cli import run_verify


def run_verify_wrapper(args: argparse.Namespace) -> None:
    """Delegate to verification CLI."""
    run_verify(args)


def run_doctor_wrapper() -> None:
    """Delegate to doctor CLI."""
    sys.exit(run_doctor())


def run_setup_wrapper(args: argparse.Namespace) -> None:
    """Delegate to setup CLI."""
    sys.exit(run_setup(non_interactive=args.non_interactive))
