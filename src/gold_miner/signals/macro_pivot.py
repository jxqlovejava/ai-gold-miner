"""宏观政策转向多线汇聚信号.

借鉴博主框架：不逐事件给信号，而是把近期已发布事件按「独立线索」分组
（就业 / 通胀 / 政策姿态 / 地缘汇率 / 央行购金），检测多条独立线索是否
指向同一宏观剧本（降息 / 加息 / 避险 / 央行结构性配置）。

只有当 ≥2 条不同类别线索汇聚到同一剧本时才输出信号：
- 跨类别汇聚降低「单一数据点过度外推」的风险（博主式三条线→同一剧本）
- 不同类别天然独立，避免同源重复计算

与 RecentEventSignalGenerator 的区别：
- recent_events 逐事件时效性加权（7 天窗口）；
- 本模块做跨类别线索的「剧本汇聚」检测，窗口更长（默认 45 天），
  因为政策转向线索按周/月演变，单月非农、单次 CPI 不足以定剧本。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger

from gold_miner.data.calendar import CalendarEvent, EventCalendar
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength
from gold_miner.signals.recent_events import _infer_direction_from_event


@dataclass
class MacroPivotConfig:
    """宏观剧本汇聚检测配置."""

    lookback_days: int = 45
    min_convergence_threads: int = 2   # 汇聚到同一剧本所需最少独立线索数
    min_thread_score: float = 0.3      # 单条线索最低累积权重（低于视为证据不足）
    # 长尾衰减: (天阈值, 权重) — 政策转向线索按周/月演变, 窗口比逐事件更长
    weights: list[tuple[float, float]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.weights is None:
            self.weights = [
                (7, 1.0),
                (14, 0.7),
                (30, 0.4),
                (45, 0.2),
            ]

    def compute_weight(self, hours_ago: float) -> float:
        """根据距今小时数计算长尾衰减权重."""
        for days, weight in self.weights:
            if hours_ago <= days * 24:
                return weight
        return 0.0


# ---------------------------------------------------------------------------
# 线索分类 / 方向 / 剧本映射
# ---------------------------------------------------------------------------

_THREAD_LABELS = {
    "employment": "就业",
    "inflation": "通胀",
    "policy": "政策姿态",
    "fx_geo": "地缘/汇率",
    "central_bank": "央行购金",
}

_SCRIPT_META: dict[str, dict[str, object]] = {
    "dovish": {
        "name": "宏观政策转向多线汇聚(降息预期)",
        "direction": SignalDirection.BULLISH,
        "desc": "多条独立线索（就业/通胀/政策）同向指向宽松/降息 → 利多黄金",
    },
    "hawkish": {
        "name": "宏观政策转向多线汇聚(加息预期)",
        "direction": SignalDirection.BEARISH,
        "desc": "多条独立线索（就业/通胀/政策）同向指向紧缩/加息 → 利空黄金",
    },
    "risk_off": {
        "name": "宏观避险多线汇聚",
        "direction": SignalDirection.BULLISH,
        "desc": "多条地缘/汇率线索指向流动性紧张/避险（如联手救日元→美债脆弱）→ 利多黄金",
    },
    "structural": {
        "name": "央行结构性配置多线汇聚",
        "direction": SignalDirection.BULLISH,
        "desc": "多条线索指向央行去美元化/增持黄金 → 结构性利多",
    },
}


def _thread_of(event: CalendarEvent) -> str | None:
    """将事件归类到独立线索."""
    t = event.event_type.value
    if t in ("nfp", "pmi", "pmi_markit"):
        return "employment"
    if t in ("cpi", "ppi", "pce"):
        return "inflation"
    if t in ("fed_rate", "fed_speech", "fomc_minutes", "ecb", "boe"):
        return "policy"
    if t in ("geo",):
        return "fx_geo"
    if t == "gold_reserve":
        return "central_bank"
    return None


def _thread_direction(event: CalendarEvent) -> SignalDirection | None:
    """按线索类型判定事件对黄金的方向.

    地缘/汇率与央行购金有专门的关键词映射；其余复用通用事件→方向映射。
    """
    t = event.event_type.value
    actual = (event.actual or "").lower()

    # 地缘/汇率: 通用映射处理不了「升级/缓和」，需专门关键词
    if t == "geo":
        # 汇率干预/动荡 → 流动性紧张 → 利多黄金（如美日联手救日元）
        if any(k in actual for k in ("干预", "日元", "yen", "汇率动荡")):
            return SignalDirection.BULLISH
        escalation = ["冲突", "紧张", "升级", "制裁", "战争", "war", "strike",
                      "attack", "sanction", "intervention"]
        de_escalation = ["缓和", "降温", "协议", "停火", "撤销", "ceasefire",
                         "agreement", "de-escalat", "withdraw"]
        if any(k in actual for k in escalation):
            return SignalDirection.BULLISH
        if any(k in actual for k in de_escalation):
            return SignalDirection.BEARISH
        return None

    # 央行购金
    if t == "gold_reserve":
        buying = ["增持", "购买", "买入", "购金", "增加", "吨", "increase", "buy", "purchase"]
        selling = ["减持", "卖出", "抛售", "sell", "reduce"]
        if any(k in actual for k in buying):
            return SignalDirection.BULLISH
        if any(k in actual for k in selling):
            return SignalDirection.BEARISH
        return None

    # 其余 (就业/通胀/政策): 复用通用事件→方向映射 (gold_bias 显式字段优先, 忽略冲突说明)
    direction, _conflict = _infer_direction_from_event(
        event.name,
        event.actual or "",
        event.forecast,
        gold_bias=getattr(event, "gold_bias", None),
    )
    return direction


def _script_of(thread: str, direction: SignalDirection) -> str | None:
    """(线索, 方向) → 宏观剧本. 返回 None 表示该线索不指向任何剧本."""
    if direction == SignalDirection.BULLISH:
        return {
            "employment": "dovish",
            "inflation": "dovish",
            "policy": "dovish",
            "fx_geo": "risk_off",
            "central_bank": "structural",
        }.get(thread)
    if direction == SignalDirection.BEARISH:
        return {
            "employment": "hawkish",
            "inflation": "hawkish",
            "policy": "hawkish",
        }.get(thread)
    return None


class MacroPivotSignalGenerator:
    """宏观政策转向多线汇聚信号生成器."""

    def __init__(
        self,
        calendar: EventCalendar | None = None,
        config: MacroPivotConfig | None = None,
    ) -> None:
        self.calendar = calendar or EventCalendar()
        self.config = config or MacroPivotConfig()

    def generate_signals(self) -> list[Signal]:
        """生成宏观剧本汇聚信号."""
        self._ensure_loaded()
        now = datetime.now(tz=UTC)

        events = self.calendar.get_recent_events_with_results(
            lookback_days=self.config.lookback_days,
        )
        if not events:
            logger.debug("[MacroPivot] 无近期事件结果，跳过")
            return []

        # 每条独立线索的累积方向分 + 证据列表
        thread_scores: dict[str, float] = defaultdict(float)
        thread_evidence: dict[str, list[str]] = defaultdict(list)

        for ev in events:
            thread = _thread_of(ev)
            if thread is None:
                continue
            direction = _thread_direction(ev)
            if direction is None:
                continue

            hours_ago = (now - ev.scheduled_at).total_seconds() / 3600
            weight = self.config.compute_weight(hours_ago)
            if weight <= 0:
                continue

            sign = 1.0 if direction == SignalDirection.BULLISH else -1.0
            thread_scores[thread] += sign * weight
            thread_evidence[thread].append(
                f"{ev.name}({'利多' if direction == SignalDirection.BULLISH else '利空'}, "
                f"实际={ev.actual})"
            )

        # 线索方向 → 剧本
        script_threads: dict[str, list[str]] = defaultdict(list)
        for thread, score in thread_scores.items():
            if abs(score) < self.config.min_thread_score:
                continue
            direction = SignalDirection.BULLISH if score > 0 else SignalDirection.BEARISH
            script = _script_of(thread, direction)
            if script is not None:
                script_threads[script].append(thread)

        signals: list[Signal] = []
        for script, threads in script_threads.items():
            if len(threads) < self.config.min_convergence_threads:
                continue
            meta = _SCRIPT_META.get(script)
            if meta is None:
                continue

            n = len(threads)
            strength = SignalStrength.STRONG if n >= 3 else SignalStrength.MODERATE
            score = round(min(0.2 + 0.15 * (n - 1), 0.6), 2)

            thread_desc = ", ".join(
                f"{_THREAD_LABELS.get(t, t)}({len(thread_evidence[t])}条)"
                for t in threads
            )
            signals.append(Signal(
                name=str(meta["name"]),
                dimension="event",
                direction=meta["direction"],  # type: ignore[arg-type]
                strength=strength,
                score=score,
                description=(
                    f"{meta['desc']} | {n} 条独立线索汇聚: {thread_desc}。"
                    f"跨类别汇聚降低单点数据过度外推风险"
                ),
                metadata={
                    "source": "macro_pivot_convergence",
                    "script": script,
                    "threads": threads,
                    "thread_count": n,
                    "thread_labels": [_THREAD_LABELS.get(t, t) for t in threads],
                    "evidence": {t: thread_evidence[t] for t in threads},
                    "lookback_days": self.config.lookback_days,
                },
            ))

        if signals:
            logger.info(
                f"[MacroPivot] {len(events)}个事件 → {len(thread_scores)}条线索 → "
                f"{len(signals)}个剧本汇聚信号"
            )
        return signals

    def _ensure_loaded(self) -> None:
        if not self.calendar.events:
            self.calendar.load_fixed_calendar()
