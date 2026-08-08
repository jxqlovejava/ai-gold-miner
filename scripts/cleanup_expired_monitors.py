#!/usr/bin/env python3
"""定期清理过期的黄金哨兵 monitor.

扫描日历中 status=active 且 expires_at 已过期的 monitor 事件,
将其标记为 expired, 避免过期例行观察继续被推送.

用途: Hermes cron 定时任务 (建议每天一次)
  0 6 * * *  cd /home/ubuntu/ai-gold-miner && PYTHONPATH=src python3 scripts/cleanup_expired_monitors.py
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from loguru import logger


def main() -> int:
    """清理过期 monitor, 返回清理数量 (用于 cron 日志/微信静默)."""
    from gold_miner.data.calendar import EventCalendar

    cal = EventCalendar()
    now = datetime.now(UTC)
    active = cal.get_active_monitors()
    expired: list[str] = []

    for m in active:
        if not m.expires_at:
            continue
        try:
            exp = datetime.fromisoformat(m.expires_at)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            continue
        if now > exp:
            cal.close_monitor(
                name=m.name,
                result=f"过期自动清理: expires_at {m.expires_at[:10]} < now",
                new_status="expired",
            )
            expired.append(m.name)
            logger.info(f"已清理过期 monitor: {m.name}")

    if expired:
        print(f"✅ 清理 {len(expired)} 个过期 monitor: {'、'.join(expired)}")
    else:
        # 空 stdout = 静默 (Hermes cron 不推送)
        logger.info("无过期 monitor 需要清理")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
