#!/usr/bin/env python3
"""Hermes 关键推送投递失败补发看门狗.

背景 (2026-08-10 事故): hermes cron 没有投递重试机制 — 脚本执行 ok 但微信
iLink 限流导致 "⚠ Delivery failed" 时, 关键推送 (盘前简报等) 永久丢失,
且 "Last run ok" 表面上完全看不出来。

本看门狗在关键推送批次后数分钟运行:
1. `hermes cron list` 解析受监视任务最近一次运行块
2. 若块内含 "Delivery failed" 且 Last run 在 --window-min 分钟内
   → 2026-08-12 修复: 从 ~/.hermes/cron/output/<job_id>/ 取该次运行的原始输出
     (hermes cron 会把 stdout 存为 .md 文件), 直接 `hermes send` 补发原文 —
     不再 `hermes cron run` 重跑整个 job (agent job 重跑 >120s 必超时, 且内容变化)
   → 无输出文件时 fallback `hermes cron run` 重跑 (捕获超时不崩溃)
3. 每个失败的 run 时间戳只补发一次; 每任务每天最多补发 --max-retries 次
   (state 文件记录. 补发成功才记 retried_runs; 限流失败不记, 下轮窗口恢复后重试)

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
from datetime import UTC, datetime, timedelta
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
    "38e92d6d7a69": "黄金·L1/V9计划级提醒(晚间)",
    "2550b053178f": "黄金·L1计划盘中监控",
    "6d49f11412cf": "白泽·开盘前简报",
    "5ca2995ab2fb": "白泽·收盘简报",
}

WEIXIN_TARGET = "weixin:o9cq80613_z9qxqE69G94f-0CzGk@im.wechat"

STATE_PATH = Path.home() / ".hermes" / "gold" / "delivery_watchdog_state.json"
JOB_BLOCK_RE = re.compile(r"^  ([0-9a-f]{12}) \[", re.MULTILINE)
LAST_RUN_RE = re.compile(r"Last run:\s+(\S+)")
CRON_OUTPUT_ROOT = Path.home() / ".hermes" / "cron" / "output"
# cron output 文件名: YYYY-MM-DD_HH-MM-SS.md
_OUTPUT_NAME_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})")


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


def _find_output(job_id: str, last_run: str) -> str | None:
    """取 ~/.hermes/cron/output/<job_id>/ 下与 last_run 时间最接近的原始输出文件.

    cron 保存文件名是该 job 运行结束时间 (比 cron list 的 last_run 略早), 因此
    匹配时间差最小的文件; 差 10 分钟内视为同一次运行.
    """
    out_dir = CRON_OUTPUT_ROOT / job_id
    if not out_dir.is_dir():
        return None
    try:
        target = datetime.fromisoformat(last_run)
    except ValueError:
        return None
    best: tuple[str, float] | None = None
    for f in out_dir.glob("*.md"):
        m = _OUTPUT_NAME_RE.search(f.stem)
        if not m:
            continue
        y, mo, d, h, mi, s = (int(g) for g in m.groups())
        try:
            ts = datetime(y, mo, d, h, mi, s)
        except ValueError:
            continue
        # 文件名是服务器本地时间 (naive), last_run 带 +08:00 偏移 → 用 target 的 tzinfo 对齐
        if target.tzinfo is not None:
            ts = ts.replace(tzinfo=target.tzinfo)
        delta = abs((ts - target).total_seconds())
        if best is None or delta < best[1]:
            best = (str(f), delta)
    if best and best[1] < 600:  # 与 last_run 差 10 分钟内
        return best[0]
    return None


def _resend(job_id: str, last_run: str) -> tuple[bool, str]:
    """补发投递失败的消息原文. 返回 (成功, 方式说明).

    优先: 读 cron output 原文 → `hermes send` (内容与原推送一致, 秒级).
    fallback: 无输出文件时 `hermes cron run` 重跑 job (捕获超时不崩溃).
    """
    out_file = _find_output(job_id, last_run)
    if out_file:
        try:
            content = Path(out_file).read_text(encoding="utf-8")
        except OSError:
            content = ""
        if content.strip():
            r = subprocess.run(
                ["hermes", "send", "-t", WEIXIN_TARGET, "-q", content],
                capture_output=True, text=True, timeout=60,
            )
            return r.returncode == 0, f"原文补发({Path(out_file).name})"
    try:
        r = subprocess.run(
            ["hermes", "cron", "run", job_id], capture_output=True, text=True, timeout=120,
        )
        ok = "succeeded" in (r.stdout + r.stderr).lower()
        return ok, "重跑job补发"
    except subprocess.TimeoutExpired:
        return False, "重跑job超时"


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

    # ── 熔断器 (2026-08-13) ──
    # 背景: iLink 微信持续限流时, 看门狗补发只会雪上加霜 (每条补发再产生 N 次重试).
    # 方案: 上一次补发失败 → 熔断 2h 暂停全部补发, 让 iLink 通道喘息恢复.
    cooldown_until = state.get("cooldown_until")
    if cooldown_until:
        try:
            if now < datetime.fromisoformat(cooldown_until):
                return 0  # 熔断期内: 静默跳过, 不补发不输出
        except (ValueError, TypeError):
            pass

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

        r_ok, how = _resend(job_id, m.group(1))
        state.setdefault(key, {"attempts": 0, "retried_runs": []})
        state[key]["attempts"] += 1
        # 补发成功才记 retried_runs (防重复补发同一消息);
        # 失败不记 → 下次看门狗运行 (限流恢复后) 自动重试, 直到达到 max_retries.
        if r_ok:
            state[key]["retried_runs"].append(m.group(1))
            # 补发成功 → 通道恢复, 清除熔断 (若有)
            state.pop("cooldown_until", None)
        else:
            # 补发失败 → iLink 通道疑似持续故障, 熔断 2h 暂停后续补发 (不再雪上加霜)
            state["cooldown_until"] = (now + timedelta(hours=2)).isoformat()
            actions.append(
                f"🧯 {label}: 补发失败, iLink 通道故障 → 熔断 2h, 暂停后续补发"
            )
            break  # 熔断: 通道故障对全部任务生效, 停止本轮剩余补发
        actions.append(
            f"{'✅' if r_ok else '❌'} {label}: {age_min:.0f}分钟前投递失败, "
            f"{how}, 补发{'成功' if r_ok else '失败'} (今日第{state[key]['attempts']}次)"
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
