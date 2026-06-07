"""通用辅助函数."""

from datetime import datetime, time


def is_us_market_hours(dt: datetime | None = None) -> bool:
    dt = dt or datetime.now()
    t = dt.time()
    return time(20, 0) <= t or t <= time(4, 0)


def is_cn_market_hours(dt: datetime | None = None) -> bool:
    dt = dt or datetime.now()
    t = dt.time()
    return time(9, 0) <= t <= time(17, 0)


def format_price_change(current: float, previous: float) -> str:
    change = current - previous
    change_pct = change / previous * 100 if previous != 0 else 0
    direction = "↑" if change > 0 else "↓" if change < 0 else "→"
    return f"{direction} {abs(change):.2f} ({abs(change_pct):.2f}%)"


def truncate_string(s: str, max_len: int = 100) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."
