"""时间序列机构信号分析 — 检测投行目标价反转与言行不一."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from loguru import logger


@dataclass(frozen=True)
class FlipFlopResult:
    """投行目标价反转结果."""

    bank: str
    previous_target: float
    previous_date: datetime
    current_target: float
    current_date: datetime
    previous_direction: str
    current_direction: str
    spot_at_previous: float
    spot_at_current: float


@dataclass(frozen=True)
class WalkTalkMismatch:
    """言行不一匹配结果."""

    bank: str
    target_direction: str
    institution: str
    ticker: str
    position_change_pct: float
    quarter: str


class InstitutionalTimeSeriesAnalyzer:
    """分析历史机构数据，检测反转与言行不一."""

    def __init__(self, bank_target_history: list[dict[str, Any]]) -> None:
        self._bank_history = bank_target_history

    # ------------------------------------------------------------------
    # 投行目标价反转检测
    # ------------------------------------------------------------------

    def detect_target_flip_flops(
        self,
        window_days: int = 30,
        min_upside_threshold: float = 5.0,
    ) -> list[FlipFlopResult]:
        """检测同一投行在 window_days 内目标价方向反转."""
        records = self._parse_bank_history(self._bank_history)
        if not records:
            return []

        results: list[FlipFlopResult] = []
        cutoff = datetime.now() - timedelta(days=window_days)

        # 按银行分组，只保留窗口内记录
        by_bank: dict[str, list[dict[str, Any]]] = {}
        for r in records:
            if r["date"] < cutoff:
                continue
            by_bank.setdefault(r["bank"], []).append(r)

        for bank, items in by_bank.items():
            if len(items) < 2:
                continue

            # 按时间倒序，取最近两次
            items = sorted(items, key=lambda x: x["date"], reverse=True)
            current, previous = items[0], items[1]

            current_dir = self._direction_from_upside(
                current["upside_pct"], min_upside_threshold
            )
            previous_dir = self._direction_from_upside(
                previous["upside_pct"], min_upside_threshold
            )

            if current_dir == "neutral" or previous_dir == "neutral":
                continue
            if current_dir == previous_dir:
                continue

            results.append(FlipFlopResult(
                bank=bank,
                previous_target=previous["target_price"],
                previous_date=previous["date"],
                current_target=current["target_price"],
                current_date=current["date"],
                previous_direction=previous_dir,
                current_direction=current_dir,
                spot_at_previous=previous["current_spot"],
                spot_at_current=current["current_spot"],
            ))

        return results

    @staticmethod
    def _direction_from_upside(upside_pct: float, threshold: float) -> str:
        if upside_pct > threshold:
            return "bullish"
        if upside_pct < -threshold:
            return "bearish"
        return "neutral"

    @staticmethod
    def _parse_bank_history(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """解析并规范化历史目标价记录."""
        parsed: list[dict[str, Any]] = []
        for r in records:
            try:
                ts = r.get("timestamp", "")
                date = datetime.fromisoformat(ts) if ts else datetime.now()
                parsed.append({
                    "bank": r.get("bank", ""),
                    "target_price": float(r.get("target_price", 0)),
                    "current_spot": float(r.get("current_spot", 0)),
                    "upside_pct": float(r.get("upside_pct", 0)),
                    "date": date,
                })
            except (ValueError, TypeError) as e:
                logger.debug(f"解析 bank target 历史记录失败: {e}")
                continue
        return parsed

    # ------------------------------------------------------------------
    # 言行不一检测
    # ------------------------------------------------------------------

    def detect_walk_talk_mismatches(
        self,
        bank_targets: list[dict[str, Any]],
        holdings_13f: list[dict[str, Any]],
        sell_threshold: float = -0.10,
    ) -> list[WalkTalkMismatch]:
        """检测投行口头看涨但关联机构 13F 减持."""
        mismatches: list[WalkTalkMismatch] = []

        bullish_banks = [
            bt for bt in bank_targets
            if bt.get("direction", "").lower() == "bullish"
        ]
        if not bullish_banks:
            return mismatches

        for holding in holdings_13f:
            institution = holding.get("institution", "")
            change_pct = holding.get("position_change_pct", 0)
            if change_pct >= sell_threshold:
                continue

            for bt in bullish_banks:
                bank = bt.get("bank", "")
                if not bank:
                    continue
                if not InstitutionalTimeSeriesAnalyzer._name_overlap(bank, institution):
                    continue

                mismatches.append(WalkTalkMismatch(
                    bank=bank,
                    target_direction="bullish",
                    institution=institution,
                    ticker=holding.get("ticker", ""),
                    position_change_pct=float(change_pct),
                    quarter=holding.get("quarter", ""),
                ))

        return mismatches

    @staticmethod
    def _name_overlap(a: str, b: str) -> bool:
        """简单名称重叠检测."""
        a_tokens = set(a.lower().split())
        b_tokens = set(b.lower().split())
        return bool(a_tokens & b_tokens)
