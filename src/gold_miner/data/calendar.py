"""事件日历 — 美联储决议、CPI、PPI、非农等重要事件.

事件数据存储于 data/calendar_events.jsonl，每条一行 JSON。
代码只负责加载/查询/追加，不包含硬编码日期。

数据来源：
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
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from loguru import logger


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
        target = year or datetime.now().year
        jsonl_events = self._load_from_jsonl(year=target)

        # JSONL 无该年份数据时回退到推算
        if not jsonl_events:
            logger.warning(f"JSONL 无 {target} 年日历数据，使用推算")
            events = self._load_approximate_calendar(target)
        else:
            events = list(jsonl_events)
            events.extend(self._generate_nfp_events(target))

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
        now = reference_time or datetime.now()
        cutoff = now + timedelta(days=days)
        impact_order = {EventImpact.HIGH: 3, EventImpact.MEDIUM: 2, EventImpact.LOW: 1}
        min_level = impact_order.get(min_impact, 1)
        return [
            e for e in self.events
            if now <= e.scheduled_at <= cutoff
            and impact_order.get(e.impact, 0) >= min_level
        ]

    def get_today(self) -> list[CalendarEvent]:
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today + timedelta(days=1)
        return [e for e in self.events if today <= e.scheduled_at < tomorrow]

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
            scheduled_at=datetime.fromisoformat(obj["scheduled_at"]),
            impact=EventImpact(obj.get("impact", "medium")),
            source=obj.get("source", ""),
            description=obj.get("description", ""),
        )

    @staticmethod
    def _to_dict(event: CalendarEvent) -> dict[str, Any]:
        return {
            "name": event.name,
            "event_type": event.event_type.value,
            "scheduled_at": event.scheduled_at.isoformat(),
            "impact": event.impact.value,
            "source": event.source,
            "description": event.description,
        }

    # ------------------------------------------------------------------
    # 算法生成事件 (不依赖外部数据)
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_nfp_events(year: int) -> list[CalendarEvent]:
        """非农就业 — 每月第一个周五 (算法固定)."""
        events: list[CalendarEvent] = []
        for month in range(1, 13):
            first_day = datetime(year, month, 1)
            days_until_fri = (4 - first_day.weekday()) % 7
            nfp_day = 1 + days_until_fri
            events.append(CalendarEvent(
                name="非农就业",
                event_type=EventType.NFP,
                scheduled_at=datetime(year, month, nfp_day, 8, 30),
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

        # FOMC: 全年8次，约6周一次，大致在每月中旬
        for month in (1, 3, 5, 6, 7, 9, 11, 12):
            events.append(CalendarEvent(
                name="FOMC利率决议",
                event_type=EventType.FED_RATE,
                scheduled_at=datetime(year, month, 12, 14, 0),
                impact=EventImpact.HIGH,
                source="Federal Reserve (approx.)",
                description="美联储联邦公开市场委员会利率决议（推算日期）",
            ))

        # CPI: 每月约10-15日
        for month in range(1, 13):
            events.append(CalendarEvent(
                name="美国CPI",
                event_type=EventType.CPI,
                scheduled_at=datetime(year, month, 13, 8, 30),
                impact=EventImpact.HIGH,
                source="BLS (approx.)",
                description="美国消费者物价指数（推算）",
            ))

        # PPI: 每月约11-15日
        for month in range(1, 13):
            events.append(CalendarEvent(
                name="美国PPI",
                event_type=EventType.PPI,
                scheduled_at=datetime(year, month, 14, 8, 30),
                impact=EventImpact.HIGH,
                source="BLS (approx.)",
                description="美国生产者价格指数（推算）",
            ))

        # PCE: 每月约25-31日
        for month in range(1, 13):
            events.append(CalendarEvent(
                name="核心PCE物价指数",
                event_type=EventType.PCE,
                scheduled_at=datetime(year, month, 28, 8, 30),
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
                scheduled_at=datetime(year, month, pmi_day, 10, 0),
                impact=EventImpact.HIGH,
                source="S&P Global / ISM (approx.)",
                description="制造业景气度指标（推算日期）",
            ))
            events.append(CalendarEvent(
                name="ISM服务业PMI",
                event_type=EventType.PMI,
                scheduled_at=datetime(year, month, min(pmi_day + 1, 28), 10, 0),
                impact=EventImpact.HIGH,
                source="S&P Global / ISM (approx.)",
                description="服务业景气度指标（推算日期）",
            ))

        return events
