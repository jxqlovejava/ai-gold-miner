"""Journal command handler."""

from __future__ import annotations

from gold_miner.execution.journal import TradeJournal


def run_journal() -> None:
    """Display trading journal stats."""
    journal = TradeJournal()
    stats = journal.stats()
    print("交易统计:")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    recent = journal.recent(5)
    if recent:
        print("\n最近交易:")
        for r in recent:
            status = "✓" if r.status == "closed" and r.pnl and r.pnl > 0 else "✗" if r.pnl and r.pnl < 0 else "○"
            print(f"  {status} {r.timestamp.strftime('%m-%d %H:%M')} {r.signal} @ {r.entry_price:.2f}")
