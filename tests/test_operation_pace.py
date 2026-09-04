"""operation_pace 操作节奏测试 — 近窗口聚合 + 同向密集冷却 (2026-09-04)."""
from __future__ import annotations

from datetime import date

from gold_miner.strategy.operation_pace import (
    BUY_COOLDOWN_N,
    OperationRecord,
    analyze_pace,
    load_operations,
)

TODAY = date(2026, 9, 4)


def _op(d: str, action: str, grams: float = 1.0) -> OperationRecord:
    return OperationRecord(date=date.fromisoformat(d), action=action, grams=grams)


def test_empty_records():
    """无操作 → 空状态, 不冷却."""
    state = analyze_pace([], today=TODAY)
    assert state.n_buy == 0 and state.n_sell == 0
    assert not state.buy_cooldown and not state.sell_cooldown
    assert "无操作" in state.summary()


def test_window_filters_out_old_ops():
    """超窗口(>10自然日)操作不计入."""
    ops = [_op("2026-08-20", "buy"), _op("2026-08-26", "sell")]  # 8/20 距 9/4 = 15 天
    state = analyze_pace(ops, today=TODAY)
    assert state.n_sell == 1
    assert state.n_buy == 0
    assert state.last_action == "sell"


def test_dense_buys_trigger_cooldown():
    """近窗口密集连买(≥3 笔且间隔≤2日) → buy_cooldown."""
    ops = [
        _op("2026-08-26", "buy"),
        _op("2026-08-28", "buy"),
        _op("2026-08-29", "buy"),
    ]
    state = analyze_pace(ops, today=TODAY)
    assert state.n_buy == 3
    assert state.buy_dense_run == 3
    assert state.buy_cooldown is True
    assert "密集连买" in state.buy_cooldown_reason
    assert "冷却" in state.summary()


def test_sparse_buys_no_cooldown():
    """稀疏分批(间隔>2日, 合规 r028) → 不触发冷却."""
    ops = [
        _op("2026-08-25", "buy"),
        _op("2026-08-29", "buy"),
        _op("2026-09-02", "buy"),
    ]
    state = analyze_pace(ops, today=TODAY)
    assert state.n_buy == 3
    assert state.buy_dense_run == 1  # 各自孤立 (间隔 4/3 日 > 2)
    assert state.buy_cooldown is False


def test_dense_below_threshold_no_cooldown():
    """密集但笔数 < buy_cooldown_n → 不冷却."""
    ops = [_op("2026-09-02", "buy"), _op("2026-09-03", "buy")]
    state = analyze_pace(ops, today=TODAY)
    assert state.buy_dense_run == 2
    assert state.buy_cooldown is False  # 2 < 3


def test_two_sells_disclose_cooldown():
    """近窗口卖出 ≥2 → sell_cooldown 披露 (与 r036 互补)."""
    ops = [_op("2026-08-31", "sell"), _op("2026-09-03", "sell")]
    state = analyze_pace(ops, today=TODAY)
    assert state.n_sell == 2
    assert state.sell_cooldown is True
    assert "卖出" in state.sell_cooldown_reason


def test_net_grams_and_last_action():
    """净克数与最近操作方向."""
    ops = [
        _op("2026-08-28", "buy", grams=10.0),
        _op("2026-08-29", "buy", grams=10.0),
        _op("2026-08-31", "sell", grams=27.6),
    ]
    state = analyze_pace(ops, today=TODAY)
    assert round(state.net_grams, 2) == -7.6
    assert state.last_action == "sell"
    assert state.last_days_ago == 4  # 8/31 → 9/4


def test_future_dates_excluded():
    """未来日期操作不计入窗口."""
    ops = [_op("2026-09-10", "buy")]
    state = analyze_pace(ops, today=TODAY)
    assert state.n_buy == 0
    assert state.recent == []


def test_load_operations_tolerates_bad_lines(tmp_path):
    """jsonl 加载容错: 注释/坏行跳过."""
    p = tmp_path / "ops.jsonl"
    p.write_text(
        "# comment line\n"
        '{"date":"2026-09-02","action":"buy","grams":5.0}\n'
        "not-json-line\n"
        '{"date":"2026-09-03","action":"sell"}\n',
        encoding="utf-8",
    )
    ops = load_operations(p)
    assert len(ops) == 2
    assert ops[0].action == "buy" and ops[0].grams == 5.0
    assert ops[1].action == "sell"


def test_load_operations_missing_file_returns_empty(tmp_path):
    assert load_operations(tmp_path / "nope.jsonl") == []


def test_summary_format():
    ops = [_op("2026-09-02", "buy", grams=5.0)]
    state = analyze_pace(ops, today=TODAY)
    s = state.summary()
    assert "近10日" in s and "买1/卖0" in s and "2天前买" in s


def test_custom_threshold():
    """自定义冷却阈值生效."""
    ops = [_op("2026-08-28", "buy"), _op("2026-08-29", "buy")]
    state = analyze_pace(ops, today=TODAY, buy_cooldown_n=2)
    assert state.buy_cooldown is True  # 阈值降到 2
