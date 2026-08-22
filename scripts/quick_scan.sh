#!/bin/bash
# quick_scan.sh — 金价分析启动批的第一条命令：条件复用 + 补最新价 + 重跑 scan 三合一
#
# 目标：把「分析总耗时压到 ≤60s」。scan 网络任务(14-25s)是数据源核心成本，
#       当日已有 <3h 的 scan 报告时直接复用 + 只补一次最新积存金价，跳过重 scan。
#
# 用法（主对话第①轮，run_in_background=true）：
#   Bash(background): scripts/quick_scan.sh
#
# 输出两种模式：
#   REUSE_MODE|data/output/scan_report_YYYYMMDD.md|AGE=Ns|LATEST_PRICE=997.10
#       → 报告新鲜，直接读报告路径 + 用 LATEST_PRICE 补最新价，无需重 scan
#   RERUN_MODE|data/output/scan_report_YYYYMMDD.md|...
#       → 报告缺失或 >3h，exec 前台跑 scan（约15-25s），完成后任务通知
#
# 配合铁律 7（2 轮工具调用）：第①轮发此脚本 + 全部静态读取；第②轮读报告输出。
set -euo pipefail
cd "$(dirname "$0")/.."

REPORT="data/output/scan_report_$(date +%Y%m%d).md"
THRESHOLD=10800  # 3h 复用窗口

if [ -f "$REPORT" ]; then
  MTIME=$(stat -f %m "$REPORT")
  AGE=$(( $(date +%s) - MTIME ))
  if [ "$AGE" -lt "$THRESHOLD" ]; then
    # 复用模式：补一次单点最新价（秒级，JdAccumulationGoldFetcher 免登录）
    PRICE=$(PYTHONPATH=src python3 -c \
      "from gold_miner.data.jd_accumulation_gold import JdAccumulationGoldFetcher as F; p=F(bank='MS').fetch_price(); print(p.price if p else '')" \
      2>/dev/null || echo "")
    echo "REUSE_MODE|$REPORT|AGE=${AGE}s|LATEST_PRICE=${PRICE:-N/A}"
    exit 0
  fi
fi
echo "RERUN_MODE|$REPORT|无新鲜报告(<3h)，执行scan（约15-25s，完成后任务通知）"
# --report-file: scan 输出 tee 到 scan_report 文件 (P2 assemble_report.py 依赖最新 scan_report)
exec gold-miner scan --days 30 --news --sentiment --report-file "$REPORT"
