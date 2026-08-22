#!/usr/bin/env python3
"""系统自评 · 周复盘 → Hermes cron --no-agent 模式, stdout 投递微信.

服务器自主运行 (不依赖用户触发 Claude Code), 每周日晚输出系统自评卡片:
预测准确率 / 推送健康度 / 增量基准 / 持仓. 问题#4 反思闭环的可见化.
"""
import os, sys

ROOT = "/home/ubuntu/ai-gold-miner"
os.chdir(ROOT)
sys.path.insert(0, ROOT)
sys.path.insert(0, f"{ROOT}/src")
os.environ.setdefault("GOLD_MINER_ROOT", ROOT)

_main = f"{ROOT}/scripts/gold_self_review.py"
code = open(_main, encoding="utf-8").read()
exec(compile(code, _main, "exec"), {"__name__": "__main__", "__file__": _main})
