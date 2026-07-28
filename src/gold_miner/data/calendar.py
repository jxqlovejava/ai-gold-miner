"""事件日历 — 美联储决议、CPI、PPI、非农等重要事件.

事件数据存储于 data/calendar_events.jsonl，每条一行 JSON。
代码只负责加载/查询/追加，不包含硬编码日期。

时区约定:
  - 存储: scheduled_at 为 ISO 8601 带时区偏移的 **美东墙上钟点**
    例: 2026-07-14T08:30:00-04:00 (EDT) 或 2026-01-13T08:30:00-05:00 (EST)
  - 旧格式(无时区): 视为美东时间，自动检测夏令时补充偏移
  - 展示: 统一通过 beijing_time / dual_clock 转为北京时间 (UTC+8)
  - 禁止双重换算: 不可先换成北京钟点再把该小时数写回 scheduled_at
    (事故: 听证 10:00 ET 误存 22:00 ET → 显示成北京次日 10:00)
  - 写入校验: add_event 调用 calendar_time_rules; 分析前跑
    scripts/validate_calendar_dates.py

数据来源:
  - BLS (劳工统计局): CPI/PPI/NFP 官方发布日程
    https://www.bls.gov/schedules/
  - BEA (经济分析局): PCE 官方发布日程
  - ISM (供应链管理协会): PMI 官方发布日程
  - Federal Reserve: FOMC 会议日程
    https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from gold_miner.compat import StrEnum

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
    EXTREME = "extreme"


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


# 快速演变事件类型 — 这些类型的事件可能在发布后数小时/数天内发生逆转或更新，
# 而非像 CPI/PPI/NFP 那样一次性发布即为最终结果。
# 包含这些类型的事件在 analysis 前需重新验证 actual 是否仍为最新状态。
FAST_EVOLVING_TYPES: frozenset[str] = frozenset({
    "geo",              # 地缘冲突 — 24h 内可逆转 (cf. 7/13 Hormuz 费→7/14 撤销)
    "policy_shift",     # 政策突变 — 多日演变
    "trade_war",        # 贸易战 — 逐轮升级/缓和
    "fed_emergency",    # 联储紧急声明 — 后续澄清/修正常见
    "monitor",          # monitor 本身追踪的事件可能演变
})

# 默认过时阈值 (小时) — 超过此时间未更新 actual 则标记为需重新验证
_STALENESS_DEFAULT_HOURS: dict[str, int] = {
    "geo": 12,           # 地缘最激进: 12h 内可能有新进展
    "policy_shift": 24,
    "trade_war": 24,
    "fed_emergency": 24,
    "monitor": 48,       # monitor 检查频率较低
}


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
    # --- 过时检测 (staleness detection) ---
    actual_updated_at: str | None = None    # 上次更新 actual 的 ISO 时间戳
    actual_history: str | None = None       # JSON 数组: [{"value":"...","updated_at":"...","superseded_at":"..."}]
    source_verified_at: str | None = None   # 上次来源验证时间 ISO
    staleness_check_hours: int | None = None # 每个事件可覆盖默认检查间隔

    @property
    def is_monitor(self) -> bool:
        return self.event_type == EventType.MONITOR

    @property
    def is_active_monitor(self) -> bool:
        return self.is_monitor and self.status == "active"

    @property
    def needs_reverify(self) -> bool:
        """快速演变事件是否可能需要重新验证 actual 数据.

        仅对 FAST_EVOLVING_TYPES 中的事件类型生效。
        返回 True 当:
        - actual 不为空 (已有值才可能过时)
        - 事件类型在 FAST_EVOLVING_TYPES 中
        - actual_updated_at 不存在 (旧事件, 保守假定过时)
          或距今超过 staleness_check_hours / 类型默认阈值
        """
        if self.actual is None:
            return False
        if self.event_type.value not in FAST_EVOLVING_TYPES:
            return False

        check_hours = (
            self.staleness_check_hours
            or _STALENESS_DEFAULT_HOURS.get(self.event_type.value, 24)
        )

        if self.actual_updated_at is None:
            # 旧事件: actual 已设但无时间戳 → 保守假定过时
            return True

        try:
            updated = datetime.fromisoformat(self.actual_updated_at)
            hours_since = (datetime.now(tz=UTC) - updated).total_seconds() / 3600
            return hours_since > check_hours
        except (ValueError, TypeError):
            return True  # 时间戳不可解析 → 保守

    @property
    def beijing_time(self) -> datetime:
        """scheduled_at 对应的北京时间 (aware datetime)."""
        return _to_beijing(self.scheduled_at)

    @property
    def beijing_time_str(self) -> str:
        """北京时间格式化字符串, 如 '07-14 20:30 (周二)'."""
        return _fmt_beijing(self.scheduled_at)

    @property
    def dual_clock_str(self) -> str:
        """ET | 北京 双列, 防只看北京误判."""
        from gold_miner.data.calendar_time_rules import dual_clock_str as _dual

        return _dual(self.scheduled_at)

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
            "dual_clock": self.dual_clock_str,
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

    def __init__(
        self,
        data_path: Path | None = None,
        *,
        autoload: bool = True,
    ) -> None:
        self.events: list[CalendarEvent] = []
        self._data_path = data_path or _CALENDAR_PATH
        # 已成功加载过的年份；避免 load_fixed_calendar 重复 extend 导致翻倍
        self._loaded_years: set[int] = set()
        if autoload:
            self.load_fixed_calendar()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def load_fixed_calendar(self, year: int | None = None) -> list[CalendarEvent]:
        """从 JSONL 文件加载已知事件，自动补全缺失的全球事件类别.

        加载策略（三层）：
        1. JSONL 精确事件（最高优先级，含 actual/forecast 等手动维护字段）
        2. 自动推算事件（仅补充 JSONL 中缺失的类别，避免重复）
        3. 幂等：同一 year 再次调用不重复 append

        自动补全覆盖：NFP/ECB/BOE/Flash PMI/UK CPI/德国ZEW/EU消费者信心
        """
        target = year or datetime.now(tz=UTC).year
        if target in self._loaded_years:
            return [e for e in self.events if e.scheduled_at.year == target]

        jsonl_events = self._load_from_jsonl(year=target)

        # JSONL 无该年份数据时：完全回退到推算
        if not jsonl_events:
            logger.warning(f"JSONL 无 {target} 年日历数据，使用推算")
            events = self._load_approximate_calendar(target)
        else:
            events = list(jsonl_events)
            # JSONL 有数据时：用自动生成器补全 JSONL 中缺失的事件类别
            self._supplement_missing_categories(events, target)

        self.events.extend(events)
        self.events.sort(key=lambda e: e.scheduled_at)
        self._loaded_years.add(target)
        return events

    @staticmethod
    def _supplement_missing_categories(
        events: list[CalendarEvent],
        year: int,
    ) -> None:
        """对已有 JSONL 数据的年份，自动补全缺失的全球事件类别.

        检查每个生成器覆盖的月份是否在 events 中已有对应事件，
        缺失的月份由算法自动生成并追加到 events 列表中。
        此方法确保即使 JSONL 只维护了美国事件，欧洲/全球事件也不会遗漏。
        """
        # 收集已有事件的 (event_type, month) 集合
        existing: dict[str, set[int]] = {}
        for e in events:
            existing.setdefault(e.event_type.value, set()).add(e.scheduled_at.month)

        def _missing_months(etype: str, expected: set[int]) -> set[int]:
            return expected - existing.get(etype, set())

        def _supplement_by_name(
            evs: list[CalendarEvent],
            gen,
            name_prefix: str,
            yr: int,
        ) -> None:
            """按事件名称补全缺失月份，避免同月同名重复."""
            _ = name_prefix  # 保留参数用于可读性
            existing_names = {
                (e.name, e.scheduled_at.month)
                for e in evs
            }
            for candidate in gen(yr):
                key = (candidate.name, candidate.scheduled_at.month)
                if key not in existing_names:
                    evs.append(candidate)

        # ---- NFP (每月第一个周五) ----
        nfp_missing = _missing_months("nfp", set(range(1, 13)))
        if nfp_missing:
            nfp_events = EventCalendar._generate_nfp_events(year, skip_months=None)
            for e in nfp_events:
                if e.scheduled_at.month in nfp_missing:
                    events.append(e)

        # ---- ECB (每6周，全年约8次) ----
        ecb_missing = _missing_months("ecb", {1, 3, 4, 6, 7, 9, 10, 12})
        if ecb_missing:
            ecb_events = EventCalendar._generate_ecb_events(year)
            for e in ecb_events:
                if e.scheduled_at.month in ecb_missing:
                    events.append(e)

        # ---- BOE (每6周，全年约8次) ----
        boe_missing = _missing_months("boe", {2, 3, 5, 6, 8, 9, 11, 12})
        if boe_missing:
            boe_events = EventCalendar._generate_boe_events(year)
            for e in boe_events:
                if e.scheduled_at.month in boe_missing:
                    events.append(e)

        # ---- 全球 Flash PMI (每月24日) ----
        flash_pmi_missing = _missing_months("pmi", set(range(1, 13)))
        if flash_pmi_missing:
            # 仅补充"全球Flash PMI"类事件（通过名称区分，不影响ISM PMI）
            flash_events = EventCalendar._generate_global_flash_pmi_events(year)
            for e in flash_events:
                if e.scheduled_at.month in flash_pmi_missing:
                    # 检查是否有同月同名事件（避免重复）
                    dup = any(
                        existing.name == e.name
                        and existing.scheduled_at.month == e.scheduled_at.month
                        for existing in events
                    )
                    if not dup:
                        events.append(e)

        # ---- UK CPI (每月中旬) ----
        uk_cpi_missing = _missing_months("cpi", set(range(1, 13)))
        if uk_cpi_missing:
            uk_cpi_events = EventCalendar._generate_uk_cpi_events(year)
            for e in uk_cpi_events:
                if e.scheduled_at.month in uk_cpi_missing:
                    dup = any(
                        existing.name == e.name
                        and existing.scheduled_at.month == e.scheduled_at.month
                        for existing in events
                    )
                    if not dup:
                        events.append(e)

        # ---- 德国 ZEW (每月中旬) ----
        zew_missing = _missing_months("pmi", set(range(1, 13)))
        if zew_missing:
            zew_events = EventCalendar._generate_german_zew_events(year)
            for e in zew_events:
                if e.scheduled_at.month in zew_missing:
                    dup = any(
                        existing.name == e.name
                        and existing.scheduled_at.month == e.scheduled_at.month
                        for existing in events
                    )
                    if not dup:
                        events.append(e)

        # ---- EU 消费者信心 (每月22日) ----
        eu_cc_missing = _missing_months("pmi", set(range(1, 13)))
        if eu_cc_missing:
            eu_cc_events = EventCalendar._generate_eu_consumer_confidence(year)
            for e in eu_cc_events:
                if e.scheduled_at.month in eu_cc_missing:
                    dup = any(
                        existing.name == e.name
                        and existing.scheduled_at.month == e.scheduled_at.month
                        for existing in events
                    )
                    if not dup:
                        events.append(e)

        # ---- 初请失业金 (每周四，周频事件) ----
        existing_jobless_dates = {
            e.scheduled_at.date()
            for e in events
            if "初请" in e.name
        }
        all_jobless = EventCalendar._generate_jobless_claims_events(year)
        for e in all_jobless:
            if e.scheduled_at.date() not in existing_jobless_dates:
                events.append(e)

        # ---- 美国谘商会消费者信心 (每月最后一个周二, MEDIUM) ----
        _supplement_by_name(events, EventCalendar._generate_us_consumer_confidence_events,
                            "美国谘商会消费者信心指数", year)

        # ---- 里奇蒙德联储制造业 (每月第4个周二, MEDIUM) ----
        _supplement_by_name(events, EventCalendar._generate_richmond_fed_manufacturing_events,
                            "美国里奇蒙德联储制造业指数", year)

        # ---- FHFA房价指数 (每月最后一个周二, MEDIUM) ----
        _supplement_by_name(events, EventCalendar._generate_housing_price_indices_events,
                            "美国FHFA房价指数月率", year)

        # ---- S&P/CS 20城房价指数 (每月最后一个周二, MEDIUM) ----
        _supplement_by_name(events, EventCalendar._generate_housing_price_indices_events,
                            "美国S&P/CS20座大城市房价指数年率", year)

        # ---- 商品贸易帐初值 (每月约26日, MEDIUM) ----
        _supplement_by_name(events, EventCalendar._generate_goods_trade_balance_events,
                            "美国商品贸易帐(初值)", year)

        # ---- 密歇根消费者信心初值 (每月第2个周五, MEDIUM) ----
        _supplement_by_name(events, EventCalendar._generate_michigan_sentiment_events,
                            "美国密歇根大学消费者信心指数初值", year)

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
        now = (reference_time or datetime.now(tz=UTC))
        cutoff = now + timedelta(days=days)
        impact_order = {EventImpact.HIGH: 3, EventImpact.MEDIUM: 2, EventImpact.LOW: 1}
        min_level = impact_order.get(min_impact, 1)
        return [
            e for e in self.events
            if now <= e.scheduled_at <= cutoff
            and impact_order.get(e.impact, 0) >= min_level
        ]

    def get_today(self) -> list[CalendarEvent]:
        today = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)
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
        now = reference_time or datetime.now(tz=UTC)
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
        now = reference_time or datetime.now(tz=UTC)
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
        source_verified: bool = True,
    ) -> bool:
        """更新事件的实际结果（内存 + 重写 JSONL 文件）.

        对 fast-evolving 事件，旧值会被追加到 actual_history 而非丢弃。
        actual_updated_at 和 source_verified_at 自动更新。

        Args:
            name: 事件名称
            scheduled_at: 事件预定时间
            actual: 新的实际结果
            forecast: 预期值
            previous: 前值
            source_verified: 是否经过来源验证 (默认 True)

        Returns:
            True 如果找到并更新了事件，False 如果未找到匹配事件.
        """
        # 确保 scheduled_at 是 aware datetime（兼容旧调用方传入 naive）
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=_et_offset(scheduled_at))

        now_iso = datetime.now(tz=UTC).isoformat()
        updated = False

        for e in self.events:
            if e.name == name and e.scheduled_at == scheduled_at:
                # 保存历史: 对 fast-evolving 事件，旧值推入 actual_history
                if (
                    e.actual is not None
                    and e.actual != actual
                    and e.event_type.value in FAST_EVOLVING_TYPES
                ):
                    history: list[dict[str, str]] = []
                    if e.actual_history:
                        try:
                            history = json.loads(e.actual_history)
                        except (json.JSONDecodeError, TypeError):
                            history = []
                    history.append({
                        "value": e.actual,
                        "updated_at": e.actual_updated_at or "unknown",
                        "superseded_at": now_iso,
                    })
                    e.actual_history = json.dumps(history, ensure_ascii=False)

                e.actual = actual
                e.actual_updated_at = now_iso
                if forecast is not None:
                    e.forecast = forecast
                if previous is not None:
                    e.previous = previous
                if source_verified:
                    e.source_verified_at = now_iso
                updated = True

        if updated:
            self._rewrite_jsonl()
        return updated

    def add_event(self, event: CalendarEvent, *, force: bool = False) -> None:
        """添加事件（内存+追加到 JSONL 文件）.

        写入前跑三重校验 (calendar_time_rules):
          1. DOW (星期) — 防止「周三初请失业金」类
          2. 钟点 — 防双重换算 (听证晚间 ET 等)
          3. 数据发布时间窗口

        默认 raise ValueError; force=True 仅用于历史回填。
        """
        from gold_miner.data.calendar_time_rules import (
            check_event_clock,
            check_event_dow,
            dual_clock_str,
        )

        all_errors: list[str] = []
        dual = (
            dual_clock_str(event.scheduled_at)
            if event.scheduled_at.tzinfo
            else str(event.scheduled_at)
        )

        # 1. DOW 校验
        dow_findings = check_event_dow(
            name=event.name,
            event_type=event.event_type.value,
            scheduled_at=event.scheduled_at,
        )
        for f in dow_findings:
            if f.severity == "error":
                all_errors.append(f.message)
            else:
                logger.warning(f"[日历DOW] {f.message}")

        # 2. 钟点校验
        clock_findings = check_event_clock(
            name=event.name,
            event_type=event.event_type.value,
            scheduled_at=event.scheduled_at,
        )
        for f in clock_findings:
            if f.severity == "error":
                all_errors.append(f.message)
            elif f.severity == "warning":
                logger.warning(f"[日历钟点] {f.message}")

        if all_errors and not force:
            detail = "; ".join(all_errors)
            raise ValueError(
                f"拒绝写入日历 (校验失败): {detail} | {dual}"
            )
        if all_errors and force:
            for msg in all_errors:
                logger.error(f"[日历 force 写入] {msg}")

        self.events.append(event)
        self.events.sort(key=lambda e: e.scheduled_at)
        try:
            with open(self._data_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(self._to_dict(event), ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning(f"写入日历文件失败: {e}")

    # ------------------------------------------------------------------
    # DOW 参考表 (分析前校验用)
    # ------------------------------------------------------------------

    def dow_reference_table(self, days_ahead: int = 30) -> str:
        """生成未来事件的 DOW 参考表 (Markdown), 供分析报告引用.

        每行包含 ET/BJ 双列日期和星期, 异常行标注 ⚠️。
        分析报告中涉及事件日期时必须引用此表校验。
        """
        from gold_miner.data.calendar_time_rules import generate_dow_reference_table

        events_dict = [self._to_dict(e) for e in self.events]
        return generate_dow_reference_table(events_dict, days_ahead=days_ahead)

    def pre_analysis_validate(self) -> tuple[list[str], list[str]]:
        """分析前校验 — 返回 (errors, warnings).

        此方法应在任何分析 pipeline 运行前调用。
        若 errors 非空, 分析应中断直到修复。
        """
        from gold_miner.data.calendar_time_rules import (
            check_event_clock,
            check_event_dow,
        )

        errors: list[str] = []
        warnings: list[str] = []

        for e in self.events:
            # DOW 校验
            for f in check_event_dow(
                name=e.name,
                event_type=e.event_type.value,
                scheduled_at=e.scheduled_at,
            ):
                if f.severity == "error":
                    errors.append(f.message)
                else:
                    warnings.append(f.message)

            # 钟点校验
            for f in check_event_clock(
                name=e.name,
                event_type=e.event_type.value,
                scheduled_at=e.scheduled_at,
            ):
                if f.severity == "error":
                    errors.append(f.message)
                else:
                    warnings.append(f.message)

        return errors, warnings

    def validate_calendar_completeness(
        self,
        year: int | None = None,
        month: int | None = None,
    ) -> tuple[list[str], list[str]]:
        """分析前检查 — 验证当前月份是否有缺失的关键事件类别.

        返回 (missing_categories, warnings).

        检查清单覆盖美国、欧洲、英国、全球关键事件：
        - 美国: FOMC/CPI/PPI/PCE/NFP/ISM PMI/初请失业金
        - 欧洲: ECB/全球Flash PMI/德国ZEW/EU消费者信心
        - 英国: BOE/UK CPI

        若发现缺失的类别，分析流程应标记为不完整，但不应阻止分析——
        分析者应手动搜索这些事件并补充到报告中。

        Args:
            year: 目标年份，默认当前年
            month: 目标月份，默认当前月

        Returns:
            (missing_categories, warnings) — missing 是需要人肉确认并补充的类别列表
        """
        now = datetime.now(tz=UTC)
        target_year = year or now.year
        target_month = month or now.month

        # 定义必须覆盖的事件类别及其检查关键词
        required_categories: list[dict[str, Any]] = [
            # 美国
            {"region": "美国", "name": "FOMC/美联储利率决议", "keywords": ["FOMC", "fed_rate"], "severity": "critical"},
            {"region": "美国", "name": "CPI/通胀数据", "keywords": ["CPI", "cpi"], "severity": "critical"},
            {"region": "美国", "name": "非农就业", "keywords": ["非农", "NFP", "nfp"], "severity": "critical"},
            {"region": "美国", "name": "ISM PMI", "keywords": ["ISM", "pmi"], "severity": "high"},
            {"region": "美国", "name": "核心PCE", "keywords": ["PCE", "pce"], "severity": "high"},
            # 欧洲
            {"region": "欧洲", "name": "ECB利率决议", "keywords": ["ECB", "ecb"], "severity": "high"},
            {"region": "欧洲", "name": "全球Flash PMI", "keywords": ["Flash PMI", "flash_pmi"], "severity": "high"},
            {"region": "欧洲", "name": "德国ZEW经济情绪", "keywords": ["ZEW", "zew"], "severity": "medium"},
            {"region": "欧洲", "name": "欧盟消费者信心", "keywords": ["消费者信心", "consumer_confidence"], "severity": "low"},
            # 英国
            {"region": "英国", "name": "BOE利率决议", "keywords": ["BOE", "boe"], "severity": "medium"},
            {"region": "英国", "name": "UK CPI", "keywords": ["UK CPI", "uk_cpi"], "severity": "medium"},
        ]

        # 收集当前月份所有事件的 event_type 和 name
        month_events = [
            e for e in self.events
            if e.scheduled_at.year == target_year
            and e.scheduled_at.month == target_month
        ]
        month_event_types = {e.event_type.value for e in month_events}
        month_event_names = " ".join(e.name for e in month_events)

        missing: list[str] = []
        warnings: list[str] = []

        for cat in required_categories:
            # 检查 event_type 或 name 中是否包含关键词
            found = any(
                kw in month_event_types or kw.lower() in month_event_names.lower()
                for kw in cat["keywords"]
            )
            if not found:
                msg = (
                    f"[{cat['region']}] {cat['name']} — "
                    f"{target_year}年{target_month}月无此事件"
                )
                if cat["severity"] == "critical":
                    missing.append(f"🔴 {msg}")
                elif cat["severity"] == "high":
                    missing.append(f"🟡 {msg}")
                else:
                    warnings.append(f"⚪ {msg}")

        # 初请失业金：每周四发布，检查最近一期是否已同步
        has_jobless = any("初请" in e.name or "jobless" in e.name.lower()
                         for e in self.events)
        if has_jobless:
            # 只检查最近8天内已发布且非monitor的初请事件
            cutoff = datetime.now(tz=UTC) - timedelta(days=8)
            past_jobless_missing = [
                e for e in self.events
                if "初请" in e.name
                and not e.is_monitor
                and e.scheduled_at > cutoff
                and e.scheduled_at < datetime.now(tz=UTC)
                and not e.actual
            ]
            if past_jobless_missing:
                for e in past_jobless_missing[-3:]:  # 只报最近3期
                    missing.append(
                        f"🔴 [美国] 初请失业金 {e.scheduled_at.strftime('%m/%d')} — "
                        f"已发布但未同步实际结果，请搜索 DOL 官方发布并更新 calendar"
                    )
        else:
            missing.append(
                "🔴 [美国] 初请失业金人数 — 周度高频事件完全缺失，"
                "自动生成器无法覆盖（需滚动生成）。请手动添加本周事件。"
            )

        return missing, warnings

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
        now_iso = datetime.now(tz=UTC).isoformat()
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
        now = reference_time or datetime.now(tz=UTC)
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

    def get_events_needing_reverify(
        self,
        lookback_days: int = 7,
        reference_time: datetime | None = None,
    ) -> list[CalendarEvent]:
        """返回可能需要重新验证 actual 的 fast-evolving 事件。

        筛选条件:
        1. 事件类型在 FAST_EVOLVING_TYPES 中 (geo/policy_shift/trade_war/...)
        2. actual 不为空 (已有值才可能过时)
        3. needs_reverify property 返回 True

        第〇步工作流应在同步普通事件结果后调用此方法，
        将返回的事件逐一用时间约束搜索检查最新状态。

        Returns:
            按过时程度排序 (无时间戳 / 最久未更新的在前)
        """
        now = reference_time or datetime.now(tz=UTC)
        cutoff = now - timedelta(days=lookback_days)

        candidates = [
            e for e in self.events
            if e.scheduled_at >= cutoff
            and e.actual is not None
            and e.needs_reverify
        ]

        def _staleness_key(e: CalendarEvent) -> tuple[int, str]:
            if e.actual_updated_at is None:
                return (0, "")  # 无时间戳——最优先检查
            return (1, e.actual_updated_at)

        candidates.sort(key=_staleness_key)
        return candidates

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
            # 转为美东墙上钟点再取无时区字符串，与 line_map 的 key 保持一致
            et_dt = event.scheduled_at.astimezone(_et_offset(event.scheduled_at))
            key = (event.name, et_dt.strftime("%Y-%m-%dT%H:%M:%S"))
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
                        # ponytail: _naive_key strips tz, updated_map keys also strip tz
                        naive_key = (data["name"], _naive_key(data["scheduled_at"]))
                        f.write(updated_map.get(naive_key, line) + "\n")
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
            # staleness detection 字段
            actual_updated_at=obj.get("actual_updated_at"),
            actual_history=obj.get("actual_history"),
            source_verified_at=obj.get("source_verified_at"),
            staleness_check_hours=obj.get("staleness_check_hours"),
        )

    _MONITOR_KEYS = (
        "status", "trigger_condition", "check_frequency",
        "action_on_trigger", "triggered_at", "trigger_result",
        "parent_analysis", "expires_at",
    )
    _STALENESS_KEYS = (
        "actual_updated_at", "actual_history",
        "source_verified_at", "staleness_check_hours",
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
        # staleness detection 字段 — 仅非 None 时写入
        for key in EventCalendar._STALENESS_KEYS:
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

    @staticmethod
    def _generate_jobless_claims_events(
        year: int,
        skip_weeks: set[int] | None = None,
    ) -> list[CalendarEvent]:
        """初请失业金人数 — 每周四 08:30 ET（DOL 固定发布日）.

        Args:
            year: 目标年份
            skip_weeks: 要跳过的 ISO 周号集合（JSONL 中已有该周事件时传入）
        """
        skip = skip_weeks or set()
        events: list[CalendarEvent] = []
        jan1 = datetime(year, 1, 1)
        days_until_thu = (3 - jan1.weekday()) % 7
        current = jan1 + timedelta(days=days_until_thu)
        while current.year == year:
            iso_week = current.isocalendar()[1]
            if iso_week not in skip:
                dt = current.replace(hour=8, minute=30)
                dt = dt.replace(tzinfo=_et_offset(dt))
                events.append(CalendarEvent(
                    name="初请失业金人数",
                    event_type=EventType.NFP,
                    scheduled_at=dt,
                    impact=EventImpact.MEDIUM,
                    source="U.S. Department of Labor",
                    description="周度初请失业金人数，高频劳动力市场指标。"
                                "持续上升→就业恶化→降息预期→利好黄金（每周四发布）",
                ))
            current += timedelta(days=7)
        return events

    # ------------------------------------------------------------------
    # 全球央行事件生成器（欧洲+英国+日本）
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_ecb_events(year: int) -> list[CalendarEvent]:
        """ECB利率决议 — 约每6周一次，全年~8次.

        ECB通常在周四14:15 CET (08:15 ET) 公布，新闻发布会14:45 CET.
        按月推算：1,3,4,6,7,9,10,12 月的中旬。
        """
        events: list[CalendarEvent] = []
        ecb_months = (1, 3, 4, 6, 7, 9, 10, 12)
        for month in ecb_months:
            dt = datetime(year, month, 15, 8, 15)
            dt = dt.replace(tzinfo=_et_offset(dt))
            events.append(CalendarEvent(
                name="ECB利率决议",
                event_type=EventType.ECB,
                scheduled_at=dt,
                impact=EventImpact.HIGH,
                source="European Central Bank (approx.)",
                description="欧洲央行利率决议 + Lagarde新闻发布会（推算日期）",
            ))
        return events

    @staticmethod
    def _generate_boe_events(year: int) -> list[CalendarEvent]:
        """BOE利率决议 — 约每6周一次，全年~8次.

        BOE通常在周四12:00 GMT (07:00 ET) 公布.
        """
        events: list[CalendarEvent] = []
        boe_months = (2, 3, 5, 6, 8, 9, 11, 12)
        for month in boe_months:
            dt = datetime(year, month, 15, 7, 0)
            dt = dt.replace(tzinfo=_et_offset(dt))
            events.append(CalendarEvent(
                name="BOE利率决议",
                event_type=EventType.BOE,
                scheduled_at=dt,
                impact=EventImpact.MEDIUM,
                source="Bank of England (approx.)",
                description="英国央行利率决议（推算日期）",
            ))
        return events

    @staticmethod
    def _generate_global_flash_pmi_events(year: int) -> list[CalendarEvent]:
        """全球Flash PMI — 每月24日前后 (S&P Global).

        覆盖：法国/德国/欧元区/英国/美国 Flash PMI.
        制造业PMI + 服务业PMI + 综合PMI 在同一天发布（flash）.
        """
        events: list[CalendarEvent] = []
        for month in range(1, 13):
            dt = datetime(year, month, 24, 9, 45)
            dt = dt.replace(tzinfo=_et_offset(dt))
            events.append(CalendarEvent(
                name="全球Flash PMI (7月)",
                event_type=EventType.PMI,
                scheduled_at=dt,
                impact=EventImpact.HIGH,
                source="S&P Global (approx.)",
                description=(
                    "法国/德国/欧元区/英国/美国 Flash PMI（制造业+服务业+综合），"
                    "推算日期，实际以S&P Global发布日历为准"
                ),
            ))
        return events

    @staticmethod
    def _generate_uk_cpi_events(year: int) -> list[CalendarEvent]:
        """UK CPI — 每月中旬 (约15-20日) 07:00 GMT."""
        events: list[CalendarEvent] = []
        for month in range(1, 13):
            dt = datetime(year, month, 17, 2, 0)
            dt = dt.replace(tzinfo=_et_offset(dt))
            events.append(CalendarEvent(
                name="UK CPI (6月)",
                event_type=EventType.CPI,
                scheduled_at=dt,
                impact=EventImpact.MEDIUM,
                source="ONS (approx.)",
                description="英国消费者物价指数，月度同比/环比（推算日期）",
            ))
        return events

    @staticmethod
    def _generate_german_zew_events(year: int) -> list[CalendarEvent]:
        """德国ZEW经济情绪指数 — 每月中旬 (约15-18日) 11:00 CEST."""
        events: list[CalendarEvent] = []
        for month in range(1, 13):
            dt = datetime(year, month, 17, 5, 0)
            dt = dt.replace(tzinfo=_et_offset(dt))
            events.append(CalendarEvent(
                name="德国ZEW经济情绪指数",
                event_type=EventType.PMI,  # 复用 PMI 类型 (经济情绪指标)
                scheduled_at=dt,
                impact=EventImpact.MEDIUM,
                source="ZEW (approx.)",
                description="德国/欧元区ZEW经济景气指数（推算日期）",
            ))
        return events

    @staticmethod
    def _generate_eu_consumer_confidence(year: int) -> list[CalendarEvent]:
        """欧盟消费者信心指数 — 每月20-23日 (Flash)."""
        events: list[CalendarEvent] = []
        for month in range(1, 13):
            dt = datetime(year, month, 22, 10, 0)
            dt = dt.replace(tzinfo=_et_offset(dt))
            events.append(CalendarEvent(
                name="欧盟消费者信心指数(7月初值)",
                event_type=EventType.PMI,  # 复用 PMI 类型 (消费者情绪)
                scheduled_at=dt,
                impact=EventImpact.LOW,
                source="European Commission (approx.)",
                description="欧元区消费者信心指数初值（推算日期）",
            ))
        return events

    # ------------------------------------------------------------------
    # 二级美国经济事件生成器（消费者信心、制造业调查、贸易帐、房价等）
    # 注：这些事件的日期为推算值，实际发布日期可能因节假日微调±1天
    # ------------------------------------------------------------------

    @staticmethod
    def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> datetime:
        """月份内第 n 个 weekday (0=Mon) 的 date."""
        first_day = datetime(year, month, 1)
        days_until = (weekday - first_day.weekday()) % 7
        day = 1 + days_until + 7 * (n - 1)
        return datetime(year, month, day)

    @staticmethod
    def _last_weekday_of_month(year: int, month: int, weekday: int) -> datetime:
        """月份内最后一个 weekday (0=Mon) 的 date."""
        if month == 12:
            last_day = datetime(year, 12, 31)
        else:
            last_day = datetime(year, month + 1, 1) - timedelta(days=1)
        days_back = (last_day.weekday() - weekday) % 7
        return last_day - timedelta(days=days_back)

    @staticmethod
    def _generate_us_consumer_confidence_events(year: int) -> list[CalendarEvent]:
        """美国谘商会消费者信心指数 — 每月最后一个周二 10:00 ET.
        影响: MEDIUM (消费信心→消费支出预期→经济预期)
        """
        events: list[CalendarEvent] = []
        for month in range(1, 13):
            dt = EventCalendar._last_weekday_of_month(year, month, 1)  # 1=Tuesday
            dt = dt.replace(hour=10, minute=0, tzinfo=_et_offset(dt))
            events.append(CalendarEvent(
                name="美国谘商会消费者信心指数",
                event_type=EventType.PMI,
                scheduled_at=dt,
                impact=EventImpact.MEDIUM,
                source="Conference Board (approx.)",
                description="谘商会消费者信心指数，反映消费者对经济/就业/收入的预期（推算）",
            ))
        return events

    @staticmethod
    def _generate_richmond_fed_manufacturing_events(year: int) -> list[CalendarEvent]:
        """里奇蒙德联储制造业指数 — 每月第4个周二 10:00 ET.
        影响: MEDIUM (地区制造业景气指标)
        """
        events: list[CalendarEvent] = []
        for month in range(1, 13):
            dt = EventCalendar._nth_weekday_of_month(year, month, 1, 4)  # 1=Tuesday, 4th
            dt = dt.replace(hour=10, minute=0, tzinfo=_et_offset(dt))
            events.append(CalendarEvent(
                name="美国里奇蒙德联储制造业指数",
                event_type=EventType.PMI,
                scheduled_at=dt,
                impact=EventImpact.MEDIUM,
                source="Richmond Fed (approx.)",
                description="里奇蒙德联储制造业指数，反映美东中部地区制造业景气（推算）",
            ))
        return events

    @staticmethod
    def _generate_housing_price_indices_events(year: int) -> list[CalendarEvent]:
        """房价指数 — FHFA + S&P/Case-Shiller 每月最后一个周二 09:00 ET.
        两个指数同日发布。
        影响: MEDIUM (房价→财富效应→通胀/消费)
        """
        events: list[CalendarEvent] = []
        for month in range(1, 13):
            dt = EventCalendar._last_weekday_of_month(year, month, 1)  # 1=Tuesday
            dt = dt.replace(hour=9, minute=0, tzinfo=_et_offset(dt))
            events.append(CalendarEvent(
                name="美国FHFA房价指数月率",
                event_type=EventType.PMI,
                scheduled_at=dt,
                impact=EventImpact.MEDIUM,
                source="FHFA (approx.)",
                description="联邦住房金融局房价指数月度环比（推算）",
            ))
            events.append(CalendarEvent(
                name="美国S&P/CS20座大城市房价指数年率",
                event_type=EventType.PMI,
                scheduled_at=dt,
                impact=EventImpact.MEDIUM,
                source="S&P/Case-Shiller (approx.)",
                description="标普/凯斯席勒20城市房价指数同比（推算）",
            ))
        return events

    @staticmethod
    def _generate_goods_trade_balance_events(year: int) -> list[CalendarEvent]:
        """商品贸易帐初值 — 每月约25-28日 08:30 ET.
        实际发布日期为该月倒数第3-5个工作日（此处推算取26日作为近似）。
        影响: MEDIUM (贸易逆差→GDP核算→汇率)
        """
        events: list[CalendarEvent] = []
        for month in range(1, 13):
            # 取当月26日，如逢周末则前移至前一个工作日
            dt = datetime(year, month, 26, 8, 30)
            if dt.weekday() >= 5:  # 周末
                dt -= timedelta(days=dt.weekday() - 4)  # 回退到周五
            dt = dt.replace(tzinfo=_et_offset(dt))
            events.append(CalendarEvent(
                name="美国商品贸易帐(初值)",
                event_type=EventType.PMI,
                scheduled_at=dt,
                impact=EventImpact.MEDIUM,
                source="Census Bureau (approx.)",
                description="月度商品贸易逆差初值（推算约26日）",
            ))
        return events

    @staticmethod
    def _generate_michigan_sentiment_events(year: int) -> list[CalendarEvent]:
        """密歇根消费者信心指数 — 每月第2个周五 10:00 ET.
        初值(Fri, week 2) + 终值(Fri, last week or next month)。
        此处仅生成初值，终值覆盖在下一个月的初值附近。
        影响: MEDIUM (消费者情绪→消费预期)
        """
        events: list[CalendarEvent] = []
        for month in range(1, 13):
            dt = EventCalendar._nth_weekday_of_month(year, month, 4, 2)  # 4=Friday, 2nd
            dt = dt.replace(hour=10, minute=0, tzinfo=_et_offset(dt))
            events.append(CalendarEvent(
                name="美国密歇根大学消费者信心指数初值",
                event_type=EventType.PMI,
                scheduled_at=dt,
                impact=EventImpact.MEDIUM,
                source="University of Michigan (approx.)",
                description="密歇根消费者信心指数月度初值（推算第2个周五）",
            ))
        return events

    # ------------------------------------------------------------------
    # 回退：无数据源的动态推算
    # ------------------------------------------------------------------

    @staticmethod
    def _load_approximate_calendar(year: int) -> list[CalendarEvent]:
        """从未知年份推算事件日期 (精确度较低).

        用于 JSONL 无覆盖的年份，基于历史规律推算。
        覆盖美国、欧洲、英国、全球PMI等主要市场事件。
        """
        events: list[CalendarEvent] = []

        def _mk_dt(month: int, day: int, hour: int, minute: int) -> datetime:
            dt = datetime(year, month, day, hour, minute)
            return dt.replace(tzinfo=_et_offset(dt))

        # ---- 美国事件 ----
        # FOMC: 全年8次，约6周一次
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

        # ISM PMI
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

        # ---- 美国二级事件 ----
        events.extend(EventCalendar._generate_us_consumer_confidence_events(year))
        events.extend(EventCalendar._generate_richmond_fed_manufacturing_events(year))
        events.extend(EventCalendar._generate_housing_price_indices_events(year))
        events.extend(EventCalendar._generate_goods_trade_balance_events(year))
        events.extend(EventCalendar._generate_michigan_sentiment_events(year))

        # ---- 欧洲事件 ----
        events.extend(EventCalendar._generate_ecb_events(year))
        events.extend(EventCalendar._generate_boe_events(year))
        events.extend(EventCalendar._generate_uk_cpi_events(year))
        events.extend(EventCalendar._generate_german_zew_events(year))
        events.extend(EventCalendar._generate_eu_consumer_confidence(year))

        # ---- 全球事件 ----
        events.extend(EventCalendar._generate_global_flash_pmi_events(year))

        return events
