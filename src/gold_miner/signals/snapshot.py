"""信号快照落盘 — 供 adaptive_gold_monitor 理由引擎读取最近一次 pipeline 维度方向."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

SNAPSHOT_PATH = Path("data/signal_snapshot.json")

_BEIJING = timezone(timedelta(hours=8))


def save_signal_snapshot(bundle, current_price: float, path: Path = SNAPSHOT_PATH) -> None:
    """把 SignalBundle 的维度方向计数落盘为 JSON.

    bundle: 任何有 dimension_direction_counts() -> (bull, bear, dispute, insufficient) 的对象.
    分歧维度（多空平手）单独落盘；多空打成平手且有分歧维度时 direction_clarity=conflicted。
    """
    bull, bear, dispute, insuf = bundle.dimension_direction_counts()
    if bull - bear >= 2:
        clarity = "bullish"
    elif bear - bull >= 2:
        clarity = "bearish"
    elif dispute > 0 and bull == bear:
        clarity = "conflicted"
    else:
        clarity = "mixed"
    payload = {
        "timestamp": datetime.now(_BEIJING).isoformat(),
        "current_price": float(current_price),
        "bull_dims": bull,
        "bear_dims": bear,
        "dispute_dims": dispute,
        "insufficient_dims": insuf,
        "direction_clarity": clarity,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
