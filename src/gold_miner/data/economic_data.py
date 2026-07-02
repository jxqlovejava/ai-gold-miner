"""经济数据记录 — 用于持久化宏观经济指标发布值，支持后续回测.

每条记录对应一次数据发布（如 JOLTS、非农、CPI 等），包含实际值、预期值、
前值、来源、Source Truth 等级及发布时的市场快照。数据以追加方式写入
`data/private/economic_data.jsonl`。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Any

from loguru import logger

from gold_miner.storage.local import LocalFileStore


# ------------------------------------------------------------------
# Market Snapshot
# ------------------------------------------------------------------


@dataclass(frozen=True)
class MarketSnapshot:
    """数据发布时的市场状态快照 — 用于回测时还原真实决策场景."""

    captured_at: str = ""  # ISO-8601 时间戳
    spot_gold_usd: float | None = None  # 现货黄金 XAUUSD
    au9999_cny: float | None = None  # 上海金交所 Au9999 (元/克)
    dxy: float | None = None  # 美元指数
    us_10y_yield: float | None = None  # 10年期美债收益率
    us_2y_yield: float | None = None  # 2年期美债收益率
    usd_cny: float | None = None  # 美元/人民币
    silver_usd: float | None = None  # 白银 XAGUSD
    wti_oil: float | None = None  # WTI 原油
    vix: float | None = None  # 恐慌指数
    fed_rate: float | None = None  # 联邦基金利率
    cme_fedwatch_hike_prob: float | None = None  # CME FedWatch 下次加息概率
    extra: dict[str, Any] = field(default_factory=dict)  # 扩展字段

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # 清理 None 值，减小存储体积
        return {k: v for k, v in d.items() if v is not None and k != "extra"} | (
            self.extra if self.extra else {}
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MarketSnapshot:
        known = {f.name for f in cls.__dataclass_fields__.values() if f.name != "extra"}
        extra = {k: v for k, v in data.items() if k not in known}
        return cls(**{k: v for k, v in data.items() if k in known}, extra=extra)


# ------------------------------------------------------------------
# Economic Data Point
# ------------------------------------------------------------------


@dataclass(frozen=True)
class EconomicDataPoint:
    """单次经济指标发布记录."""

    indicator: str  # 指标名称，如 "nonfarm_payrolls"
    release_date: str  # 发布日期，ISO-8601（YYYY-MM-DD）
    actual: float | int | str | None  # 实际值
    forecast: float | int | str | None = None  # 市场预期值
    previous: float | int | str | None = None  # 前值
    observation_date: str = ""  # 数据观测/参考日期（如 FRED date）
    unit: str = ""  # 单位，如 "万人" / "%"
    period: str = ""  # 数据对应周期，如 "2026-06"
    source: str = ""  # 数据来源
    source_tier: str = "unknown"  # T0/T1/T2/T3
    impact: str = "high"  # high/medium/low
    notes: str = ""  # 备注
    fetched_at: str = field(default_factory=lambda: datetime.now().isoformat())
    batch_id: str = ""  # 同一批次发布的分组 ID（如 "nfp_20260702"）
    market_snapshot: MarketSnapshot | None = None  # 发布时的市场状态

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.market_snapshot is not None:
            d["market_snapshot"] = self.market_snapshot.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EconomicDataPoint:
        # 不修改传入 dict — 拷贝一份
        data_copy = dict(data)
        snapshot_data = data_copy.pop("market_snapshot", None)
        snapshot = MarketSnapshot.from_dict(snapshot_data) if snapshot_data else None
        point = cls(
            **{k: v for k, v in data_copy.items() if k in cls.__dataclass_fields__},
        )
        if snapshot is not None:
            object.__setattr__(point, "market_snapshot", snapshot)
        return point


# ------------------------------------------------------------------
# Economic Data Recorder
# ------------------------------------------------------------------


class EconomicDataRecorder:
    """经济数据记录器.

    自动去重：同一指标 + 同一发布日期 + 同一数据周期只保留一条记录。
    支持 force=True 覆盖已有记录（用于数据修正场景）。
    支持 save_batch() 批量保存同一批次发布的多个指标。
    """

    def __init__(self, store: LocalFileStore | None = None) -> None:
        self.store = store or LocalFileStore()

    # ------------------------------------------------------------------
    # 单条保存
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # 批量保存
    # ------------------------------------------------------------------

    def save_batch(
        self,
        points: list[EconomicDataPoint],
        batch_id: str = "",
        force: bool = False,
    ) -> int:
        """批量保存同一批次发布的多个经济指标.

        Args:
            points: 待保存的数据点列表。
            batch_id: 批次 ID，如 "nfp_20260702"。
                      若提供且 point.batch_id 为空则自动填充。
            force: 覆盖同 key 的已有记录。

        Returns:
            实际保存成功的数据点数量。
        """
        if not points:
            return 0

        # 自动填充 batch_id
        if batch_id:
            points = [
                replace(p, batch_id=batch_id) if not p.batch_id else p
                for p in points
            ]

        saved_count = 0
        for point in points:
            if self.save(point, force=force):
                saved_count += 1

        logger.info(f"批量保存完成: {saved_count}/{len(points)} 条 (batch: {batch_id or 'N/A'})")
        return saved_count

    # ------------------------------------------------------------------
    # 加载 & 查询
    # ------------------------------------------------------------------

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
        batch_id: str | None = None,
    ) -> list[EconomicDataPoint]:
        """按指标名称和日期范围查询."""
        results = self.load()
        if indicator:
            results = [r for r in results if r.indicator == indicator]
        if start_date:
            results = [r for r in results if r.release_date >= start_date]
        if end_date:
            results = [r for r in results if r.release_date <= end_date]
        if batch_id:
            results = [r for r in results if r.batch_id == batch_id]
        return sorted(results, key=lambda r: r.release_date)

    def find_batch(self, batch_id: str) -> list[EconomicDataPoint]:
        """查询同一批次的所有数据点."""
        return self.find(batch_id=batch_id)

    def list_batches(self) -> list[dict[str, Any]]:
        """列出所有批次及其摘要."""
        batches: dict[str, dict[str, Any]] = {}
        for point in self.load():
            bid = point.batch_id
            if not bid:
                continue
            if bid not in batches:
                batches[bid] = {
                    "batch_id": bid,
                    "release_date": point.release_date,
                    "count": 0,
                    "indicators": [],
                    "has_snapshot": point.market_snapshot is not None,
                }
            batches[bid]["count"] += 1
            batches[bid]["indicators"].append(point.indicator)
        return sorted(batches.values(), key=lambda b: b["release_date"], reverse=True)
