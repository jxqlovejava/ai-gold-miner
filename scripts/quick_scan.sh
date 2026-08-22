#!/bin/bash
# quick_scan.sh - 金价分析单轮取数命令：条件复用 + 补最新价 + 重跑 scan + 组装骨架/摘要 + bundle 输出 五合一
#
# 目标：一次金价分析 = 2 轮模型调用。本脚本前台跑（P5, 2026-08-22），stdout 即 LLM 全部输入：
#       模式行(REUSE/RERUN + LATEST_PRICE + ASSEMBLE_*) + bundle(骨架/摘要/portfolio/active条件单)。
#       旧后台+通知驱动模式已废弃（3 轮推理各 20-25s > 前台工具时间，单轮省 1 轮推理）。
#
# 用法（主对话第①轮，前台运行，非后台）：
#   Bash(前台): bash scripts/quick_scan.sh              # REUSE ~6s / RERUN ~15-30s
#   Bash(前台): FORCE_SCAN=1 bash scripts/quick_scan.sh # 强制重 scan（价格剧变/用户要最新）
#
# 输出结构（stdout）：
#   REUSE_MODE|data/output/scan_report_YYYYMMDD.md|AGE=Ns|LATEST_PRICE=997.10   (或 RERUN_MODE|...)
#   ASSEMBLE_OK|data/output/金价分析_YYYY-MM-DD.md   (或 ASSEMBLE_SKIP|... 当日报告已填充不覆盖)
#   =====BUNDLE_START=====
#   ### 报告骨架 / ### scan摘要 / ### 持仓 / ### 活跃条件单
#   =====BUNDLE_END=====
#
set -euo pipefail
cd "$(dirname "$0")/.."

REPORT="data/output/scan_report_$(date +%Y%m%d).md"
THRESHOLD=10800  # 3h 复用窗口
ANALYSIS="data/output/金价分析_$(date +%F).md"

run_assemble() {
  # scan 摘要(技术面/聪明钱明细)无条件刷新: ASSEMBLE_SKIP(当日报告已填充)时骨架不重建, 但摘要须更新。
  # REUSE 场景 LLM 只读 骨架+摘要 双文件, 不再读 420 行 scan_report 全文(2026-08-22 提速P4)
  python3 scripts/assemble_report.py --digest-only 2>/dev/null || true
  # 已填充的当日报告不覆盖（无占位符 = LLM 已完成增量填充）；
  # 强制重建用 ASSEMBLE_FORCE=1
  if [ -f "$ANALYSIS" ] && [ -z "${ASSEMBLE_FORCE:-}" ]; then
    if ! grep -q "LLM 增量填充\|LLM 补充" "$ANALYSIS"; then
      echo "ASSEMBLE_SKIP|${ANALYSIS}|当日报告已填充，不覆盖（强制重建: ASSEMBLE_FORCE=1）"
      return 0
    fi
  fi
  python3 scripts/assemble_report.py
}

emit_bundle() {
  # P5 单轮取数协议(2026-08-22): 脚本完成后把 LLM 所需全部数据 cat 到 stdout,
  # 模型一条前台 Bash 拿齐(骨架+摘要+portfolio+active条件单), 消灭后台通知驱动的中间推理轮。
  local day
  day=$(date +%F)
  echo "=====BUNDLE_START====="
  echo "### 报告骨架: data/output/金价分析_${day}.md"
  cat "data/output/金价分析_${day}.md" 2>/dev/null || echo "(骨架不存在)"
  echo ""
  echo "### scan摘要: data/output/scan_digest_${day}.md"
  cat "data/output/scan_digest_${day}.md" 2>/dev/null || echo "(摘要不存在)"
  echo ""
  echo "### 持仓: data/private/portfolio.yaml"
  cat data/private/portfolio.yaml 2>/dev/null || echo "(portfolio不存在)"
  echo ""
  echo "### 活跃条件单"
  grep '"status": "active"' data/private/conditional_orders.jsonl 2>/dev/null || echo "(无active条件单)"
  echo "=====BUNDLE_END====="
}

if [ -z "${FORCE_SCAN:-}" ] && [ -f "$REPORT" ]; then
  MTIME=$(stat -f %m "$REPORT")
  AGE=$(( $(date +%s) - MTIME ))
  if [ "$AGE" -lt "$THRESHOLD" ]; then
    # 复用模式：补一次单点最新价（秒级，JdAccumulationGoldFetcher 免登录）
    PRICE=$(PYTHONPATH=src python3 -c \
      "from gold_miner.data.jd_accumulation_gold import JdAccumulationGoldFetcher as F; p=F(bank='MS').fetch_price(); print(p.price if p else '')" \
      2>/dev/null || echo "")
    echo "REUSE_MODE|$REPORT|AGE=${AGE}s|LATEST_PRICE=${PRICE:-N/A}"
    run_assemble
    emit_bundle
    exit 0
  fi
fi
echo "RERUN_MODE|$REPORT|无新鲜报告(<3h)，前台执行scan（约15-25s）"
# 陈旧报告移开（.stale 后缀不匹配 scan_report_*.md glob，不会被 assemble_report 选中）：
# scan 现为原子写入（tmp + rename），报告落盘前并行 Read 得到干净的「文件不存在」，
# 而不是读到陈旧报告或半截文件（2026-08-22 事故修复）
if [ -f "$REPORT" ]; then
  mv "$REPORT" "${REPORT}.stale"
fi
# --report-file: scan 输出 tee 到 scan_report 文件 (P2 assemble_report.py 依赖最新 scan_report)
# 注意: 不用 exec -- scan 完成后还要接着组装骨架（set -e: scan 失败则中止，不组装）
gold-miner scan --days 30 --news --sentiment --report-file "$REPORT"
run_assemble
emit_bundle
