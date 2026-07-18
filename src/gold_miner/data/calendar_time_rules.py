"""日历时刻硬规则 — 防止「北京钟点误写成美东」导致决策偏差.

背景事故 (2026-07-15):
  沃什众议院听证官方 10:00 ET = 北京 22:00 (7/14)。
  日历误存 22:00 ET → 展示成北京 7/15 10:00，使分析误判「会还没开」。
  根因: 先把 ET 换成北京 22:00，再把 22:00 当 ET 写回 (双重换算)。

写入契约 (强制):
  1. scheduled_at 一律存 **美东本地钟点** + 正确 DST 偏移 (-04:00/-05:00)
  2. 写入前必须同时算出并人工核对 **ET 与北京** 两列
  3. 官网/委员会 notice 的钟点是 ET (或 GMT)，禁止把北京钟点直接贴进 scheduled_at
  4. 校验脚本对「国会听证晚间 ET」等典型双重换算形态报 🔴 错误

本模块供 scripts/validate_calendar_dates.py 与 EventCalendar.add_event 共用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

_BEIJING_TZ = timezone(timedelta(hours=8))

# 名称关键词 → 国会/半年度听证类 (几乎总是美东上午)
_HEARING_NAME_RE = re.compile(
    r"听证|国会|House|Senate|Humphrey|Monetary Policy Report|"
    r"金融服务委员会|银行委员会|Banking Committee|Financial Services",
    re.IGNORECASE,
)

# BLS/BEA 类数据发布: 官方几乎固定 08:30 ET
_DATA_TYPES = frozenset({"cpi", "ppi", "pce", "nfp"})

# 各类型允许的美东小时 (含端点); None = 不检查小时
# 说明: 这是防双重换算的安全网, 不是完整官网替代
_HOUR_WINDOWS: dict[str, tuple[int, int] | None] = {
    "cpi": (8, 9),          # 通常 08:30
    "ppi": (8, 9),
    "pce": (8, 9),
    "nfp": (8, 9),          # 非农/初请多为 08:30
    "fed_rate": (13, 15),   # FOMC 声明多在 14:00 ET
    "fomc_minutes": (13, 15),
    "fed_speech": (8, 17),  # 讲话/听证通常工作日白天; 晚间另有听证规则
    "pmi": (8, 12),
    "pmi_markit": (8, 12),
    "ecb": (5, 14),         # 欧洲时间, 允许更早 ET
    "boe": (5, 14),
    "geo": None,
    "monitor": None,
    "gold_reserve": None,
}


@dataclass(frozen=True)
class TimeCheckFinding:
    severity: str  # "error" | "warning"
    code: str
    message: str


def to_beijing(et_dt: datetime) -> datetime:
    if et_dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware (store ET with offset)")
    return et_dt.astimezone(_BEIJING_TZ)


def dual_clock_str(et_dt: datetime) -> str:
    """强制双列展示: ET | 北京."""
    bj = to_beijing(et_dt)
    et_off = et_dt.utcoffset()
    label = "EDT" if et_off == timedelta(hours=-4) else (
        "EST" if et_off == timedelta(hours=-5) else "ET?"
    )
    return (
        f"ET {et_dt.strftime('%Y-%m-%d %H:%M')} ({label}) | "
        f"北京 {bj.strftime('%Y-%m-%d %H:%M')}"
    )


def is_hearing_like(name: str, event_type: str = "") -> bool:
    """是否为国会/正式听证主事件 (monitor「观测:…听证后…」不算)."""
    if event_type in ("monitor", "geo"):
        return False
    n = name or ""
    if n.startswith("观测"):
        return False
    if event_type == "fed_speech" and _HEARING_NAME_RE.search(n):
        return True
    return bool(_HEARING_NAME_RE.search(n))


def check_event_clock(
    *,
    name: str,
    event_type: str,
    scheduled_at: datetime,
) -> list[TimeCheckFinding]:
    """检查单条事件的钟点是否像「双重换算」或偏离美国官方惯例.

    Returns:
        findings: error 必须阻断写入/分析; warning 需人工确认.
    """
    findings: list[TimeCheckFinding] = []

    if scheduled_at.tzinfo is None:
        findings.append(TimeCheckFinding(
            "error",
            "naive_datetime",
            f"[{event_type}] {name}: scheduled_at 无时区, 禁止写入 (须带 -04:00/-05:00)",
        ))
        return findings

    hour = scheduled_at.hour
    dual = dual_clock_str(scheduled_at)

    # --- 国会听证: 美东上午, 晚间几乎必是双重换算 ---
    if is_hearing_like(name, event_type):
        if hour >= 18 or hour < 8:
            findings.append(TimeCheckFinding(
                "error",
                "hearing_double_convert",
                f"[{event_type}] {name}: 国会/听证类事件 ET 钟点={hour:02d}:xx 异常 "
                f"(应为美东上午, 常见 09:00-11:00)。高度疑似把「北京钟点」写进了 ET。"
                f" 当前 {dual}。"
                f" 修复: 官网/notice 的 10:00 ET 应存 10:00-04:00, 北京才是 +12h 的晚上。"
                f" 禁止: 先换成北京22:00 再把22:00当ET存。",
            ))
        elif not (9 <= hour <= 11):
            findings.append(TimeCheckFinding(
                "warning",
                "hearing_unusual_hour",
                f"[{event_type}] {name}: 听证 ET={hour:02d} 非典型 09-11, 请查委员会 notice。{dual}",
            ))
        return findings

    # --- BLS/BEA 数据: 固定 08:30 一带 ---
    if event_type in _DATA_TYPES:
        lo, hi = _HOUR_WINDOWS[event_type]  # type: ignore[misc]
        if hour < lo or hour > hi:
            # 白天写错成晚上 = 双重换算高危
            sev = "error" if hour >= 12 else "warning"
            findings.append(TimeCheckFinding(
                sev,
                "data_release_hour",
                f"[{event_type}] {name}: 美国宏观数据通常 08:30 ET, 当前 ET hour={hour}. {dual}",
            ))
        return findings

    # --- 通用类型窗口 ---
    window = _HOUR_WINDOWS.get(event_type)
    if window is not None:
        lo, hi = window
        if hour < lo or hour > hi:
            # fed_speech 晚间: 可能是欧洲, 也可能双重换算 → 警告
            sev = "warning"
            if event_type == "fed_speech" and hour >= 18:
                sev = "warning"
                findings.append(TimeCheckFinding(
                    sev,
                    "fed_speech_late_et",
                    f"[{event_type}] {name}: 美东晚间讲话少见, 请确认是否误把北京时间当 ET。"
                    f" 若是欧洲日程, 用欧洲官方当地时换算 ET 而非北京钟点。{dual}",
                ))
            else:
                findings.append(TimeCheckFinding(
                    sev,
                    "hour_window",
                    f"[{event_type}] {name}: ET hour={hour} 超出惯例窗口 [{lo},{hi}]. {dual}",
                ))

    return findings


def check_events(events: Iterable[dict]) -> tuple[list[str], list[str]]:
    """批量检查 dict 事件 (jsonl 行). 返回 (errors, warnings) 字符串列表."""
    errors: list[str] = []
    warnings: list[str] = []
    for e in events:
        name = e.get("name", "")
        etype = e.get("event_type", "")
        scheduled = e.get("scheduled_at", "")
        if not scheduled:
            continue
        try:
            dt = datetime.fromisoformat(scheduled)
        except ValueError:
            errors.append(f"无法解析日期: {name} → {scheduled}")
            continue
        for f in check_event_clock(name=name, event_type=etype, scheduled_at=dt):
            msg = f.message
            if f.severity == "error":
                errors.append(msg)
            else:
                warnings.append(msg)
    return errors, warnings


# ---- DOW (星期) 硬规则 — 防止输出「周三初请失业金」类错误 ----

# 事件类型 → 允许的美东星期 (0=Mon, 6=Sun)
# None = 不检查
_DOW_RULES: dict[str, set[int] | None] = {
    "nfp": {3},                    # 初请失业金永远周四; 非农永远周五
    "fed_rate": {2},               # FOMC 决议日永远是周三 (14:00 ET)
    "fomc_minutes": {2, 3},        # 纪要通常周三，偶尔调整
    "cpi": {1, 2, 3, 4},          # 周二-周五常见
    "ppi": {0, 1, 2, 3, 4},       # 周一-周五均可能
    "pce": {1, 2, 3, 4},          # 周二-周五
    "pmi": {0, 1, 2, 3, 4},
    "pmi_markit": {0, 1, 2, 3, 4},
    "fed_speech": {0, 1, 2, 3, 4},
    "ecb": {0, 1, 2, 3, 4},
    "boe": {0, 1, 2, 3, 4},
    "geo": None,                    # 地缘事件任何一天都可能
    "monitor": None,                # monitor 任何一天都行
    "gold_reserve": None,
}

# 事件名称关键词 → 期望 DOW 覆盖 (优先级高于类型规则)
_NAME_DOW_OVERRIDES: dict[str, set[int]] = {
    "初请失业金": {3},             # 永远周四
    "非农就业": {4},               # 永远周五 (偶尔周四但罕见)
    "非农": {4},
}

_WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
_WEEKDAY_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def expected_dow(event_type: str, name: str = "") -> set[int] | None:
    """返回某事件类型期望的美东星期 (0=Mon, 6=Sun). None=不限制."""
    # 名称覆盖优先
    for keyword, dows in _NAME_DOW_OVERRIDES.items():
        if keyword in name:
            return dows
    return _DOW_RULES.get(event_type)


def fmt_dow_set(dows: set[int] | None) -> str:
    """格式化期望 DOW 为人类可读字符串."""
    if dows is None:
        return "不限"
    return ", ".join(_WEEKDAY_CN[d] for d in sorted(dows))


def check_event_dow(
    *,
    name: str,
    event_type: str,
    scheduled_at: datetime,
) -> list[TimeCheckFinding]:
    """检查事件的 ET 星期是否符合该类型惯例.

    Returns:
        findings: 星期异常 = warning (不阻断写入, 但需人工确认).
    """
    findings: list[TimeCheckFinding] = []

    if scheduled_at.tzinfo is None:
        return findings  # 钟点检查已报 error, 不重复

    dow = scheduled_at.weekday()
    dow_cn = _WEEKDAY_CN[dow]
    dual = dual_clock_str(scheduled_at)

    # 周末检查 (geo/monitor 除外)
    if dow >= 5 and event_type not in ("geo", "monitor"):
        findings.append(TimeCheckFinding(
            "error",
            "weekend_event",
            f"[{event_type}] {name}: 安排在周末 (ET {_WEEKDAY_EN[dow]})。"
            f" 官方数据/会议不会在周末发布。{dual}",
        ))
        return findings

    expected = expected_dow(event_type, name)
    if expected is not None and dow not in expected:
        expected_str = ", ".join(_WEEKDAY_CN[d] for d in sorted(expected))

        # 名称覆盖匹配 → 已知确定性事件, DOW 错误 = error 阻断
        is_name_override = any(kw in name for kw in _NAME_DOW_OVERRIDES)
        sev = "error" if is_name_override else "warning"

        findings.append(TimeCheckFinding(
            sev,
            "dow_anomaly",
            f"[{event_type}] {name}: ET 星期={dow_cn}({_WEEKDAY_EN[dow]}), "
            f"该类型通常为 {expected_str}。请确认日期是否正确。{dual}",
        ))

    return findings


# ---- 输出参考表 ----

def generate_dow_reference_table(
    events: list[dict],
    days_ahead: int = 30,
) -> str:
    """生成未来事件的 DOW 参考表 (Markdown), 供分析报告引用.

    每行包含: 事件名 | ET日期 | ET星期 | 北京时间 | 北京星期 | 期望DOW
    异常行会在末尾标注 ⚠️。
    """
    from datetime import timezone as _tz

    now = datetime.now(tz=_tz.utc)
    cutoff = now + timedelta(days=days_ahead)

    rows: list[str] = []
    header = (
        "| 事件名称 | ET 日期 | ET 星期 | 北京时间 | 北京星期 | 期望 | 校验 |\n"
        "|----------|---------|---------|----------|----------|------|------|"
    )
    rows.append(header)

    for e in events:
        name = e.get("name", "")
        etype = e.get("event_type", "")
        sat_str = e.get("scheduled_at", "")
        if not sat_str:
            continue
        try:
            dt = datetime.fromisoformat(sat_str)
        except ValueError:
            continue

        # 只显示未来的事件 + 最近 7 天已过去的
        if dt < now - timedelta(days=7) or dt > cutoff:
            continue

        bj = to_beijing(dt) if dt.tzinfo else dt
        et_dow_cn = _WEEKDAY_CN[dt.weekday()]
        et_dow_en = _WEEKDAY_EN[dt.weekday()]
        bj_dow_cn = _WEEKDAY_CN[bj.weekday()]

        expected = expected_dow(etype, name)
        expected_str = fmt_dow_set(expected)

        # 校验
        flags = []
        if dt.weekday() >= 5 and etype not in ("geo", "monitor"):
            flags.append("🔴周末")
        elif expected is not None and dt.weekday() not in expected:
            flags.append(f"⚠️DOW异常(期望{expected_str})")
        status = " ".join(flags) if flags else "✅"

        et_date = dt.strftime("%m-%d %H:%M")
        bj_date = bj.strftime("%m-%d %H:%M")

        rows.append(
            f"| {name[:30]} | {et_date} | {et_dow_cn}({et_dow_en}) "
            f"| {bj_date} | {bj_dow_cn} | {expected_str} | {status} |"
        )

    return "\n".join(rows)


def make_et_iso(year: int, month: int, day: int, hour: int, minute: int = 0) -> str:
    """构造带正确 DST 偏移的美东 ISO 字符串 (写入日历用).

    传入的是 **美东墙上钟点**, 不是北京时间, 也不是 UTC。
    """
    from gold_miner.data.calendar import _et_offset  # local import 避免环依赖初始化问题

    naive = datetime(year, month, day, hour, minute)
    return naive.replace(tzinfo=_et_offset(naive)).isoformat()
