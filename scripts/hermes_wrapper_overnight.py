#!/usr/bin/env python3
"""盘前新闻扫描 → stdout → Hermes gateway 推送

部署: deploy_gold_miner_to_hermes.sh 将本文件 scp 为 ~/.hermes/scripts/gold_overnight_news.py
历史: 曾用 timeout=90 硬超时 + 无 TimeoutExpired 捕获, 导致 anysearch/LLM 瞬时慢时
      cron 静默失败("provider timeout, fallback chain exhausted"). 2026-08-20 加固:
      - timeout 90→240s (新闻多主题串行 + LLM 判断需 1-3 分钟)
      - 捕获 TimeoutExpired: 超时也推送已产出的部分 stdout, 避免整段丢失
"""
import subprocess, sys, os

ROOT = "/home/ubuntu/ai-gold-miner"
env = os.environ.copy()
env["PYTHONPATH"] = f"{ROOT}/src:" + env.get("PYTHONPATH", "")
script = f"{ROOT}/scripts/overnight_news_scanner.py"

try:
    result = subprocess.run(
        [sys.executable, script],
        capture_output=True, text=True, timeout=240,
        cwd=ROOT, env=env,
    )
except subprocess.TimeoutExpired as e:
    # 超时: 推送已产出的部分 stdout (部分结果优于静默丢失), stderr 提示超时
    partial = e.stdout or ""
    if partial.strip():
        print(partial.strip(), flush=True)
    print("⚠️ 扫描超时(240s), 已推送部分结果", file=sys.stderr, flush=True)
    raise SystemExit(1)

output = (result.stdout or "").strip()
if result.stderr:
    print(result.stderr.strip(), file=sys.stderr, flush=True)
if output:
    print(output, flush=True)
