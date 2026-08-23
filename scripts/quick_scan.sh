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
#   ### 报告骨架                                        (全模式)
#   ### scan摘要 / ### 持仓 / ### 活跃条件单              (仅全量 bundle: RERUN / 未填充 REUSE)
#   =====BUNDLE_END=====
#   bundle 同步落盘 data/private/.last_bundle.txt (P8, 2026-08-23): 下一轮直接 Read 该文件,
#   免对超限持久化输出 grep/awk 定位抽取（省 1 轮推理 ≈1min）。含私密数据 → 必须 gitignored 路径。
#
# P6 (2026-08-22 提速): ①ASSEMBLE_SKIP 用轻量 bundle(仅骨架 — 已填充报告已含 portfolio/条件单/摘要结论, 省~170行≈4-5k token)
#                       ②python 单进程化: REUSE 补价+组装合一个 heredoc; digest 并入 assemble 全量(main 内写)
#                       ③REUSE SKIP 不刷 digest (scan_report 未变 → digest 是其确定性产物, 刷新=白耗一次冷启动)
#                         ※ RERUN+SKIP 仍刷 digest: scan_report 是新的, digest 必须跟踪
#
set -euo pipefail
cd "$(dirname "$0")/.."

REPORT="data/output/scan_report_$(date +%Y%m%d).md"
THRESHOLD=10800  # 3h 复用窗口
ANALYSIS="data/output/金价分析_$(date +%F).md"
STEPS="data/output/scan_steps_$(date +%F).md"
BUNDLE_OUT="data/private/.last_bundle.txt"  # P8: bundle 落盘(含 portfolio/条件单私密数据 → gitignored 路径)

# 步骤进度提取（2026-08-23 用户选定折中模式：步骤进度 + cat 直出报告）
# scan 日志(44KB+)不再直打终端(超限折叠用户看不到)，收进 .log 文件, 提取 [N/9]+⏱Step+关键结论行
emit_steps() {
  if [ -f "$STEPS" ]; then
    echo "### 步骤进度: $STEPS"
    cat "$STEPS"
    echo ""
  fi
}

run_assemble() {
  # RERUN 路径专用: scan_report 是新的 → digest 必须跟着刷新（SKIP 时也刷, 与骨架解耦）。
  # digest 已并入全量组装（main 内顺带写）, 全量时不再第二次冷启动；仅 SKIP 分支单独 --digest-only。
  # 已填充的当日报告不覆盖（无占位符 = LLM 已完成增量填充）；强制重建用 ASSEMBLE_FORCE=1
  if [ -f "$ANALYSIS" ] && [ -z "${ASSEMBLE_FORCE:-}" ]; then
    if ! grep -q "LLM 增量填充\|LLM 补充" "$ANALYSIS"; then
      python3 scripts/assemble_report.py --digest-only 2>/dev/null || true
      echo "ASSEMBLE_SKIP|${ANALYSIS}|当日报告已填充，不覆盖（强制重建: ASSEMBLE_FORCE=1）"
      return 0
    fi
  fi
  python3 scripts/assemble_report.py && echo "ASSEMBLE_OK|${ANALYSIS}"
}

emit_bundle() {
  # P5 单轮取数协议(2026-08-22): 脚本完成后把 LLM 所需全部数据 cat 到 stdout,
  # 模型一条前台 Bash 拿齐(骨架+摘要+portfolio+active条件单), 消灭后台通知驱动的中间推理轮。
  local day
  day=$(date +%F)
  {
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
  } | tee "$BUNDLE_OUT"
}

emit_bundle_light() {
  # P6: ASSEMBLE_SKIP 轻量 bundle — 已填充报告已含 portfolio/条件单/维度结论,
  # 只 cat 骨架(终端重发报告的唯一数据源), 省 ~170 行 ≈ 4-5k token/轮。
  local day
  day=$(date +%F)
  {
  echo "=====BUNDLE_START====="
  echo "### 报告骨架: data/output/金价分析_${day}.md (已填充, 轻量bundle)"
  cat "data/output/金价分析_${day}.md" 2>/dev/null || echo "(骨架不存在)"
  echo "=====BUNDLE_END====="
  } | tee "$BUNDLE_OUT"
}

if [ -z "${FORCE_SCAN:-}" ] && [ -f "$REPORT" ]; then
  MTIME=$(stat -f %m "$REPORT")
  AGE=$(( $(date +%s) - MTIME ))
  if [ "$AGE" -lt "$THRESHOLD" ]; then
    # 已填充判定提前（纯 grep, 零 python 冷启动）：SKIP 时只补价 + 轻量 bundle
    FILLED=0
    if [ -z "${ASSEMBLE_FORCE:-}" ] && [ -f "$ANALYSIS" ] && ! grep -q "LLM 增量填充\|LLM 补充" "$ANALYSIS"; then
      FILLED=1
    fi
    # 单 python 进程：补最新价 (+ 未填充时顺带 digest+组装+增量基准, 与 RERUN 同一 main)
    OUT=$(QS_FILLED="$FILLED" PYTHONPATH="src:scripts" python3 - <<'PYEOF' || printf 'LATEST_PRICE=N/A\nASSEMBLE_RC=1\n'
import contextlib, io, os, sys

# 1) 补最新价（REUSE 唯一网络动作；loguru/SSL 噪声收掉, stdout 只留结构化行）
try:
    with contextlib.redirect_stderr(io.StringIO()):
        from gold_miner.data.jd_accumulation_gold import JdAccumulationGoldFetcher as F
        p = F(bank="MS").fetch_price()
    print(f"LATEST_PRICE={p.price if p else 'N/A'}")
except Exception:
    print("LATEST_PRICE=N/A")

# 2) 未填充时才组装（已填充: scan_report 未变, 摘要/骨架是其确定性产物, 刷新=白耗冷启动）
if os.environ.get("QS_FILLED") != "1":
    try:
        import assemble_report as ar
        rc = ar.main([])   # 全量: digest + 骨架 + 增量基准刷新
        print(f"ASSEMBLE_RC={rc}")
    except Exception as e:
        print("ASSEMBLE_RC=1")
        print(f"⚠️ assemble 失败: {e}", file=sys.stderr)
PYEOF
)
    PRICE=$(printf '%s\n' "$OUT" | sed -n 's/^LATEST_PRICE=//p' | tail -n1)
    echo "REUSE_MODE|$REPORT|AGE=${AGE}s|LATEST_PRICE=${PRICE:-N/A}"
    emit_steps
    if [ "$FILLED" = "1" ]; then
      echo "ASSEMBLE_SKIP|${ANALYSIS}|当日报告已填充，不覆盖（强制重建: ASSEMBLE_FORCE=1）"
      emit_bundle_light
    else
      if printf '%s\n' "$OUT" | grep -q '^ASSEMBLE_RC=0'; then
        echo "ASSEMBLE_OK|${ANALYSIS}"
      else
        echo "ASSEMBLE_FAIL|${ANALYSIS}|assemble 异常, 骨架可能未更新"
      fi
      emit_bundle
    fi
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
# 2026-08-23: scan stdout(44KB+ 日志) 收进 .log 文件而非直打终端(超限折叠)，完成后提取步骤进度
SCANLOG="data/output/.scan_log_$(date +%Y%m%d).log"
gold-miner scan --days 30 --news --sentiment --report-file "$REPORT" > "$SCANLOG" 2>&1
# 提取步骤进度: [N/9]标题 + ⏱Step耗时 + 关键结论行(评分/校验/价格/事件/画像/未来事件)
grep -E '\[[1-9]/9\]|⏱ Step|综合评分:|跨维度不一致|事件同步\]|日历校验|官方日历比对|国内金价:|民生银行积存金|国际金价|实际利率最新|通胀预期最新|画像匹配:|未来14天' "$SCANLOG" \
  | sed -E 's/^[0-9]{2}:[0-9]{2}:[0-9]{2} \| INFO *\| //' > "$STEPS" || true
emit_steps
run_assemble
emit_bundle
