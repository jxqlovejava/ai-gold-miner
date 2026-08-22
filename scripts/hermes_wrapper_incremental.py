#!/usr/bin/env python3
"""金价增量判断 → Hermes cron --no-agent 模式, stdout 投递微信.

stdout 为空 = 静默 (无新信号 / 判断无实质变化), Hermes 不推送.
有实质增量判断 (新事件强化/反向) 时输出 "⚡ 金价增量判断" 卡片 → 微信.
"""
import os, sys

os.chdir("/home/ubuntu/ai-gold-miner")
sys.path.insert(0, "/home/ubuntu/ai-gold-miner")
# PYTHONPATH 环境变量只在解释器启动时生效, 运行中修改不影响 sys.path — 必须显式注入 <root>/src
sys.path.insert(0, "/home/ubuntu/ai-gold-miner/src")
os.environ["PYTHONPATH"] = "src:" + os.environ.get("PYTHONPATH", "")
os.environ.setdefault("GOLD_MINER_ROOT", "/home/ubuntu/ai-gold-miner")

from gold_miner.incremental.judge import run_incremental

card = run_incremental()
if card:
    print(card, flush=True)
