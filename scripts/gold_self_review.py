#!/usr/bin/env python3
"""系统自评 · 周复盘 — 预测准确率 + 推送健康度 + 持仓, 服务器自主运行 (Hermes cron).

问题#4 (2026-08-22): 反思进化的可见输出 (准确率/健康度) 原在本地 CLI, 依赖用户
手动触发 Claude Code. 本脚本部署到 Hermes cron (周日晚 21:00), 自动把系统自评
卡片投递微信 — 反思闭环不依赖人工触发.

数据源 (全服务器本地):
  - prediction_journal.jsonl  预测记录 (增量引擎 30min cron 自动结算)
  - gateway.log               推送健康 (rate limited / delivery failed)
  - decision_state.json       增量判断基准健康
  - portfolio.yaml            持仓/净保本

用法 (Hermes cron --no-agent, stdout 投递微信):
  PYTHONPATH=src python3 scripts/gold_self_review.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BEIJING = timezone(timedelta(hours=8))
_UTC = timezone.utc

PRED_JOURNAL = PROJECT_ROOT / "data" / "private" / "prediction_journal.jsonl"
GATEWAY_LOG = Path.home() / ".hermes" / "logs" / "gateway.log"
DECISION_STATE = PROJECT_ROOT / "data" / "private" / "decision_state.json"
PORTFOLIO = PROJECT_ROOT / "data" / "private" / "portfolio.yaml"


def _prediction_stats() -> dict:
    if not PRED_JOURNAL.exists():
        return {"total": 0, "settled": 0, "pending": 0}
    rows = []
    for line in PRED_JOURNAL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    settled = [r for r in rows if r.get("was_correct") is not None]
    pending = [r for r in rows if r.get("actual_price") is None]
    correct = sum(1 for r in settled if r.get("was_correct"))
    dirs = Counter((r.get("direction") or "neutral") for r in settled)
    return {
        "total": len(rows),
        "settled": len(settled),
        "pending": len(pending),
        "correct": correct,
        "accuracy": round(correct / len(settled), 2) if settled else None,
        "directions": dict(dirs.most_common(5)),
    }


def _push_health(days: int = 7) -> dict:
    """近 N 天 gateway.log 限流/投递失败计数 (尽力而为, 文件缺失返回空)."""
    if not GATEWAY_LOG.exists():
        return {}
    cutoff = (datetime.now(_UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
    rate = 0
    delivery = 0
    for line in GATEWAY_LOG.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.startswith(cutoff):
            continue
        if "rate limited" in line:
            rate += 1
        if "delivery error" in line or "Delivery failed" in line:
            delivery += 1
    return {"rate_limited": rate, "delivery_failed": delivery, "days": days}


def _decision_health() -> dict:
    if not DECISION_STATE.exists():
        return {}
    try:
        b = json.loads(DECISION_STATE.read_text(encoding="utf-8")).get("baseline", {})
        return {
            "direction": b.get("direction"),
            "direction_cn": b.get("direction_cn"),
            "range": b.get("target_range"),
            "score": b.get("score"),
            "last_delta": b.get("last_delta"),
            "updated_at": (b.get("updated_at") or "")[:16],
        }
    except Exception:
        return {}


def _portfolio_line() -> str:
    if not PORTFOLIO.exists():
        return ""
    try:
        import yaml

        p = yaml.safe_load(PORTFOLIO.read_text(encoding="utf-8"))
        pos = p["positions"]["gold_jd"]
        g = float(pos.get("grams", 0) or 0)
        cost = float(pos.get("avg_cost", 0))
        fee = float(pos.get("sell_fee_pct", 0)) / 100
        if g <= 0:
            return "空仓"
        return f"{g:.2f}g @ ¥{cost:.2f} (净保本 ¥{cost / (1 - fee):.2f})"
    except Exception:
        return ""


def main() -> int:
    pred = _prediction_stats()
    health = _push_health()
    dec = _decision_health()
    port = _portfolio_line()

    lines = ["🧠 黄金系统自评 · 周复盘", ""]

    # 预测自评
    acc = pred.get("accuracy")
    acc_str = f"{acc:.0%}" if acc is not None else "—"
    lines.append(f"📊 预测自评: {pred.get('settled', 0)} 结算 / 正确 {pred.get('correct', 0)}"
                 f" = **{acc_str}**" + (f" (待结算 {pred.get('pending', 0)})" if pred.get("pending") else ""))
    dirs = pred.get("directions") or {}
    if dirs:
        lines.append(f"   方向分布: {' '.join(f'{k}:{v}' for k, v in list(dirs.items())[:5])}")

    # 推送健康
    if health:
        lines.append(f"📮 推送健康(近{health.get('days', 7)}天): 限流 {health.get('rate_limited', 0)} 次"
                     f" | 投递失败 {health.get('delivery_failed', 0)} 次")

    # 增量基准健康
    if dec:
        lines.append(f"⚡ 增量基准: {dec.get('direction_cn', dec.get('direction', '?'))}"
                     f" | 评分 {dec.get('score', '-')}"
                     + (f" | 区间 {dec.get('range', '-')}" if dec.get("range") else "")
                     + (f" | 最近delta {dec.get('last_delta', '-')}" if dec.get("last_delta") else ""))

    # 持仓
    if port:
        lines.append(f"💼 持仓: {port}")

    lines.append("")
    lines.append(f"🤖 {datetime.now(BEIJING).strftime('%m-%d %H:%M')} | 青蚨 · 服务器自主复盘")
    print("\n".join(lines), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
