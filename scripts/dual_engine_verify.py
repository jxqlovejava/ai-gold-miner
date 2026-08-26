#!/usr/bin/env python3
"""双引擎交叉验证 — 英文一手源事件补查的强制工具（2026-08-26 起接入事件同步铁律）。

引擎A = anysearch（主引擎，web+垂直域，快而全）
引擎B = wigolo  (独立第二引擎, 带 --category=news --from --force-refresh, 与 anysearch 不同引擎族)

为什么带这些参数（实证于 2026-08-26 双引擎互证测试）：
- wigolo 裸搜会把陈旧内容当最新（曾把 2023-07 非农 187K 当最新，相关度仍打 0.83）——reranker 无新鲜度感知，
  必须 --category=news --from=<日期> 约束
- --no-content 避免内容 enrich 超时（裸搜实测 5 条 fetch timeout 只剩 snippet）
- --force-refresh 绕过缓存，确保拿到最新

用法（英文一手源 T0/T1 事件补查必走此工具，输出须标注引擎来源）：
  PYTHONPATH=src python3 scripts/dual_engine_verify.py "US nonfarm payrolls July 2026 unemployment rate" --from 2026-08-01
  # 可选: --max-results N (默认 6/5), --reversal 追加逆转/修正查询(快速演变事件)

输出：引擎A/B 标注结果表 + 引擎级互证点 + 可靠性标注（snippet-only/fetch failed）+ 结论提示。
本工具只给证据，数值判定与 gold_bias 由调用模型按 sourcing_protocol 自行判定。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

ANYSEARCH_CLI = Path(os.path.expanduser(
    "~/.claude/skills/anysearch/scripts/anysearch_cli.py"
))
# T0/T1 一手/权威域名，用于互证点加权提示
AUTHORITATIVE_DOMAINS = (
    "bls.gov", "federalreserve.gov", "fred", "cme", "bea.gov", "reuters.com",
    "bloomberg", "ft.com", "wsj.com", "kitco.com", "tradingeconomics.com",
)


def run(cmd: list[str], timeout: int = 60) -> tuple[int, str, str]:
    """执行子命令, 返回 (rc, stdout, stderr)。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except FileNotFoundError as e:
        return -1, "", f"command not found: {e}"


# ── 引擎A: anysearch ──
def engine_anysearch(query: str, max_results: int) -> list[dict]:
    rc, out, err = run([
        sys.executable, str(ANYSEARCH_CLI), "search", query,
        "--max_results", str(max_results),
    ])
    if rc != 0:
        print(f"  ⚠️ anysearch 失败 (rc={rc}): {err.strip()[:200]}")
        return []
    results = []
    for block in re.split(r"\n### ", out):
        m = re.search(r"(\d+)\.\s+(.+?)\s*\n", block)
        if not m:
            continue
        title = m.group(2).strip()
        url = ""
        mu = re.search(r"\*\*URL\*\*:\s*(\S+)", block)
        if mu:
            url = mu.group(1)
        snippet = re.sub(r"^.*?\*\*URL\*\*[^\n]*\n", "", block, flags=re.M | re.S).strip()
        snippet = " ".join(snippet.split())[:200]
        if url:
            results.append({"title": title, "url": url, "snippet": snippet})
    return results


# ── 引擎B: wigolo (独立第二引擎, 必须日期约束) ──
def engine_wigolo(query: str, from_date: str, max_results: int) -> list[dict]:
    cmd = [
        "npx", "wigolo", "search", query,
        "--category=news", f"--from={from_date}",
        "--force-refresh", "--no-content",
        f"--max-results={max_results}", "--json",
    ]
    rc, out, err = run(cmd, timeout=120)
    if rc != 0:
        print(f"  ⚠️ wigolo 不可用 (rc={rc}): {err.strip()[:200]} —— 引擎B缺失，单引擎结果需 WebSearch/手动补确认")
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        print("  ⚠️ wigolo 输出非 JSON，引擎B降级跳过")
        return []
    results = []
    for r in data.get("results", [])[:max_results]:
        results.append({
            "title": (r.get("title") or "")[:100],
            "url": r.get("url") or "",
            "snippet": " ".join((r.get("snippet") or "").split())[:200],
            "score": r.get("relevance_score"),
            "fetch_failed": r.get("fetch_failed"),
            "snippet_only": r.get("content_from_snippet"),
        })
    return results


def host_of(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url or "").split("/")[0]


def print_engine(label: str, note: str, results: list[dict]):
    print(f"\n[{label}] {note}")
    if not results:
        print("  无结果")
        return
    for i, r in enumerate(results, 1):
        rel = ""
        if "score" in r and r["score"] is not None:
            rel = f" (相关度{r['score']:.2f})"
        flag = ""
        if r.get("fetch_failed"):
            flag = " [fetch失败·仅snippet]"
        elif r.get("snippet_only"):
            flag = " [仅snippet]"
        print(f"  {i}. {r['title']}{rel}{flag}")
        print(f"     {r['url']}")
        if r.get("snippet"):
            print(f"     {r['snippet']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="双引擎交叉验证：anysearch(主) + wigolo(独立第二引擎)")
    ap.add_argument("query", help="查询（英文一手源事件关键词，建议含事件名+日期）")
    ap.add_argument("--from", dest="from_date", default=None,
                    help="最早发布日期 YYYY-MM-DD（wigolo 日期约束，缺省=今天-14天）")
    ap.add_argument("--max-results", type=int, default=6, help="anysearch 结果数 (默认6)")
    ap.add_argument("--reversal", action="store_true",
                    help="追加逆转/修正查询（快速演变事件用）")
    args = ap.parse_args()

    from_date = args.from_date or (date.today() - timedelta(days=14)).isoformat()
    print(f"# 双引擎交叉验证\n查询: {args.query}\n日期约束: {args.from_date or ('自 ' + from_date + ' (默认14天)')}")

    # 引擎A
    a_results = engine_anysearch(args.query, args.max_results)
    print_engine("引擎A: anysearch", "主引擎", a_results)

    # 引擎B
    b_results = engine_wigolo(args.query, from_date, max(1, args.max_results - 1))
    engines_used = f"wigolo news(自{from_date}, force-refresh)"
    print_engine("引擎B: wigolo", f"独立第二引擎 — {engines_used}", b_results)

    # 引擎级互证点: 两引擎都返回的域名
    a_domains = {host_of(r["url"]) for r in a_results}
    b_domains = {host_of(r["url"]) for r in b_results}
    shared = a_domains & b_domains
    print("\n[引擎级互证]")
    if shared:
        for d in sorted(shared):
            tag = " ★T0/T1" if any(k in d for k in AUTHORITATIVE_DOMAINS) else ""
            print(f"  ✓ 两引擎均命中: {d}{tag} —— 独立引擎互证点")
    else:
        print("  两引擎无同域命中 —— 无引擎级互证，需人工核对数值是否一致")
    # 权威域名出现提示
    seen_auth = {d for d in a_domains | b_domains if any(k in d for k in AUTHORITATIVE_DOMAINS)}
    if seen_auth:
        print(f"  权威源出现: {', '.join(sorted(seen_auth))} —— 以此为准校验数值")

    if args.reversal:
        print("\n[逆转/修正查询] 快速演变事件须另搜 reversal/backtrack/withdraw/update（见 event-sync §1.10）")

    # 可靠性统计
    snippet_only = sum(1 for r in a_results + b_results if r.get("snippet_only") or r.get("fetch_failed"))
    total = len(a_results) + len(b_results)
    if total and snippet_only:
        print(f"\n[可靠性] {snippet_only}/{total} 条仅snippet/fetch失败 —— 涉及关键数值须 fetch 原文复核")
    print("\n[结论提示] 本工具只给证据。数值一致性 + gold_bias 由调用方按 sourcing_protocol 判定，报告中标注引擎来源。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
