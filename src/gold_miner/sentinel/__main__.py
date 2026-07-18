# -*- coding: utf-8 -*-
"""黄金哨兵 — Hermes 入口.

Hermes 约定:
  - 无异动：stdout 为空，exit 0
  - 有异动：stdout 打印人话卡片，exit 0
  - 致命错误：stderr 打印，exit 1

频道 (--mode):
  alert     持仓监控 + 价格异动 (默认)
  price     仅报价快照
  orders    仅条件单检查
  calendar  仅日历提醒
  full      全部频道
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .engine import SentinelConfig, SentinelEngine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="黄金哨兵 / Hermes 微信推送")
    parser.add_argument("--mode", default="alert",
                        help="alert|price|orders|calendar|full")
    parser.add_argument("--portfolio", type=Path, default=None)
    parser.add_argument("--orders", type=Path, default=None)
    parser.add_argument("--calendar", type=Path, default=None)
    parser.add_argument("--state", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--force", action="store_true",
                        help="忽略冷却 (测试用)")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="调试 JSON 输出")
    parser.add_argument("--quiet-errors", action="store_true",
                        help="失败也静默")
    args = parser.parse_args(argv)

    # 配置加载优先级: CLI > 环境变量 > config 文件 > 默认
    config_path = args.config
    if config_path is None and os.environ.get("GOLD_MINER_CONFIG"):
        config_path = Path(os.environ["GOLD_MINER_CONFIG"])

    cfg_dict: dict = {}
    if config_path and config_path.exists():
        try:
            cfg_dict = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cfg_dict = {}

    root = Path(os.environ.get("GOLD_MINER_ROOT", cfg_dict.get("root", ".")))

    config = SentinelConfig(
        portfolio_path=args.portfolio or Path(
            os.environ.get("GOLD_MINER_PORTFOLIO",
                           cfg_dict.get("portfolio_path",
                                        str(root / "data/private/portfolio.yaml")))
        ),
        orders_path=args.orders or Path(
            os.environ.get("GOLD_MINER_ORDERS",
                           cfg_dict.get("orders_path",
                                        str(root / "data/private/conditional_orders.jsonl")))
        ),
        calendar_path=args.calendar or Path(
            os.environ.get("GOLD_MINER_CALENDAR",
                           cfg_dict.get("calendar_path",
                                        str(root / "data/calendar_events.jsonl")))
        ),
        state_path=args.state or Path(
            os.environ.get("GOLD_MINER_STATE",
                           cfg_dict.get("state_path",
                                        str(root / "data/sentinel_state.json")))
        ),
        force=bool(args.force),
    )

    mode = (args.mode or "alert").lower()

    try:
        engine = SentinelEngine(config)
        result = engine.run()
    except Exception as e:
        if not args.quiet_errors:
            print(f"❌ 黄金哨兵异常: {e}", file=sys.stderr)
        return 1

    # JSON 调试模式
    if args.as_json:
        print(json.dumps({
            "mode": mode,
            "silent": result.silent,
            "alerts": [
                {"level": a.level.value, "title": a.title, "detail": a.detail}
                for a in result.alerts
            ],
            "quotes": [
                {"symbol": q.symbol, "price": q.price, "change_pct": q.change_pct}
                for q in result.quotes
            ],
        }, ensure_ascii=False, indent=2))
        return 0

    # Hermes 模式: 静默 vs 推送
    message = result.message
    if not (message or "").strip():
        return 0

    print(message, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
