"""经济数据记录 — 用于持久化宏观经济指标发布值，支持后续回测.

每条记录对应一次数据发布（如 JOLTS、非农、CPI 等），包含实际值、预期值、
前值、来源及 Source Truth 等级。数据以追加方式写入
`data/private/economic_data.jsonl`。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger

from gold_miner.storage.local import LocalFileStore


@dataclass(frozen=True)
class EconomicDataPoint:
    """单次经济指标发布记录."""

    indicator: str                      # 指标名称，如 "jolts_job_openings"
    release_date: str                   # 发布日期，ISO-8601（YYYY-MM-DD）
    actual: float | int | str | None    # 实际值
    forecast: float | int | str | None = None  # 市场预期值
    previous: float | int | str | None = None  # 前值
    observation_date: str = ""          # 数据观测/参考日期（如 FRED date）
    unit: str = ""                      # 单位，如 "万人" / "%"
    period: str = ""                    # 数据对应周期，如 "2026-05"
    source: str = ""                    # 数据来源
    source_tier: str = "unknown"        # T0/T1/T2/T3
    impact: str = "high"                # high/medium/low
    notes: str = ""                     # 备注
    fetched_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EconomicDataPoint:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class EconomicDataRecorder:
    """经济数据记录器.

    自动去重：同一指标 + 同一发布日期 + 同一数据周期只保留一条记录。
    支持 force=True 覆盖已有记录（用于数据修正场景）。
    """

    def __init__(self, store: LocalFileStore | None = None) -> None:
        self.store = store or LocalFileStore()

    def save(self, point: EconomicDataPoint, force: bool = False) -> bool:
        """保存一条经济数据发布记录.

        Args:
            point: 待保存的数据点。
            force: 是否覆盖同 key 的已有记录（如数据修正）。默认跳过重复。
        """
        try:
            existing = self.load()
            key = (point.indicator, point.release_date, point.period)
            filtered: list[EconomicDataPoint] = []
            for record in existing:
                if (record.indicator, record.release_date, record.period) == key:
                    if not force:
                        logger.debug(f"经济数据已存在，跳过: {key}")
                        return False
                    logger.info(f"覆盖已有经济数据: {key}")
                    continue
                filtered.append(record)

            filtered.append(point)
            self.store.save_economic_data([p.to_dict() for p in filtered])
            logger.info(f"已保存经济数据: {point.indicator} @ {point.release_date}")
            return True
        except Exception as e:
            logger.warning(f"保存经济数据失败: {e}")
            return False

    def load(self) -> list[EconomicDataPoint]:
        """加载所有已保存的经济数据，跳过损坏记录."""
        records = self.store.load_economic_data()
        points: list[EconomicDataPoint] = []
        for idx, raw in enumerate(records):
            try:
                points.append(EconomicDataPoint.from_dict(raw))
            except Exception as e:
                logger.warning(f"第 {idx} 条经济数据记录损坏，跳过: {e}")
        return points

    def find(
        self,
        indicator: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[EconomicDataPoint]:
        """按指标名称和日期范围查询."""
        results = self.load()
        if indicator:
            results = [r for r in results if r.indicator == indicator]
        if start_date:
            results = [r for r in results if r.release_date >= start_date]
        if end_date:
            results = [r for r in results if r.release_date <= end_date]
        return sorted(results, key=lambda r: r.release_date)
