"""Python version compatibility shims.

Provides backports for stdlib features added in Python 3.10+ so the codebase
runs on Python 3.9 (macOS default).
"""
from __future__ import annotations

__all__ = ["StrEnum"]

from enum import StrEnum
