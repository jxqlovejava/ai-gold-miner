"""Monitor 事件信号生成器.

将第〇步 Monitor 检查的结果转换为管线 Step 2 可消费的信号：

- triggered monitors → 方向信号（按触发时间时效性加权）
- active monitors  → 中性"观测中"提醒信号

打通第〇步 close_monitor() → Step 2 _step_generate_signals 的数据流断点。
"""

from __future__ import annotations

from datetime import datetime

from loguru import logger

from gold_miner.data.calendar import EventCalendar
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength
from gold_miner.signals.recent_events import (
    RecencyWeightConfig,
    _infer_strength_from_weight,
)


def _infer_monitor_direction(trigger_result: str) -> SignalDirection:
    """从 monitor 触发结果文本推断方向信号.

    基于关键词匹配做快速推断。复杂判断由 AI 分析补充。
    """
    text = trigger_result.lower()

    # 利多关键词
    bullish_keywords = [
        "利多", "看多", "加仓", "买入", "反弹", "上涨",
        "bullish", "buy", "long",
    ]
    if any(kw in text for kw in bullish_keywords):
        return SignalDirection.BULLISH

    # 利空关键词
    bearish_keywords = [
        "利空", "看空", "减仓", "卖出", "下跌", "回调",
        "bearish", "sell", "short",
    ]
    if any(kw in text for kw in bearish_keywords):
        return SignalDirection.BEARISH

    # 默认中性
    return SignalDirection.NEUTRAL


class MonitorSignalGenerator:
    """Monitor 事件信号生成器.

    两种信号类型：

    1. **触发信号** (triggered): status="triggered" 的 monitor
       - 方向由 trigger_result 文本推断
       - 得分按 triggered_at 时效性加权
       - 包含触发条件、触发结果、建议动作

    2. **观测中信号** (active): status="active" 的 monitor
       - 中性方向，score=0.0
       - 仅作提醒："正在观测 X"
    """

    def __init__(
        self,
        calendar: EventCalendar | None = None,
        config: RecencyWeightConfig | None = None,
    ) -> None:
        self.calendar = calendar or EventCalendar()
        self.config = config or RecencyWeightConfig()

    def generate_signals(self) -> list[Signal]:
        """生成 monitor 相关信号.

        合并 triggered + active 两类信号。
        """
        self._ensure_loaded()
        signals: list[Signal] = []

        # 1. 已触发的 monitor — 方向信号
        signals.extend(self._generate_triggered_signals())

        # 2. 活跃的 monitor — 观测提醒
        signals.extend(self._generate_active_monitor_signals())

        logger.info(
            f"[Monitor] {len(signals)}个信号 "
            f"(触发: {sum(1 for s in signals if s.direction != SignalDirection.NEUTRAL)}, "
            f"观测中: {sum(1 for s in signals if s.direction == SignalDirection.NEUTRAL)})"
        )
        return signals

    # ------------------------------------------------------------------
    # 已触发 monitor → 方向信号
    # ------------------------------------------------------------------

    def _generate_triggered_signals(self) -> list[Signal]:
        """为最近触发的 monitor 生成方向信号."""
        triggered = self.calendar.get_recently_triggered_monitors(
            lookback_days=self.config.lookback_days,
        )
        if not triggered:
            logger.debug("近期无触发的 monitor 事件")
            return []

        signals: list[Signal] = []
        now = datetime.now()

        for monitor in triggered:
            triggered_dt = self._parse_triggered_at(monitor.triggered_at)
            hours_ago = (
                (now - triggered_dt).total_seconds() / 3600
                if triggered_dt
                else self.config.lookback_days * 24
            )
            weight = self.config.compute_weight(hours_ago)

            if weight <= 0:
                continue

            trigger_result = monitor.trigger_result or "触发条件满足"
            direction = _infer_monitor_direction(trigger_result)
            strength = _infer_strength_from_weight(weight)

            dir_sign = {
                SignalDirection.BULLISH: 1.0,
                SignalDirection.BEARISH: -1.0,
                SignalDirection.NEUTRAL: 0.0,
            }
            score = dir_sign.get(direction, 0.0) * weight

            hours_desc = (
                f"{hours_ago:.0f}h前" if hours_ago < 72
                else f"{hours_ago / 24:.0f}天前"
            )

            description_parts = [
                f"{hours_desc}触发 | 权重{weight:.1f}",
            ]
            if monitor.trigger_condition:
                description_parts.append(f"条件: {monitor.trigger_condition}")
            description_parts.append(f"结果: {trigger_result[:120]}")
            if monitor.action_on_trigger:
                description_parts.append(f"建议: {monitor.action_on_trigger[:100]}")

            signals.append(
                Signal(
                    name=f"Monitor触发: {monitor.name}",
                    dimension="monitor",
                    direction=direction,
                    strength=strength,
                    score=score,
                    description=" | ".join(description_parts),
                    metadata={
                        "monitor_name": monitor.name,
                        "status": monitor.status,
                        "trigger_condition": monitor.trigger_condition,
                        "trigger_result": trigger_result,
                        "action_on_trigger": monitor.action_on_trigger,
                        "triggered_at": monitor.triggered_at,
                        "hours_ago": round(hours_ago, 1),
                        "recency_weight": weight,
                        "parent_analysis": monitor.parent_analysis,
                        "expires_at": monitor.expires_at,
                    },
                )
            )

        signals.sort(key=lambda s: s.metadata.get("hours_ago", 999))
        return signals

    # ------------------------------------------------------------------
    # 活跃 monitor → 中性观测提醒
    # ------------------------------------------------------------------

    def _generate_active_monitor_signals(self) -> list[Signal]:
        """为活跃 monitor 生成中性观测信号."""
        active = self.calendar.get_active_monitors()
        if not active:
            return []

        signals: list[Signal] = []
        for monitor in active:
            description_parts = ["正在观测中"]
            if monitor.trigger_condition:
                description_parts.append(f"条件: {monitor.trigger_condition}")
            if monitor.action_on_trigger:
                description_parts.append(f"触发后: {monitor.action_on_trigger[:100]}")

            signals.append(
                Signal(
                    name=f"Monitor观测: {monitor.name}",
                    dimension="monitor",
                    direction=SignalDirection.NEUTRAL,
                    strength=SignalStrength.WEAK,
                    score=0.0,
                    description=" | ".join(description_parts),
                    metadata={
                        "monitor_name": monitor.name,
                        "status": "active",
                        "trigger_condition": monitor.trigger_condition,
                        "action_on_trigger": monitor.action_on_trigger,
                        "check_frequency": monitor.check_frequency,
                        "expires_at": monitor.expires_at,
                        "parent_analysis": monitor.parent_analysis,
                    },
                )
            )

        return signals

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if not self.calendar.events:
            self.calendar.load_fixed_calendar()

    @staticmethod
    def _parse_triggered_at(triggered_at: str | None) -> datetime | None:
        """解析 ISO 时间字符串."""
        if triggered_at is None:
            return None
        try:
            return datetime.fromisoformat(triggered_at)
        except (ValueError, TypeError):
            return None
