"""验证 CLI — verify 命令实现."""

import argparse
from datetime import datetime

from loguru import logger

from gold_miner.events.models import EventType
from gold_miner.events.resolver import AutoResolver
from gold_miner.events.store import EventStore
from gold_miner.verification.reporter import VerificationReporter


def run_verify(args: argparse.Namespace) -> None:
    """验证命令入口."""
    store = EventStore()

    # --report: 生成 Markdown 报告
    if args.report:
        reporter = VerificationReporter(store=store)
        path = reporter.generate_cycle_report()
        logger.info(f"验证报告已生成: {path}")
        return

    # --id <ID>: 查看详情
    if args.id:
        reporter = VerificationReporter(store=store)
        detail = reporter.generate_prediction_detail(args.id)
        print(detail)
        return

    # --confirm <ID>: 人工确认结算
    if args.confirm:
        state = store.get_state(args.confirm)
        if state is None:
            logger.error(f"未找到预测: {args.confirm}")
            return
        if state.settled:
            logger.warning(f"预测 {args.confirm[:8]}... 已结算，无需重复确认")
            return

        # 如果还没有观察到价格，先记录
        if state.observed_price is None:
            from gold_miner.data.spot_gold import SpotGoldFetcher
            gold_df = SpotGoldFetcher().fetch(days=5)
            if not gold_df.empty:
                current_price = float(gold_df["close"].iloc[-1])
                store.append(
                    EventType.PRICE_OBSERVED,
                    args.confirm,
                    {"observed_price": current_price},
                )
                state.observed_price = current_price

        actual_return = (
            (state.observed_price - state.current_price) / state.current_price
            if state.current_price > 0 and state.observed_price else 0.0
        )
        was_correct = _check_direction(state.direction, actual_return)

        # 检查 override
        overridden = False
        if args.override:
            was_correct = args.override == "correct"
            overridden = True

        store.append(
            EventType.PREDICTION_SETTLED,
            args.confirm,
            {
                "was_correct": was_correct,
                "actual_return": round(actual_return, 6),
                "settled_by": "human",
            },
        )

        notes = args.notes or ""
        if overridden:
            notes = f"[OVERRIDE: {args.override}] " + notes
        store.append(
            EventType.HUMAN_VERIFIED,
            args.confirm,
            {"verifier_notes": notes},
        )

        status = "✓ 正确" if was_correct else "✗ 错误"
        logger.info(
            f"人工确认: {args.confirm[:8]}... {status} "
            f"(收益: {actual_return:+.2%})"
        )
        return

    # --reject <ID>: 无效化预测
    if args.reject:
        store.append(
            EventType.PREDICTION_INVALIDATED,
            args.reject,
            {"reason": args.reason or "人工驳回"},
        )
        logger.info(f"预测 {args.reject[:8]}... 已无效化")
        return

    # 默认: 列出待确认列表
    resolver = AutoResolver(store=store)
    awaiting = resolver.get_awaiting()

    if not awaiting:
        print("暂无待人工确认的预测。")
        return

    print(f"{'ID':<14} {'创建':<10} {'方向':<8} {'窗口':<6} {'入场价':>10} {'现价':>10} {'来源'}")
    print("-" * 75)
    for a in awaiting:
        print(
            f"{a['id'][:12]:<14} "
            f"{a['created_at'][:10]:<10} "
            f"{a['direction']:<8} "
            f"{a['horizon_days']}天{'':<3}"
            f"{a['current_price']:>10.2f} "
            f"{a['observed_price'] or '-':>10} "
            f"{a['source']}"
        )

    print()
    print(f"共 {len(awaiting)} 条待确认。使用 gold-miner verify --confirm <ID> 确认。")
    print("使用 gold-miner verify --id <ID> 查看完整证据链。")


def _check_direction(direction: str, actual_return: float) -> bool:
    if direction in ("buy", "long"):
        return actual_return > 0
    if direction in ("sell", "short"):
        return actual_return < 0
    return abs(actual_return) < 0.01
