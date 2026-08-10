#!/usr/bin/env python3
"""Hermes 关键推送投递失败补发看门狗.

背景 (2026-08-10 事故): hermes cron 没有投递重试机制 — 脚本执行 ok 但微信
iLink 限流导致 "⚠ Delivery failed" 时, 关键推送 (盘前简报等) 永久丢失,
且 "Last run ok" 表面上完全看不出来。

本看门狗在关键推送批次后数分钟运行:
1. `hermes cron list` 解析受监视任务最近一次运行块
2. 若块内含 "Delivery failed" 且 Last run 在 --window-min 分钟内
   → `hermes cron run <job_id>` 补发一次
3. 每个失败的 run 时间戳只补发一次; 每任务每天最多补发 --max-retries 次
   (state 文件记录, 防补发-失败-再补发循环)

用法:
  python3 scripts/hermes_delivery_watchdog.py            # 实际执行
  python3 scripts/hermes_delivery_watchdog.py --dry-run  # 只报告不补发
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

# 受监视的关键推送任务 (job_id: 说明) — 只收"错过就有实质损失"的定时推送;
# 高频条件性任务 (突发新闻/自适应监控) 大多数时候静默, 不在此列。
WATCHED_JOBS: dict[str, str] = {
    "4b8df8cca144": "黄金哨兵-盘前简报",
    "4639f7f30012": "黄金哨兵-日历提醒",
    "dfd1f9e7cac6": "黄金分析-每日完整报告",
    "1f008e97147a": "黄金·盘前新闻扫描",
    "89897db0d91a": "黄金哨兵-周报",
    "31c7bee379fa": "黄金·晚间事件预告",
    "6d49f11412cf": "白泽·开盘前简报",
    "5ca2995ab2fb": "白泽·收盘简报",
}

STATE_PATH = Path.home() / ".hermes" / "gold" / "delivery_watchdog_state.json"
JOB_BLOCK_RE = re.compile(r"^  ([0-9a-f]{12}) \[", re.MULTILINE)
LAST_RUN_RE = re.compile(r"Last run:\s+(\S+)")


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _parse_job_blocks(cron_list_output: str) -> dict[str, str]:
    """把 hermes cron list 输出切成 {job_id: block_text}."""
    matches = list(JOB_BLOCK_RE.finditer(cron_list_output))
    blocks: dict[str, str] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(cron_list_output)
        blocks[m.group(1)] = cron_list_output[m.start():end]
    return blocks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-min", type=int, default=45, help="只补发 N 分钟内的失败运行")
    ap.add_argument("--max-retries", type=int, default=2, help="每任务每天补发上限")
    ap.add_argument("--dry-run", action="store_true", help="只报告, 不补发")
    args = ap.parse_args()

    out = subprocess.run(
        ["hermes", "cron", "list"], capture_output=True, text=True, timeout=60,
    ).stdout
    blocks = _parse_job_blocks(out)
    state = _load_state()
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    now = datetime.now(tz=UTC)

    actions: list[str] = []
    for job_id, label in WATCHED_JOBS.items():
        block = blocks.get(job_id)
        if block is None:
            actions.append(f"⚠️ {label}({job_id}): hermes 中未找到, 跳过")
            continue
        if "Delivery failed" not in block:
            continue  # 最近运行投递正常或本就静默
        m = LAST_RUN_RE.search(block)
        if not m:
            continue
        try:
            last_run = datetime.fromisoformat(m.group(1))
            age_min = (now - last_run).total_seconds() / 60
        except ValueError:
            continue
        if age_min > args.window_min:
            continue  # 太旧的失败不补 (内容已过时)

        key = f"{job_id}:{today}"
        attempts = state.get(key, {}).get("attempts", 0)
        retried_runs = state.get(key, {}).get("retried_runs", [])
        if m.group(1) in retried_runs or attempts >= args.max_retries:
            continue

        if args.dry_run:
            actions.append(f"🔍 [dry-run] {label}: {age_min:.0f}分钟前投递失败, 将补发")
            continue

        r = subprocess.run(
            ["hermes", "cron", "run", job_id], capture_output=True, text=True, timeout=120,
        )
        ok = "succeeded" in (r.stdout + r.stderr).lower()
        state.setdefault(key, {"attempts": 0, "retried_runs": []})
        state[key]["attempts"] += 1
        state[key]["retried_runs"].append(m.group(1))
        actions.append(
            f"{'✅' if ok else '❌'} {label}: {age_min:.0f}分钟前投递失败, "
            f"补发{'成功' if ok else '失败'} (今日第{state[key]['attempts']}次)"
        )

    _save_state(state)
    if actions:
        print("🐕 投递看门狗 |", datetime.now().strftime("%H:%M"))
        for a in actions:
            print(" ", a)
    # 无动作时静默 (stdout 为空 → 不打扰)
    return 0


if __name__ == "__main__":
    sys.exit(main())
