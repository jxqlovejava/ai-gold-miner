#!/usr/bin/env python3
"""黄金预测自检 · 周复盘 → Hermes cron --no-agent 模式, stdout 投递微信.

服务器自主运行 (不依赖用户触发), 每周输出预测健康卡片: 命中率/方向分布/校准/Brier/警告.
"""
import os, sys

ROOT = "/home/ubuntu/ai-gold-miner"
os.chdir(ROOT)
sys.path.insert(0, ROOT)
sys.path.insert(0, f"{ROOT}/src")
os.environ.setdefault("GOLD_MINER_ROOT", ROOT)

_main = f"{ROOT}/scripts/weekly_prediction_review.py"
code = open(_main, encoding="utf-8").read()
exec(compile(code, _main, "exec"), {"__name__": "__main__", "__file__": _main})
