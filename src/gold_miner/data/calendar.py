"""事件日历 — 美联储决议、CPI、PPI、非农等重要事件.

事件数据存储于 data/calendar_events.jsonl，每条一行 JSON。
代码只负责加载/查询/追加，不包含硬编码日期。

时区约定:
  - 存储: scheduled_at 为 ISO 8601 带时区偏移的美东时间
    例: 2026-07-14T08:30:00-04:00 (EDT) 或 2026-01-13T08:30:00-05:00 (EST)
  - 旧格式(无时区): 视为美东时间，自动检测夏令时补充偏移
  - 展示: 统一通过 beijing_time 属性转换为北京时间 (UTC+8)

数据来源:
  - BLS (劳工统计局): CPI/PPI/NFP 官方发布日程
    https://www.bls.gov/schedules/
  - BEA (经济分析局): PCE 官方发布日程
  - ISM (供应链管理协会): PMI 官方发布日程
  - Federal Reserve: FOMC 会议日程
    https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
"""

from __future__ import annotations

import calendar as _calendar_mod
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from gold_miner.compat import StrEnum
from pathlib import Path
from typing import Any

from loguru import logger

# 北京时间 = UTC+8
_BEIJING_TZ = timezone(timedelta(hours=8))


def _is_us_dst(dt: datetime) -> bool:
    """美东夏令时 (EDT, UTC-4): 3月第二个周日 – 11月第一个周日."""
    # 3月第二个周日
    mar_first = datetime(dt.year, 3, 1)
    mar_second_sun = mar_first + timedelta(
        days=(6 - mar_first.weekday() + 7) % 7 + 7
    )
    # 11月第一个周日
    nov_first = datetime(dt.year, 11, 1)
    nov_first_sun = nov_first + timedelta(
        days=(6 - nov_first.weekday() + 7) % 7
    )
    return mar_second_sun <= dt.replace(tzinfo=None) < nov_first_sun


def _et_offset(dt: datetime) -> timezone:
    """返回美东时区: EDT(UTC-4) 或 EST(UTC-5)."""
    return timezone(timedelta(hours=-4 if _is_us_dst(dt) else -5))


def _parse_et_datetime(iso_str: str) -> datetime:
    """解析 ISO 字符串为美东时间 aware datetime.

    兼容两种格式:
      - 带时区: 2026-07-14T08:30:00-04:00 → 直接解析
      - 无时区(旧格式): 2026-07-14T08:30:00 → 视为美东时间, 自动检测 DST
    """
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        # 旧格式: 无时区 → 自动附加美东时区
        dt = dt.replace(tzinfo=_et_offset(dt))
    return dt


def _fmt_et_iso(dt: datetime) -> str:
    """将 aware datetime 格式化为美东时间 ISO 字符串 (含时区偏移)."""
    et_tz = _et_offset(dt)
    et_dt = dt.astimezone(et_tz)
    return et_dt.isoformat()


def _to_beijing(et_dt: datetime) -> datetime:
    """美东时间 → 北京时间."""
    if et_dt.tzinfo is None:
        et_dt = et_dt.replace(tzinfo=_et_offset(et_dt))
    return et_dt.astimezone(_BEIJING_TZ)


def _fmt_beijing(et_dt: datetime) -> str:
    """美东时间 → 北京时间字符串, 如 '07-14 20:30 (周二)'."""
    bj = _to_beijing(et_dt)
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return f"{bj.strftime('%m-%d %H:%M')} ({weekdays[bj.weekday()]})"


class EventImpact(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EventType(StrEnum):
    FED_RATE = "fed_rate"
    CPI = "cpi"
    PPI = "ppi"
    PCE = "pce"
    NFP = "nfp"
    PMI = "pmi"
    FOMC_MINUTES = "fomc_minutes"
    PMI_MARKIT = "pmi_markit"
    ECB = "ecb"
    BOE = "boe"
    GEO_POLITICAL = "geo"
    GOLD_RESERVE = "gold_reserve"
    FED_SPEECH = "fed_speech"
    MONITOR = "monitor"  # 持续性观测事件，带有触发条件和检查周期


@dataclass
class CalendarEvent:
    name: str
    event_type: EventType
    scheduled_at: datetime
    impact: EventImpact
    actual: str | None = None
    forecast: str | None = None
    previous: str | None = None
    source: str = ""
    description: str = ""
    # monitor 事件专用字段
    status: str | None = None              # "active" | "triggered" | "expired"
    trigger_condition: str | None = None   # 触发条件（自然语言描述，AI 在分析时评估）
    check_frequency: str | None = None     # "on_analysis" | "daily" | "weekly"
    action_on_trigger: str | None = None   # 条件触发后的建议动作
    triggered_at: str | None = None        # 触发时间 ISO 格式
    trigger_result: str | None = None      # 触发时的实际结果
    parent_analysis: str | None = None     # 创建该 monitor 的分析 session id
    expires_at: str | None = None          # 过期时间 ISO 格式

    @property
    def is_monitor(self) -> bool:
        return self.event_type == EventType.MONITOR

    @property
    def is_active_monitor(self) -> bool:
        return self.is_monitor and self.status == "active"

    @property
    def beijing_time(self) -> datetime:
        """scheduled_at 对应的北京时间 (aware datetime)."""
        return _to_beijing(self.scheduled_at)

    @property
    def beijing_time_str(self) -> str:
        """北京时间格式化字符串, 如 '07-14 20:30 (周二)'."""
        return _fmt_beijing(self.scheduled_at)

    @property
    def date(self) -> date:
        """scheduled_at 的日期部分 (date 对象)."""
        return self.scheduled_at.date()

    def to_dict(self) -> dict[str, object]:
        """转为字典，便于表格展示与序列化."""
        return {
            "name": self.name,
            "event_type": self.event_type.value,
            "scheduled_at": self.scheduled_at.isoformat(),
            "beijing_time": self.beijing_time_str,
            "impact": self.impact.value,
            "actual": self.actual,
            "forecast": self.forecast,
            "previous": self.previous,
            "source": self.source,
            "description": self.description,
            "status": self.status,
            "trigger_condition": self.trigger_condition,
            "action_on_trigger": self.action_on_trigger,
            "expires_at": self.expires_at,
        }


# 项目根目录下的日历数据文件
_CALENDAR_PATH = Path(__file__).parents[3] / "data" / "calendar_events.jsonl"


class EventCalendar:
    """事件日历管理器.

    从 data/calendar_events.jsonl 加载已验证事件，
    回退到算法推算未覆盖年份。
    """

    def __init__(self, data_path: Path | None = None) -> None:
        self.events: list[CalendarEvent] = []
        self._data_path = data_path or _CALENDAR_PATH

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def load_fixed_calendar(self, year: int | None = None) -> list[CalendarEvent]:
        """从 JSONL 文件加载已知事件，叠加算法生成事件."""
        target = year or datetime.now(tz=timezone.utc).year
        jsonl_events = self._load_from_jsonl(year=target)

        # JSONL 无该年份数据时回退到推算
        if not jsonl_events:
            logger.warning(f"JSONL 无 {target} 年日历数据，使用推算")
            events = self._load_approximate_calendar(target)
        else:
            events = list(jsonl_events)
            # 收集 JSONL 中已有的 NFP 月份，避免 _generate_nfp_events 生成重复事件
            nfp_months_in_jsonl = {
                e.scheduled_at.month
                for e in jsonl_events
                if e.event_type == EventType.NFP
            }
            events.extend(self._generate_nfp_events(
                target, skip_months=nfp_months_in_jsonl,
            ))

        self.events.extend(events)
        self.events.sort(key=lambda e: e.scheduled_at)
        return events

    def check_event_outcome(self, event_name: str, actual: str, forecast: str) -> None:
        """更新事件的实际结果."""
        for e in self.events:
            if e.name == event_name and e.actual is None:
                e.actual = actual
                e.forecast = e.forecast or forecast
                return

    def get_upcoming(
        self,
        days: int = 7,
        min_impact: EventImpact = EventImpact.MEDIUM,
        reference_time: datetime | None = None,
    ) -> list[CalendarEvent]:
        now = (reference_time or datetime.now(tz=timezone.utc))
        cutoff = now + timedelta(days=days)
        impact_order = {EventImpact.HIGH: 3, EventImpact.MEDIUM: 2, EventImpact.LOW: 1}
        min_level = impact_order.get(min_impact, 1)
        return [
            e for e in self.events
            if now <= e.scheduled_at <= cutoff
            and impact_order.get(e.impact, 0) >= min_level
        ]

    def get_today(self) -> list[CalendarEvent]:
        today = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today + timedelta(days=1)
        return [e for e in self.events if today <= e.scheduled_at < tomorrow]

    def get_recently_published_without_result(
        self,
        lookback_days: int = 7,
        reference_time: datetime | None = None,
    ) -> list[CalendarEvent]:
        """返回最近已发布但尚未记录实际结果的事件.

        用于分析前自动拉取事件结果：scheduled_at 已过但 actual 为空的事件，
        按时间倒序排列。
        """
        now = reference_time or datetime.now(tz=timezone.utc)
        cutoff = now - timedelta(days=lookback_days)
        candidates = [
            e for e in self.events
            if cutoff <= e.scheduled_at <= now and e.actual is None
        ]
        candidates.sort(key=lambda e: e.scheduled_at, reverse=True)
        return candidates

    def get_recent_events_with_results(
        self,
        lookback_days: int = 7,
        reference_time: datetime | None = None,
    ) -> list[CalendarEvent]:
        """返回最近已发布且已记录实际结果的事件.

        与 get_recently_published_without_result 互补：
        用于事件结果同步后，将有 actual 值的事件注入到信号管线中，
        生成「预期 vs 实际偏差」信号。

        只返回非 monitor 类型的普通事件（monitor 由独立机制处理）。
        """
        now = reference_time or datetime.now(tz=timezone.utc)
        cutoff = now - timedelta(days=lookback_days)
        candidates = [
            e for e in self.events
            if cutoff <= e.scheduled_at <= now
            and e.actual is not None
            and e.event_type != EventType.MONITOR
        ]
        candidates.sort(key=lambda e: e.scheduled_at, reverse=True)
        return candidates

    def update_event_result(
        self,
        name: str,
        scheduled_at: datetime,
        actual: str,
        forecast: str | None = None,
        previous: str | None = None,
    ) -> bool:
        """更新事件的实际结果（内存 + 重写 JSONL 文件）.

        Returns:
            True 如果找到并更新了事件，False 如果未找到匹配事件.
        """
        # 确保 scheduled_at 是 aware datetime（兼容旧调用方传入 naive）
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=_et_offset(scheduled_at))

        updated = False
        for e in self.events:
            if e.name == name and e.scheduled_at == scheduled_at:
                e.actual = actual
                if forecast is not None:
                    e.forecast = forecast
                if previous is not None:
                    e.previous = previous
                updated = True

        if updated:
            self._rewrite_jsonl()
        return updated

    def add_event(self, event: CalendarEvent) -> None:
        """添加事件（内存+追加到 JSONL 文件）."""
        self.events.append(event)
        self.events.sort(key=lambda e: e.scheduled_at)
        try:
            with open(self._data_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(self._to_dict(event), ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning(f"写入日历文件失败: {e}")

    # ------------------------------------------------------------------
    # Monitor 事件管理
    # ------------------------------------------------------------------

    def get_active_monitors(self) -> list[CalendarEvent]:
        """返回所有 status="active" 的 monitor 事件."""
        return [
            e for e in self.events
            if e.event_type == EventType.MONITOR and e.status == "active"
        ]

    def close_monitor(
        self,
        name: str,
        result: str,
        new_status: str = "triggered",
    ) -> bool:
        """关闭 monitor 事件，记录触发结果（内存 + 重写 JSONL）.

        Args:
            name: monitor 事件名称（精确匹配）
            result: 触发时的实际结果描述
            new_status: 新状态，"triggered" 或 "expired"

        Returns:
            True 如果找到并更新了事件
        """
        updated = False
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        for e in self.events:
            if e.name == name and e.is_active_monitor:
                e.status = new_status
                e.trigger_result = result
                e.triggered_at = now_iso
                updated = True
                break

        if updated:
            self._rewrite_jsonl()
        return updated

    def get_recently_triggered_monitors(
        self,
        lookback_days: int = 7,
        reference_time: datetime | None = None,
    ) -> list[CalendarEvent]:
        """返回最近触发的 monitor 事件（供信号管线消费）.

        第〇步调用 close_monitor() 将 monitor 标记为 triggered 后，
        本方法使 Step 2 的 MonitorSignalGenerator 能自动读取触发结果，
        生成方向信号。

        Args:
            lookback_days: 回溯天数
            reference_time: 参考时间，默认当前时间

        Returns:
            最近触发的 monitor 事件列表
        """
        now = reference_time or datetime.now(tz=timezone.utc)
        cutoff = now - timedelta(days=lookback_days)
        candidates = [
            e for e in self.events
            if e.event_type == EventType.MONITOR and e.status == "triggered"
        ]
        result: list[CalendarEvent] = []
        for e in candidates:
            if e.triggered_at is None:
                continue
            try:
                triggered_dt = datetime.fromisoformat(e.triggered_at)
                if triggered_dt >= cutoff:
                    result.append(e)
            except (ValueError, TypeError):
                # 无法解析日期时保守纳入
                result.append(e)
        result.sort(key=lambda e: e.triggered_at or "", reverse=True)
        return result

    def _rewrite_jsonl(self) -> None:
        """更新 JSONL 文件中的已有事件（保留未改动行原样）.

        只更新 self.events 中在 JSONL 文件里已有对应条目的事件。
        不新增行（由 add_event() 负责追加），不删除行。
        避免将 load_fixed_calendar() 程序化生成的事件写入文件。
        """
        try:
            with open(self._data_path, encoding="utf-8") as f:
                current_lines = [line.rstrip("\n") for line in f if line.strip()]
        except (OSError, FileNotFoundError):
            return

        # 构建 (name, scheduled_at_naive) → 原行 的映射
        # 用无时区的本地时间做 key，兼容新旧格式
        def _naive_key(iso_str: str) -> str:
            """从 ISO 字符串提取无时区的本地时间部分."""
            dt = _parse_et_datetime(iso_str)
            return dt.strftime("%Y-%m-%dT%H:%M:%S")

        line_map: dict[tuple[str, str], str] = {}
        for line in current_lines:
            try:
                data = json.loads(line)
                key = (data["name"], _naive_key(data["scheduled_at"]))
                line_map[key] = line
            except (json.JSONDecodeError, KeyError):
                continue

        # 构建 (name, scheduled_at_naive) → 更新后的 JSON 的映射
        updated_map: dict[tuple[str, str], str] = {}
        for event in self.events:
            key = (event.name, event.scheduled_at.strftime("%Y-%m-%dT%H:%M:%S"))
            if key in line_map:
                updated_map[key] = json.dumps(
                    self._to_dict(event), ensure_ascii=False
                )

        if not updated_map:
            return  # 没有需要更新的行

        # 逐行重写：有更新的用新版，无更新的保留原样
        try:
            with open(self._data_path, "w", encoding="utf-8") as f:
                for line in current_lines:
                    try:
                        data = json.loads(line)
                        key = (data["name"], data["scheduled_at"])
                        f.write(updated_map.get(key, line) + "\n")
                    except json.JSONDecodeError:
                        f.write(line + "\n")
        except OSError as e:
            logger.warning(f"重写日历文件失败: {e}")

    # ------------------------------------------------------------------
    # JSONL 读写
    # ------------------------------------------------------------------

    def _load_from_jsonl(self, year: int | None = None) -> list[CalendarEvent]:
        """从 JSONL 文件加载事件，可选按年份过滤."""
        events: list[CalendarEvent] = []
        if not self._data_path.exists():
            logger.warning(f"日历数据文件不存在: {self._data_path}")
            return events

        try:
            for line in self._data_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event = self._from_dict(obj)
                if year is None or event.scheduled_at.year == year:
                    events.append(event)
        except OSError as e:
            logger.warning(f"读取日历文件失败: {e}")

        return events

    @staticmethod
    def _from_dict(obj: dict[str, Any]) -> CalendarEvent:
        return CalendarEvent(
            name=obj.get("name", ""),
            event_type=EventType(obj.get("event_type", "")),
            scheduled_at=_parse_et_datetime(obj["scheduled_at"]),
            impact=EventImpact(obj.get("impact", "medium")),
            actual=obj.get("actual"),
            forecast=obj.get("forecast"),
            previous=obj.get("previous"),
            source=obj.get("source", ""),
            description=obj.get("description", ""),
            # monitor 字段
            status=obj.get("status"),
            trigger_condition=obj.get("trigger_condition"),
            check_frequency=obj.get("check_frequency"),
            action_on_trigger=obj.get("action_on_trigger"),
            triggered_at=obj.get("triggered_at"),
            trigger_result=obj.get("trigger_result"),
            parent_analysis=obj.get("parent_analysis"),
            expires_at=obj.get("expires_at"),
        )

    _MONITOR_KEYS = (
        "status", "trigger_condition", "check_frequency",
        "action_on_trigger", "triggered_at", "trigger_result",
        "parent_analysis", "expires_at",
    )

    @staticmethod
    def _to_dict(event: CalendarEvent) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": event.name,
            "event_type": event.event_type.value,
            "scheduled_at": _fmt_et_iso(event.scheduled_at),
            "impact": event.impact.value,
            "source": event.source,
            "description": event.description,
        }
        if event.actual is not None:
            d["actual"] = event.actual
        if event.forecast is not None:
            d["forecast"] = event.forecast
        if event.previous is not None:
            d["previous"] = event.previous
        # monitor 字段 — 仅非 None 时写入
        for key in EventCalendar._MONITOR_KEYS:
            val = getattr(event, key, None)
            if val is not None:
                d[key] = val
        return d

    # ------------------------------------------------------------------
    # 算法生成事件 (不依赖外部数据)
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_nfp_events(
        year: int,
        skip_months: set[int] | None = None,
    ) -> list[CalendarEvent]:
        """非农就业 — 每月第一个周五 (算法固定).

        Args:
            year: 目标年份
            skip_months: 要跳过的月份集合（JSONL 中已有该月 NFP 事件时传入，
                         避免生成重复事件导致 actual 被覆盖为空）
        """
        skip = skip_months or set()
        events: list[CalendarEvent] = []
        for month in range(1, 13):
            if month in skip:
                continue
            first_day = datetime(year, month, 1)
            days_until_fri = (4 - first_day.weekday()) % 7
            nfp_day = 1 + days_until_fri
            dt = datetime(year, month, nfp_day, 8, 30)
            dt = dt.replace(tzinfo=_et_offset(dt))
            events.append(CalendarEvent(
                name="非农就业",
                event_type=EventType.NFP,
                scheduled_at=dt,
                impact=EventImpact.HIGH,
                source="BLS",
                description="美国非农就业数据",
            ))
        return events

    # ------------------------------------------------------------------
    # 回退：无数据源的动态推算
    # ------------------------------------------------------------------

    @staticmethod
    def _load_approximate_calendar(year: int) -> list[CalendarEvent]:
        """从未知年份推算事件日期 (精确度较低).

        用于 JSONL 无覆盖的年份，基于历史规律推算。
        """
        events: list[CalendarEvent] = []

        def _mk_dt(month: int, day: int, hour: int, minute: int) -> datetime:
            dt = datetime(year, month, day, hour, minute)
            return dt.replace(tzinfo=_et_offset(dt))

        # FOMC: 全年8次，约6周一次，大致在每月中旬
        for month in (1, 3, 5, 6, 7, 9, 11, 12):
            events.append(CalendarEvent(
                name="FOMC利率决议",
                event_type=EventType.FED_RATE,
                scheduled_at=_mk_dt(month, 12, 14, 0),
                impact=EventImpact.HIGH,
                source="Federal Reserve (approx.)",
                description="美联储联邦公开市场委员会利率决议（推算日期）",
            ))

        # CPI: 每月约10-15日
        for month in range(1, 13):
            events.append(CalendarEvent(
                name="美国CPI",
                event_type=EventType.CPI,
                scheduled_at=_mk_dt(month, 13, 8, 30),
                impact=EventImpact.HIGH,
                source="BLS (approx.)",
                description="美国消费者物价指数（推算）",
            ))

        # PPI: 每月约11-15日
        for month in range(1, 13):
            events.append(CalendarEvent(
                name="美国PPI",
                event_type=EventType.PPI,
                scheduled_at=_mk_dt(month, 14, 8, 30),
                impact=EventImpact.HIGH,
                source="BLS (approx.)",
                description="美国生产者价格指数（推算）",
            ))

        # PCE: 每月约25-31日
        for month in range(1, 13):
            events.append(CalendarEvent(
                name="核心PCE物价指数",
                event_type=EventType.PCE,
                scheduled_at=_mk_dt(month, 28, 8, 30),
                impact=EventImpact.HIGH,
                source="BEA (approx.)",
                description="核心个人消费支出物价指数（推算）",
            ))

        # NFP
        events.extend(EventCalendar._generate_nfp_events(year))

        # PMI
        for month in range(1, 13):
            first_day = datetime(year, month, 1)
            days_until_fri = (4 - first_day.weekday()) % 7
            pmi_day = min(1 + days_until_fri + 7, 28)
            events.append(CalendarEvent(
                name="ISM制造业PMI",
                event_type=EventType.PMI,
                scheduled_at=_mk_dt(month, pmi_day, 10, 0),
                impact=EventImpact.HIGH,
                source="S&P Global / ISM (approx.)",
                description="制造业景气度指标（推算日期）",
            ))
            events.append(CalendarEvent(
                name="ISM服务业PMI",
                event_type=EventType.PMI,
                scheduled_at=_mk_dt(month, min(pmi_day + 1, 28), 10, 0),
                impact=EventImpact.HIGH,
                source="S&P Global / ISM (approx.)",
                description="服务业景气度指标（推算日期）",
            ))

        return events
