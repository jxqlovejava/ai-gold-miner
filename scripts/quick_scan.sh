#!/bin/bash
# quick_scan.sh - 金价分析启动批的第一条命令：条件复用 + 补最新价 + 重跑 scan + 组装报告骨架 四合一
#
# 目标：把「分析总耗时压到 ≤60s」+ 消灭模型中转轮次。scan 网络任务(14-25s)是数据源核心成本，
#       当日已有 <3h 的 scan 报告时直接复用 + 只补一次最新积存金价，跳过重 scan；
#       scan/reuse 完成后自动跑 assemble_report.py 组装报告骨架（2026-08-22 P3 串联，
#       省掉「模型生成 assemble 调用」的一整轮 ~15s）。
#
# 用法（主对话第①轮，run_in_background=true）：
#   Bash(background): scripts/quick_scan.sh
#
# 输出模式（.output 文件，供通知后校验）：
#   REUSE_MODE|data/output/scan_report_YYYYMMDD.md|AGE=Ns|LATEST_PRICE=997.10
#       -> 报告新鲜，直接读报告路径 + 用 LATEST_PRICE 补最新价，无需重 scan
#   RERUN_MODE|data/output/scan_report_YYYYMMDD.md|...
#       -> 报告缺失或 >3h，前台跑 scan（约15-25s），完成后任务通知
#   ASSEMBLE_OK|data/output/金价分析_YYYY-MM-DD.md（骨架已生成，LLM 只填 3 个推理板块）
#   ASSEMBLE_SKIP|data/output/金价分析_YYYY-MM-DD.md（当日报告已填充，不覆盖；
#       强制重建: ASSEMBLE_FORCE=1 bash scripts/quick_scan.sh 或手动跑 assemble_report.py）
#   副产物: data/output/scan_digest_YYYY-MM-DD.md（技术面/聪明钱明细摘要，供 LLM 推理，
#       配合骨架双文件模式替代 scan_report 全文读取）
#
# 配合铁律 7（2 轮工具调用）：第①轮发此脚本 + 全部静态读取；第②轮 Read 骨架直接填充输出。
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

if [ -f "$REPORT" ]; then
  MTIME=$(stat -f %m "$REPORT")
  AGE=$(( $(date +%s) - MTIME ))
  if [ "$AGE" -lt "$THRESHOLD" ]; then
    # 复用模式：补一次单点最新价（秒级，JdAccumulationGoldFetcher 免登录）
    PRICE=$(PYTHONPATH=src python3 -c \
      "from gold_miner.data.jd_accumulation_gold import JdAccumulationGoldFetcher as F; p=F(bank='MS').fetch_price(); print(p.price if p else '')" \
      2>/dev/null || echo "")
    echo "REUSE_MODE|$REPORT|AGE=${AGE}s|LATEST_PRICE=${PRICE:-N/A}"
    run_assemble
    exit 0
  fi
fi
echo "RERUN_MODE|$REPORT|无新鲜报告(<3h)，执行scan（约15-25s，完成后任务通知）"
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
