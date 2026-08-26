#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预测回溯统计检验 — 对 prediction_journal.jsonl 历史预测做命中率/校准度统计.

目的 (2026-08-26):
  1. 量化系统历史预测的真实命中率与方向能力;
  2. 诊断「neutral(观望) 主导 + 宽松判定阈值」是否制造命中率假象;
  3. 输出置信度校准度 (Brier score) 与数据质量警告。

判定阈值 (与 improvement/tracker.py determine_correctness 一致):
  - long : actual_return > 0
  - short: actual_return < 0
  - neutral: |actual_return| < NEUTRAL_BAND (1.5%)

用法:
    python3 scripts/prediction_stats.py [--path data/private/prediction_journal.jsonl]
    python3 scripts/prediction_stats.py --json   # JSON 输出 (供程序消费)
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from collections import Counter
from pathlib import Path

from gold_miner.improvement.calibration import build_calibration, calibrate_confidence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = PROJECT_ROOT / "data" / "private" / "prediction_journal.jsonl"

# 判定阈值 (与 improvement/tracker.py 一致)
NEUTRAL_BAND = 0.015
DIR_THRESHOLD = 0.0

# 方向性 (真正押方向) 的 direction 值
DIRECTIONAL = ("buy", "sell", "long", "short", "hold")


def _is_correct(direction: str, actual_return: float) -> bool:
    """按判定阈值判断方向是否正确 (与 tracker.determine_correctness 一致)."""
    d = str(direction or "").lower()
    if d in ("buy", "long"):
        return actual_return > DIR_THRESHOLD
    if d in ("sell", "short"):
        return actual_return < -DIR_THRESHOLD
    return abs(actual_return) < NEUTRAL_BAND


def load(path: Path) -> list[dict]:
    records: list[dict] = []
    if not path.exists():
        sys.exit(f"❌ 预测日记不存在: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def collect(records: list[dict]) -> dict:
    """过滤无效记录, 分组统计. 返回结构化结果."""
    n_total = len(records)
    invalidated = [r for r in records if r.get("invalidated")]
    valid = [r for r in records if not r.get("invalidated")]
    resolved = [r for r in valid if r.get("actual_price") is not None]
    unresolved = [r for r in valid if r.get("actual_price") is None]

    # 方向命中
    by_dir: dict[str, dict] = {}
    for d in sorted({r.get("direction") for r in resolved}):
        grp = [r for r in resolved if r.get("direction") == d]
        rets = [r.get("actual_return") or 0 for r in grp]
        by_dir[d] = {
            "n": len(grp),
            "correct": sum(1 for r in grp if _is_correct(r.get("direction"), r.get("actual_return") or 0)),
            "avg_return": st.mean(rets) if rets else 0,
            "avg_conf": st.mean([r.get("confidence") or 0 for r in grp]) if grp else 0,
            "dates": sorted({(r.get("timestamp") or "")[:10] for r in grp}),
        }

    # 中性判定阈值敏感性: 不同 NEUTRAL_BAND 下 neutral 命中率
    neutral_recs = [r for r in resolved if (r.get("direction") or "").lower() in ("neutral", "hold")]
    band_sensitivity = []
    for band in (0.005, 0.01, 0.015, 0.02, 0.03, 0.05):
        ok = sum(1 for r in neutral_recs if abs(r.get("actual_return") or 0) < band)
        band_sensitivity.append({
            "band": band,
            "n": len(neutral_recs),
            "hit_rate": ok / len(neutral_recs) if neutral_recs else 0,
        })

    # 置信度校准
    conf_buckets = []
    for lo, hi in ((0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01)):
        grp = [r for r in resolved if lo <= (r.get("confidence") or 0) < hi]
        if grp:
            ok = sum(1 for r in grp if _is_correct(r.get("direction"), r.get("actual_return") or 0))
            conf_buckets.append({"range": f"{lo:.1f}-{hi:.1f}", "n": len(grp), "hit_rate": ok / len(grp)})

    # Brier score (confidence 作为预测概率 vs 实际结果)
    brier_terms = []
    for r in resolved:
        conf = r.get("confidence") or 0
        actual = 1.0 if _is_correct(r.get("direction"), r.get("actual_return") or 0) else 0.0
        brier_terms.append((conf - actual) ** 2)
    brier = st.mean(brier_terms) if brier_terms else 0.0

    # 方向级置信度校准 (2026-08-26): 用历史同方向命中率校准后重算 Brier
    calib_table = build_calibration(resolved)
    calib_terms = []
    for r in resolved:
        conf = calibrate_confidence(r.get("direction"), r.get("confidence") or 0, calib_table)
        actual = 1.0 if _is_correct(r.get("direction"), r.get("actual_return") or 0) else 0.0
        calib_terms.append((conf - actual) ** 2)
    brier_calibrated = st.mean(calib_terms) if calib_terms else 0.0

    # 方向性预测 (真正押方向, 排除 neutral)
    directional = [r for r in resolved if (r.get("direction") or "").lower() not in ("neutral", "hold")]
    dir_correct = sum(1 for r in directional if _is_correct(r.get("direction"), r.get("actual_return") or 0))

    overall_correct = sum(1 for r in resolved if _is_correct(r.get("direction"), r.get("actual_return") or 0))
    overall_ret = st.mean([r.get("actual_return") or 0 for r in resolved]) if resolved else 0

    return {
        "n_total": n_total,
        "n_invalidated": len(invalidated),
        "n_valid": len(valid),
        "n_resolved": len(resolved),
        "n_unresolved": len(unresolved),
        "by_dir": by_dir,
        "neutral_ratio": (len(neutral_recs) / len(resolved)) if resolved else 0,
        "band_sensitivity": band_sensitivity,
        "conf_buckets": conf_buckets,
        "brier": brier,
        "brier_calibrated": brier_calibrated,
        "calib_table": calib_table,
        "n_directional": len(directional),
        "directional_correct": dir_correct,
        "overall_correct": overall_correct,
        "overall_ret": overall_ret,
        "invalid_reasons": Counter((r.get("invalidation_reason") or "")[:60] for r in invalidated),
        "time_span_resolved": sorted({(r.get("resolved_at") or (r.get("timestamp") or ""))[:10] for r in resolved}),
    }


def render_md(s: dict) -> str:
    L: list[str] = []
    L.append("# 📊 预测回溯统计检验")
    L.append("")
    L.append("## 1. 数据健康度")
    L.append("| 指标 | 数值 |")
    L.append("|---|---|")
    L.append(f"| 总记录 | {s['n_total']} |")
    L.append(f"| 无效(invalidated) | {s['n_invalidated']} ({s['n_invalidated']/max(s['n_total'],1):.0%}) |")
    L.append(f"| 有效 | {s['n_valid']} |")
    L.append(f"| 已结算 | {s['n_resolved']} |")
    L.append(f"| 未到期 | {s['n_unresolved']} |")
    L.append(f"| 结算时间跨度 | {s['time_span_resolved'][0] if s['time_span_resolved'] else '-'} ~ {s['time_span_resolved'][-1] if s['time_span_resolved'] else '-'} |")
    L.append("")
    if s["invalid_reasons"]:
        L.append(f"> ⚠️ 无效原因: {s['invalid_reasons'].most_common(1)[0][0]}")
        L.append("")
    L.append("## 2. 方向命中率 (判定阈值: long=ret>0 / short=ret<0 / neutral=|ret|<1.5%)")
    L.append("| 方向 | 样本 | 命中 | 命中率 | 平均收益 | 平均置信度 |")
    L.append("|---|---|---|---|---|---|")
    for d, v in s["by_dir"].items():
        L.append(f"| {d} | {v['n']} | {v['correct']} | {v['correct']/v['n']:.0%} | {v['avg_return']:+.2%} | {v['avg_conf']:.2f} |")
    L.append(f"| **合计** | **{s['n_resolved']}** | **{s['overall_correct']}** | **{s['overall_correct']/max(s['n_resolved'],1):.0%}** | {s['overall_ret']:+.2%} | - |")
    L.append("")
    L.append("## 3. ⚠️ 方向失衡诊断")
    L.append(f"- **neutral(观望) 占比 {s['neutral_ratio']:.0%}** — 系统绝大多数输出「观望」")
    L.append(f"- **真正押方向 (buy/sell/hold) 仅 {s['n_directional']} 条, 命中 {s['directional_correct']} 条 ({s['directional_correct']/max(s['n_directional'],1):.0%})** — 样本不足以验证方向能力")
    L.append("- **neutral 判定标准 = |收益| < 1.5%** — 金价「没怎么动」即判正确, 在震荡市天然高命中, 是命中率假象的主要来源")
    L.append("")
    L.append("### 3.1 neutral 命中率随阈值变化 (敏感性)")
    L.append("| 判定阈值 ±| 样本 | 命中率 |")
    L.append("|---|---|---|")
    for b in s["band_sensitivity"]:
        L.append(f"| {b['band']:.1%} | {b['n']} | {b['hit_rate']:.0%} |")
    L.append("")
    L.append("## 4. 置信度校准 (方向级)")
    L.append("| 方向 | 样本 | 历史命中率 | 平均原始置信度 | 校准后置信度 |")
    L.append("|---|---|---|---|---|")
    for d, v in s["calib_table"].items():
        cal = f"{v['hit_rate']:.0%}" if v["n"] >= 10 else "保留原始"
        L.append(f"| {d} | {int(v['n'])} | {v['hit_rate']:.0%} | {v['mean_conf']:.2f} | {cal} |")
    L.append(f"\n**Brier: 原始 {s['brier']:.4f} → 方向级校准后 {s['brier_calibrated']:.4f}** (0=完美, 越低越好)")
    L.append("")
    L.append("> 校准策略: 方向历史样本 ≥10 才用命中率校准, 否则保留原始置信度 (小样本不可靠)。")
    L.append("> 中性命中率是「|收益|<1.5% 中性带」概率, 反映行情波动而非预测能力。")
    L.append("")
    L.append("### 4.1 置信度区间命中率 (未校准, 供参考)")
    L.append("| 置信度区间 | 样本 | 实际命中率 |")
    L.append("|---|---|---|")
    for b in s["conf_buckets"]:
        L.append(f"| {b['range']} | {b['n']} | {b['hit_rate']:.0%} |")
    L.append("")
    L.append("## 5. 数据质量警告")
    warns = []
    if s["n_directional"] < 30:
        warns.append(f"🔴 方向性预测样本仅 {s['n_directional']} 条 (<30), 方向能力统计无意义")
    if s["neutral_ratio"] > 0.7:
        warns.append(f"🔴 neutral 占比 {s['neutral_ratio']:.0%} (>70%), 方向预测几乎不输出, 系统可能过于保守")
    for d, v in s["by_dir"].items():
        if len(v["dates"]) <= 1:
            warns.append(f"🟡 「{d}」预测集中在单日 ({v['dates']}), 可能批量结算, 样本非独立")
    if s["brier"] > 0.25:
        warns.append(f"🟡 Brier {s['brier']:.4f} > 0.25, 置信度未校准 (高置信≠高命中)")
    L.extend(f"- {w}" for w in warns)
    L.append("")
    L.append("## 6. 结论")
    L.append("> 表面高命中率主要由 **neutral 宽松阈值 + 震荡市低波动** 制造, 不能作为预测能力证据;")
    L.append("> 真正方向能力 (buy/sell) 样本极小且判定依赖方向性阈值, 需积累更多方向性预测后才能可靠评估。")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="预测回溯统计检验")
    ap.add_argument("--path", default=str(DEFAULT_PATH))
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    s = collect(load(Path(args.path)))
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_md(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
