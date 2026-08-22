#!/usr/bin/env python3
"""GLD 持仓盘前预热 (cron): 强制下载 SPDR 最新持仓入库 + 回填双层缓存.

效果: scan 时 GLD 走 DB/缓存命中 ~0s, 把 SPDR Excel 多层降级下载 (~2-8s, 最坏 25s×4)
移出分析关键路径。GLD 持仓日频, 美股每个交易日收盘后发布, 周二~周六 08:40(北京)
各拉一次即可全覆盖。

crontab:
  40 8 * * 2-6 cd <repo> && PYTHONPATH=src /opt/homebrew/bin/python3 scripts/preheat_gld_holdings.py >> logs/gld_preheat.log 2>&1
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from loguru import logger

from gold_miner.data.gld_holdings import GldHoldingsFetcher


def main() -> int:
    df = GldHoldingsFetcher().fetch(force_refresh=True)
    if df.empty:
        logger.warning("GLD 预热失败: 下载与 DB 均不可用")
        return 1
    latest = df.iloc[-1]
    logger.info(f"GLD 预热完成: {latest['timestamp'].date()} {latest['value']:.2f} 吨 ({len(df)} 行)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
