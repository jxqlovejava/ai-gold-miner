#!/usr/bin/env python3
"""自适应金价监控 → Hermes cron 每2分钟触发 → stdout → 微信.

通过 Hermes cron --no-agent 模式运行: stdout 直接转发到微信.
无告警时静默退出 (exit 0, 空 stdout) → Hermes 不推送.
有告警时输出卡片 → Hermes 转发 stdout 到微信.
"""
import os, sys

os.chdir("/home/ubuntu/ai-gold-miner")
sys.path.insert(0, "/home/ubuntu/ai-gold-miner")
os.environ["PYTHONPATH"] = "src:" + os.environ.get("PYTHONPATH", "")
os.environ.setdefault("GOLD_MINER_ROOT", "/home/ubuntu/ai-gold-miner")
# 2026-08-12: 告知脚本 stdout 会被 Hermes cron 投递微信, 脚本内不再重复 hermes send (防双投递限流)
os.environ["GOLD_MONITOR_STDOUT_DELIVERY"] = "1"

_mp = "/home/ubuntu/ai-gold-miner/scripts/adaptive_gold_monitor.py"
code = open(_mp).read()
exec(compile(code, _mp, "exec"), {"__name__": "__main__", "__file__": _mp})
