"""AutoResolver — 自动结算到期预测."""

from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from gold_miner.config import settings
from gold_miner.data.spot_gold import SpotGoldFetcher
from gold_miner.events.models import EventType
from gold_miner.events.store import EventStore


class AutoResolver:
    """自动结算器.

    规则:
    - auto_resolve + horizon <= 7天: 自动追加 prediction_settled
    - auto_resolve + horizon > 7天: 仅追加 price_observed，待人工确认
    - auto_resolve = false: 跳过
    """

    SHORT_TERM_DAYS = 7

    def __init__(
        self,
        store: EventStore | None = None,
    ) -> None:
        self.store = store or EventStore()

    def resolve_due(self) -> dict[str, Any]:
        """结算所有到期预测."""
        pending_ids = self.store.pending_settlement()
        if not pending_ids:
            logger.debug("无到期预测待结算")
            return {"auto_settled": [], "awaiting_verification": [], "errors": []}

        # 获取当前价格
        try:
            gold_fetcher = SpotGoldFetcher()
            gold_df = gold_fetcher.fetch(days=5)
            if gold_df.empty:
                logger.warning("无法获取当前金价，跳过自动结算")
                return {"auto_settled": [], "awaiting_verification": [], "errors": []}
            current_price = float(gold_df["close"].iloc[-1])
        except Exception as e:
            logger.error(f"获取金价失败: {e}")
            return {"auto_settled": [], "awaiting_verification": [], "errors": [str(e)]}

        auto_settled: list[str] = []
        awaiting: list[str] = []
        errors: list[str] = []

        for pid in pending_ids:
            try:
                state = self.store.get_state(pid)
                if state is None:
                    continue

                # 追加 price_observed
                self.store.append(
                    EventType.PRICE_OBSERVED,
                    pid,
                    {"observed_price": current_price, "observed_at": datetime.now().isoformat()},
                )

                if state.horizon_days <= self.SHORT_TERM_DAYS:
                    # 自动结算
                    actual_return = (
                        (current_price - state.current_price) / state.current_price
                        if state.current_price > 0 else 0.0
                    )
                    was_correct = _determine_correctness(state.direction, actual_return)

                    self.store.append(
                        EventType.PREDICTION_SETTLED,
                        pid,
                        {
                            "was_correct": was_correct,
                            "actual_return": round(actual_return, 6),
                            "settled_by": "auto",
                        },
                    )
                    auto_settled.append(pid)
                    logger.info(
                        f"自动结算: {pid[:8]}... {state.direction} "
                        f"{'✓' if was_correct else '✗'} "
                        f"(收益: {actual_return:+.2%})"
                    )
                else:
                    awaiting.append(pid)
                    logger.info(
                        f"等待人工确认: {pid[:8]}... "
                        f"({state.horizon_days}天, 方向: {state.direction})"
                    )

            except Exception as e:
                logger.error(f"结算预测 {pid[:8]}... 失败: {e}")
                errors.append(str(e))

        return {
            "auto_settled": auto_settled,
            "awaiting_verification": awaiting,
            "errors": errors,
            "current_price": current_price,
        }

    def get_awaiting(self) -> list[dict[str, Any]]:
        """获取待人工确认的预测摘要."""
        pending_ids = self.store.pending_verification()
        result: list[dict[str, Any]] = []
        for pid in pending_ids:
            state = self.store.get_state(pid)
            if state is None:
                continue
            result.append({
                "id": pid,
                "direction": state.direction,
                "horizon_days": state.horizon_days,
                "created_at": state.created_at.isoformat() if state.created_at else "",
                "current_price": state.current_price,
                "observed_price": state.observed_price,
                "source": state.source,
                "composite_score": state.composite_score,
                "evidence_count": len(state.evidence_snapshots),
            })
        return sorted(result, key=lambda x: x["created_at"], reverse=True)


def _determine_correctness(direction: str, actual_return: float) -> bool:
    if direction == "buy" or direction == "long":
        return actual_return > 0
    if direction == "sell" or direction == "short":
        return actual_return < 0
    return abs(actual_return) < 0.01
