# -*- coding: utf-8 -*-
"""黄金哨兵 — 轻量持仓监控 + 价格告警 + 条件单检查.

Hermes 集成: 无异动静默, 有异动推微信卡片.
"""

from __future__ import annotations

from .engine import SentinelConfig, SentinelEngine
from .models import GoldQuote, SentinelAlert, SentinelResult

__all__ = [
    "SentinelConfig",
    "SentinelEngine",
    "GoldQuote",
    "SentinelAlert",
    "SentinelResult",
]
