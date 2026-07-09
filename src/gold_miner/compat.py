"""Python version compatibility shims.

Provides backports for stdlib features added in Python 3.10+ so the codebase
runs on Python 3.9 (macOS default).
"""
from __future__ import annotations

import sys

__all__ = ["StrEnum"]

if sys.version_info >= (3, 11):
    from enum import StrEnum  # pragma: no cover
else:
    from enum import Enum

    class StrEnum(str, Enum):  # noqa: D204  # pragma: no cover
        """Backport of enum.StrEnum (new in 3.11).

        Members are str subclasses — they can be used anywhere a str is expected.
        """

        __str__ = str.__str__

        @staticmethod
        def _generate_next_value_(  # noqa: D205
            name: str, start: int, count: int, last_values: list[str],
        ) -> str:
            """Auto-value: return lowercased name (same as stdlib StrEnum)."""
            return name.lower()
