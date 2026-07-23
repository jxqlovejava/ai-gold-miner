#!/usr/bin/env python3
"""Evening event preview for Hermes cron --no-agent mode.

stdout is delivered to Weixin. Empty stdout = silent.
"""
import os, sys

os.chdir("/home/ubuntu/ai-gold-miner")
sys.path.insert(0, "/home/ubuntu/ai-gold-miner")
os.environ["PYTHONPATH"] = "src:" + os.environ.get("PYTHONPATH", "")
os.environ.setdefault("GOLD_MINER_ROOT", "/home/ubuntu/ai-gold-miner")

_mp = "/home/ubuntu/ai-gold-miner/scripts/evening_event_preview.py"
code = open(_mp).read()
exec(compile(code, _mp, "exec"), {"__name__": "__main__", "__file__": _mp})
