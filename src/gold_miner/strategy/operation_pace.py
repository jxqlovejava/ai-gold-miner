"""操作节奏状态 — 近 N 日操作序列 + 同向密集冷却 (2026-09-04 轻量版).

背景 (9/2 复盘延伸): r036 只防「同波二次减仓」(单个 last_reduce 状态点), 本模块补
「一段时间内的连续操作状态」的最小版 —— 从结构化成交事件 (operations.jsonl) 做窗口聚合:

  - 近 N 日 (默认 10) 买入/卖出次数、净克数、最近操作、同向连续段
  - 冷却判定: 同向「密集连击」(连续 ≥buy_n 笔 且 相邻间隔 ≤2 自然日) → 建议冷却,
    防手痒连击 (连续追低越接越深 / 连续割肉). 与 r028 分批不冲突: 合规分批每批间隔
    ≥5 交易日, 不会被 dense gap≤2 误判.

数据真相源: data/private/operations.jsonl (私有, 每笔真实买卖事件一条, 由用户在成交后
追加或经对账脚本写入). 分析/监控读取本模块做披露与低吸冷却, 不直接改决策方向.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from loguru import logger

# 默认参数
WINDOW_DAYS = 10          # 窗口: 近 N 自然日
BUY_COOLDOWN_N = 3        # 买入侧: 近窗口内密集连买 ≥3 → 低吸冷却
SELL_COOLDOWN_N = 2       # 卖出侧: 近窗口内 ≥2 笔卖 (同波) → 披露 (与 r036 互补)
DENSE_GAP_DAYS = 2        # 「密集」= 相邻同向操作间隔 ≤2 自然日


@dataclass(frozen=True)
class OperationRecord:
    """一笔真实成交操作事件."""

    date: date
    action: str            # "buy" | "sell"
    grams: float = 0.0
    price: float = 0.0
    rule: str = ""         # 触发规则/条件单号 (如 r025 / co_xxx)
    note: str = ""


def _parse_date(raw: Any) -> date | None:
    """容错解析日期 (ISO 或 datetime)."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    try:
        return datetime.fromisoformat(str(raw)).date()
    except (ValueError, TypeError):
        try:
            return datetime.strptime(str(raw), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None


def load_operations(path: str | Path) -> list[OperationRecord]:
    """读取 operations.jsonl → 有序 (日期升序) OperationRecord 列表.

    逐行容错: 坏行/字段缺失静默跳过; 文件不存在返回空表 (调用方据此禁用节奏披露).
    """
    p = Path(path)
    if not p.exists():
        return []
    records: list[OperationRecord] = []
    for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            raw = json.loads(line)
        except (ValueError, TypeError):
            logger.warning(f"operations.jsonl 第 {lineno} 行 JSON 解析失败, 跳过")
            continue
        d = _parse_date(raw.get("date") or raw.get("ts"))
        action = str(raw.get("action") or raw.get("type") or "").lower()
        if d is None or action not in ("buy", "sell"):
            logger.warning(f"operations.jsonl 第 {lineno} 行缺 date/action, 跳过")
            continue
        try:
            grams = float(raw.get("grams") or 0)
            price = float(raw.get("price") or 0)
        except (TypeError, ValueError):
            grams, price = 0.0, 0.0
        records.append(
            OperationRecord(
                date=d,
                action=action,
                grams=grams,
                price=price,
                rule=str(raw.get("rule") or ""),
                note=str(raw.get("note") or ""),
            )
        )
    records.sort(key=lambda r: r.date)
    return records


def _max_dense_run(dates: list[date], gap_days: int = DENSE_GAP_DAYS) -> int:
    """按日期升序, 相邻间隔 ≤ gap_days 视为连续 → 返回最长密集连段长度."""
    if not dates:
        return 0
    dates = sorted(set(dates))
    best = cur = 1
    for a, b in zip(dates, dates[1:]):
        if (b - a).days <= gap_days:
            cur += 1
            best = max(best, cur)
        else:
            cur = 1
    return best


@dataclass(frozen=True)
class PaceState:
    """近窗口操作节奏状态 (不可变)."""

    window_days: int = WINDOW_DAYS
    today: date = field(default_factory=date.today)
    recent: list[OperationRecord] = field(default_factory=list)   # 窗口内 (日期升序)
    n_buy: int = 0
    n_sell: int = 0
    net_grams: float = 0.0
    last_action: str | None = None      # 窗口内最近一笔方向
    last_days_ago: int | None = None    # 最近一笔距今自然日
    buy_dense_run: int = 0              # 近窗口最长密集连买段
    sell_dense_run: int = 0             # 近窗口最长密集连卖段
    buy_cooldown: bool = False          # 近窗口密集连买 ≥ BUY_COOLDOWN_N → 低吸冷却
    buy_cooldown_reason: str = ""
    sell_cooldown: bool = False         # 近窗口卖出 ≥2 笔 → 披露 (与 r036 同波护栏互补)
    sell_cooldown_reason: str = ""

    def summary(self) -> str:
        """一行文本供报告/监控披露."""
        if not self.recent:
            return "近窗口无操作记录"
        parts = [
            f"近{self.window_days}日 买{self.n_buy}/卖{self.n_sell}"
            f"(净{self.net_grams:+.1f}g)",
        ]
        last = self.last_action
        if last:
            ago = f"{self.last_days_ago}天前" if self.last_days_ago is not None else "今日"
            last_cn = "买" if last == "buy" else "卖"
            parts.append(f"最近: {ago}{last_cn}")
        if self.buy_cooldown:
            parts.append(f"⚠ 低吸冷却({self.buy_dense_run}笔密集连买)")
        elif self.buy_dense_run >= BUY_COOLDOWN_N:
            parts.append(f"密集连买段 {self.buy_dense_run} 笔")
        if self.sell_cooldown:
            parts.append(f"⚠ 近期已减 {self.n_sell} 笔 (r036 同波)")
        return " | ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_days": self.window_days,
            "today": self.today.isoformat(),
            "n_buy": self.n_buy,
            "n_sell": self.n_sell,
            "net_grams": round(self.net_grams, 2),
            "last_action": self.last_action,
            "last_days_ago": self.last_days_ago,
            "buy_dense_run": self.buy_dense_run,
            "sell_dense_run": self.sell_dense_run,
            "buy_cooldown": self.buy_cooldown,
            "buy_cooldown_reason": self.buy_cooldown_reason,
            "sell_cooldown": self.sell_cooldown,
            "sell_cooldown_reason": self.sell_cooldown_reason,
        }


def analyze_pace(
    operations: list[OperationRecord],
    *,
    today: date | None = None,
    window_days: int = WINDOW_DAYS,
    buy_cooldown_n: int = BUY_COOLDOWN_N,
    sell_cooldown_n: int = SELL_COOLDOWN_N,
    dense_gap_days: int = DENSE_GAP_DAYS,
) -> PaceState:
    """对操作事件做近窗口聚合 + 同向密集冷却判定 (纯函数, 可注入 today 便于测试).

    冷却规则:
      - buy_cooldown: 近窗口内最长密集连买段 ≥ buy_cooldown_n → 低吸冷却 (防手痒连击)
      - sell_cooldown: 近窗口卖出 ≥ sell_cooldown_n → 披露 (防连续割, 与 r036 互补)
    """
    ref = today if today is not None else date.today()
    recent = [o for o in operations if (ref - o.date).days <= window_days]
    recent = [o for o in recent if (ref - o.date).days >= 0]  # 排除未来日期
    recent.sort(key=lambda o: o.date)

    buys = [o.date for o in recent if o.action == "buy"]
    sells = [o.date for o in recent if o.action == "sell"]
    n_buy, n_sell = len(buys), len(sells)
    net_grams = sum(
        (o.grams if o.action == "buy" else -o.grams) for o in recent
    )

    buy_dense_run = _max_dense_run(buys, dense_gap_days)
    sell_dense_run = _max_dense_run(sells, dense_gap_days)

    buy_cooldown = buy_dense_run >= buy_cooldown_n
    buy_reason = (
        f"近{window_days}日密集连买 {buy_dense_run} 笔(间隔≤{dense_gap_days}日)"
        f" → 低吸冷却, 防手痒连击越接越深 (仅执行计划内条件单)" if buy_cooldown else ""
    )
    sell_cooldown = n_sell >= sell_cooldown_n
    sell_reason = (
        f"近{window_days}日已卖出 {n_sell} 笔 → 连续减仓披露, 与 r036 同波护栏互补"
        if sell_cooldown else ""
    )

    last_action: str | None = None
    last_days_ago: int | None = None
    if recent:
        last = recent[-1]
        last_action = last.action
        last_days_ago = (ref - last.date).days

    return PaceState(
        window_days=window_days,
        today=ref,
        recent=recent,
        n_buy=n_buy,
        n_sell=n_sell,
        net_grams=net_grams,
        last_action=last_action,
        last_days_ago=last_days_ago,
        buy_dense_run=buy_dense_run,
        sell_dense_run=sell_dense_run,
        buy_cooldown=buy_cooldown,
        buy_cooldown_reason=buy_reason,
        sell_cooldown=sell_cooldown,
        sell_cooldown_reason=sell_reason,
    )
