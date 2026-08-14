#!/usr/bin/env python3
"""校验日历中美国宏观数据事件的发布日期是否与权威经济日历一致.

背景 (2026-08-14 事故): PPI 被标成 08-14, 实际 BLS 为 08-13 (CPI 后一天).
现有 DOW 校验对 ppi 的期望星期是"周一-周五全放行", 拦不住"日期错一天但星期合法".

为什么用 TradingEconomics 而非 BLS 官网:
- BLS schedule 页面 (bls.gov/schedule/news_release/*.htm) 被 Akamai 反爬 (HTTP 403),
  curl/WebFetch 均被拒.
- TE 经济日历 (tradingeconomics.com/united-states/calendar) 可达, 每行含
  data-event (如 ppi/cpi/pce/non farm payrolls) + 发布日期 td, 字段规范稳定.

校验范围: ppi / cpi / pce / 非农 / FOMC纪要 — 这些是每月一次的"易错"事件,
日期错一天 = 发布日错误. 初请/非农等已有确定性 DOW 锚点的除外 (初请由 {3} 保护).

失败策略: TE 抓取失败 → 降级为 warning (不阻断, 网络抖动不应卡死分析);
TE 抓取成功但日期不一致 → error (阻断, 防止错误日期污染下游分析).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

# ---- 配置 ----

CALENDAR_FILE = Path("data/calendar_events.jsonl")
TE_URL = "https://tradingeconomics.com/united-states/calendar"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
TE_TIMEOUT = 20.0

# 校验目标: 日历 event_type → TE 事件匹配关键词 (data-event 或事件名子串).
# 排除初请 (每周四, DOW {3} 已锚定) 与 非农 (DOW {4} 已锚定, 但非农日期也纳入强校验).
# 匹配策略: TE 中 data-event/事件名 含任一关键词的事件视为同一发布日集合.
TARGET_MATCHERS: dict[str, list[str]] = {
    "ppi": ["ppi"],
    "cpi": ["cpi"],
    "pce": ["pce"],
    "fomc_minutes": ["fomc minutes", "fomc meeting minutes"],
    # 非农: 排除初请, 只匹配月度非农
    "nfp": ["non farm payrolls", "payrolls private", "manufacturing payrolls", "government payrolls"],
}

# 名称关键词 → 精确到特定子事件 (避免 ppi 把 core ppi 也算错月)
_NAME_HINTS: dict[str, list[str]] = {
    "初请": ["initial jobless claims"],  # 由 DOW 保护, 这里基本不触发
    "非农": ["non farm payrolls", "government payrolls", "manufacturing payrolls", "payrolls private"],
}


@dataclass(frozen=True)
class Finding:
    """单条校验发现."""
    severity: str  # "error" | "warning" | "info"
    event_name: str
    calendar_date: date | None
    official_date: date | None
    message: str


@dataclass
class TeEvent:
    """TradingEconomics 日历中的一条事件."""
    date: date
    data_event: str
    name: str


def _fetch_html(url: str) -> str:
    """多层降级抓取 HTML (macOS OpenSSL 兼容性问题 → fallback curl).

    参考 gld_holdings.py 模式:
    1. httpx 直连 (verify=True)
    2. httpx verify=False (绕过 macOS OpenSSL EOF)
    3. curl 子进程 (绕过 Python TLS 栈, 最可靠)
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    # Strategy 1: httpx verify=True
    try:
        with httpx.Client(timeout=TE_TIMEOUT, follow_redirects=True, verify=True) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.text
    except Exception:
        pass

    # Strategy 2: httpx verify=False (macOS OpenSSL EOF 常见)
    try:
        with httpx.Client(timeout=TE_TIMEOUT, follow_redirects=True, verify=False) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.text
    except Exception:
        pass

    # Strategy 3: curl 子进程 (绕过 Python TLS 栈, 已验证可达)
    try:
        result = subprocess.run(
            [
                "curl", "-sS", "--max-time", str(TE_TIMEOUT),
                "--connect-timeout", "10",
                "-H", "User-Agent: " + USER_AGENT,
                "-H", "Accept-Language: en-US,en;q=0.9",
                url,
            ],
            capture_output=True, text=True, timeout=TE_TIMEOUT + 10,
        )
        if result.returncode == 0 and result.stdout and len(result.stdout) > 1000:
            return result.stdout
    except Exception:
        pass

    raise RuntimeError("所有抓取策略均失败 (httpx×2 + curl)")


def fetch_te_calendar(start: date, end: date) -> list[TeEvent]:
    """抓取 TradingEconomics 美国经济日历, 解析为 TeEvent 列表.

    页面日期范围通过 d1/d2 URL 参数控制.
    """
    params = f"?d1={start.isoformat()}&d2={end.isoformat()}"
    url = TE_URL + params
    html = _fetch_html(url)

    # 解析事件行: <tr data-url="..." data-event="..." ...> ... <td class=' YYYY-MM-DD'> ... </tr>
    events: list[TeEvent] = []
    row_re = re.compile(
        r'<tr\s+data-url="[^"]*"[^>]*?data-event="([^"]*)"[^>]*?>(.*?)</tr>',
        re.S,
    )
    date_re = re.compile(r"<td[^>]*class='\s*(\d{4}-\d{2}-\d{2})'")
    name_re = re.compile(r'title="([^"]+)"')

    for m in row_re.finditer(html):
        data_event = m.group(1).strip().lower()
        body = m.group(2)
        dm = date_re.search(body)
        if not dm:
            continue
        try:
            ev_date = date.fromisoformat(dm.group(1))
        except ValueError:
            continue
        nm = name_re.search(body)
        name = nm.group(1) if nm else ""
        events.append(TeEvent(date=ev_date, data_event=data_event, name=name))

    return events


def match_te_date(events: list[TeEvent], matchers: list[str], target: date) -> date | None:
    """在 TE 事件中找与 target 同一月份的同类型发布日期.

    同类型事件每月一次 (ppi/cpi/pce/非农). 只匹配同月, 避免跨月误报
    (如日历 9月 PPI 被错误匹配到 TE 8月 PPI).
    返回 None = 该月无同类型事件 (TE 未排期/已过窗口), 调用方跳过而非报错.
    """
    candidates = [
        e for e in events
        if any(k in e.data_event for k in matchers)
    ]
    if not candidates:
        return None
    same_month = [
        e for e in candidates
        if e.date.year == target.year and e.date.month == target.month
    ]
    if not same_month:
        return None
    return min(same_month, key=lambda e: abs((e.date - target).days)).date


def load_calendar_events() -> list[dict]:
    """读取 calendar_events.jsonl."""
    if not CALENDAR_FILE.exists():
        return []
    events = []
    for line in CALENDAR_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def run(
    *,
    days_back: int = 7,
    days_ahead: int = 45,
    offline: bool = False,
) -> tuple[list[Finding], bool]:
    """执行校验. 返回 (findings, te_available)."""
    findings: list[Finding] = []

    cal_events = load_calendar_events()
    today = date.today()
    window_start = today - timedelta(days=days_back)
    window_end = today + timedelta(days=days_ahead)

    # 提取窗口内需要校验的事件
    targets: list[tuple[dict, str, date]] = []
    for e in cal_events:
        et = e.get("event_type", "")
        matchers = TARGET_MATCHERS.get(et)
        if not matchers:
            continue
        # 已发布事件 (actual 非空): 日期已由发布确认, 无校验意义, 跳过
        if e.get("actual"):
            continue
        sat = e.get("scheduled_at", "")
        if not sat:
            continue
        try:
            dt = datetime.fromisoformat(sat)
            ev_date = dt.date()
        except (ValueError, TypeError):
            continue
        if not (window_start <= ev_date <= window_end):
            continue
        # 名称提示: 用更精确的 matcher (如非农 vs 初请)
        name = e.get("name", "")
        hints = [m for kw, ms in _NAME_HINTS.items() if kw in name for m in ms]
        used_matchers = hints if hints else matchers
        targets.append((e, et, ev_date))

    if not targets:
        return findings, False

    # 抓取 TE 日历
    if offline:
        findings.append(Finding("info", "OFFLINE", None, None,
                                "离线模式: 跳过 TE 抓取, 仅跑本地相对锚点校验"))
        return findings, False

    try:
        fetch_start = min(ev_date for _, _, ev_date in targets) - timedelta(days=7)
        fetch_end = max(ev_date for _, _, ev_date in targets) + timedelta(days=7)
        te_events = fetch_te_calendar(fetch_start, fetch_end)
    except Exception as exc:
        findings.append(Finding(
            "warning", "TE_FETCH_FAIL", None, None,
            f"TradingEconomics 日历抓取失败: {exc} — 日期比对降级跳过 "
            f"(请网络可用后重跑, 或手动核对官网 schedule)",
        ))
        return findings, False

    if not te_events:
        findings.append(Finding(
            "warning", "TE_EMPTY", None, None,
            "TradingEconomics 日历无事件 — 页面可能结构变化, 日期比对降级跳过",
        ))
        return findings, False

    # 逐事件比对
    for e, et, ev_date in targets:
        name = e.get("name", "")
        matchers = TARGET_MATCHERS[et]
        hints = [m for kw, ms in _NAME_HINTS.items() if kw in name for m in ms]
        used = hints if hints else matchers
        official = match_te_date(te_events, used, ev_date)
        if official is None:
            findings.append(Finding(
                "info", name, ev_date, None,
                f"[{et}] TE 范围内未找到 {used} 事件, 跳过",
            ))
            continue
        diff = (official - ev_date).days
        if diff == 0:
            findings.append(Finding(
                "info", name, ev_date, official,
                f"[{et}] 日期一致 ✅ ({ev_date})",
            ))
        else:
            findings.append(Finding(
                "error", name, ev_date, official,
                f"[{et}] 日期不一致 ❌ 日历={ev_date} vs 官方={official} "
                f"(差 {abs(diff)} 天). 请修正 calendar_events.jsonl. "
                f"DOW 校验无法拦截此类偏移, 以官方发布日为准.",
            ))

    return findings, True


def _print_findings(findings: list[Finding], te_available: bool) -> None:
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]

    if te_available:
        print("🌐 官方日历比对 (TradingEconomics)")
    else:
        print("⚠️ 官方日历比对不可用 (降级)")

    infos = [f for f in findings if f.severity == "info" and f.event_name not in ("OFFLINE",)]
    for f in infos:
        print(f"  {f.message}")

    for f in warnings:
        print(f"  ⚠️ {f.message}")

    for f in errors:
        print(f"  ❌ {f.event_name}: {f.message}")

    print(f"\n结果: {len(errors)} 错误, {len(warnings)} 警告, {len(infos)} 通过")
    if errors:
        print("🔴 存在日期错误, 禁止基于错误日历继续分析")


def main() -> int:
    parser = argparse.ArgumentParser(description="校验宏观事件发布日期 vs 官方日历")
    parser.add_argument("--days-back", type=int, default=7, help="回看天数")
    parser.add_argument("--days-ahead", type=int, default=45, help="前看天数")
    parser.add_argument("--offline", action="store_true", help="离线模式, 不抓取网络")
    parser.add_argument("--fail-on-error", action="store_true",
                        help="有 error 时以非零码退出 (供 CI/prepare 阻断)")
    args = parser.parse_args()

    findings, te_available = run(
        days_back=args.days_back,
        days_ahead=args.days_ahead,
        offline=args.offline,
    )
    _print_findings(findings, te_available)

    if args.fail_on_error and any(f.severity == "error" for f in findings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
