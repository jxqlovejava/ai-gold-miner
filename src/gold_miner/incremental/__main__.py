"""python -m gold_miner.incremental — 增量判断引擎入口 (Hermes cron stdout 投递微信)."""
import sys

from .judge import main

if __name__ == "__main__":
    sys.exit(main())
