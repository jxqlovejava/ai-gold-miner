#!/usr/bin/env bash
# 部署黄金哨兵到 Hermes 服务器
# 用法: bash scripts/deploy_gold_miner_to_hermes.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PEM="${HERMES_PEM:-}"
HOST="${HERMES_HOST:-}"

# 优先读环境变量，否则从 data/private/hermes_config.sh 读取
if [ -z "$PEM" ] || [ -z "$HOST" ]; then
  CONFIG="$ROOT/data/private/hermes_config.sh"
  if [ -f "$CONFIG" ]; then
    source "$CONFIG"
    PEM="${PEM:-${HERMES_PEM:-}}"
    HOST="${HOST:-${HERMES_HOST:-}}"
  fi
fi

if [ -z "$PEM" ] || [ -z "$HOST" ]; then
  echo "❌ 请设置 HERMES_PEM 和 HERMES_HOST 环境变量，或在 data/private/hermes_config.sh 中配置"
  exit 1
fi
REMOTE_ROOT="${HERMES_GOLD_ROOT:-/home/ubuntu/ai-gold-miner}"
REMOTE_PORTFOLIO="${HERMES_GOLD_PORTFOLIO:-/home/ubuntu/.hermes/gold/portfolio.yaml}"
REMOTE_ORDERS="${HERMES_GOLD_ORDERS:-/home/ubuntu/.hermes/gold/conditional_orders.jsonl}"
REMOTE_CALENDAR="${HERMES_GOLD_CALENDAR:-/home/ubuntu/.hermes/gold/calendar_events.jsonl}"
REMOTE_STATE="${HERMES_GOLD_STATE:-/home/ubuntu/.hermes/gold/sentinel_state.json}"
REMOTE_SURGE_STATE="${HERMES_GOLD_SURGE_STATE:-/home/ubuntu/.hermes/gold/surge_monitor_state.json}"
REMOTE_CFG="${HERMES_GOLD_CFG:-/home/ubuntu/.hermes/gold/sentinel_config.json}"

if [[ ! -f "$PEM" ]]; then
  echo "缺少 SSH 密钥: $PEM"
  exit 1
fi

SSH=(ssh -i "$PEM" -o StrictHostKeyChecking=no)
SCP=(scp -i "$PEM" -o StrictHostKeyChecking=no)

echo "==> 创建远程目录"
"${SSH[@]}" "$HOST" "mkdir -p '$REMOTE_ROOT/src/gold_miner' '$REMOTE_ROOT/scripts' '$REMOTE_ROOT/.claude/skills/jdgold/scripts' '$(dirname "$REMOTE_PORTFOLIO")' '$(dirname "$REMOTE_SURGE_STATE")'"

echo "==> 同步 gold_miner 代码 (rsync --delay-updates 原子, 含 sentinel/data/signals 等)"
if command -v rsync >/dev/null 2>&1; then
  # --delay-updates: 全部暂存后统一改名, 避免 scp 逐文件覆盖造成的新旧代码混合窗口
  # (曾因该窗口导致夜间哨兵报 'Settings' has no attribute 'news_llm_categories')
  rsync -a --delete --delay-updates \
    -e "ssh -i '$PEM' -o StrictHostKeyChecking=no" \
    "$ROOT/src/gold_miner/" \
    "$HOST:$REMOTE_ROOT/src/gold_miner/"
else
  "${SCP[@]}" -r "$ROOT/src/gold_miner" "$HOST:$REMOTE_ROOT/src/"
fi
# 确保包可导入
"${SSH[@]}" "$HOST" "touch '$REMOTE_ROOT/src/gold_miner/__init__.py' 2>/dev/null || true"
"${SSH[@]}" "$HOST" "touch '$REMOTE_ROOT/src/__init__.py' 2>/dev/null || true"

echo "==> 同步 jdgold skill 免登录脚本 (数据层主源, gold_miner.data.jdgold_client 依赖)"
JDGOLD_SCRIPTS="$ROOT/.claude/skills/jdgold/scripts"
if [[ -d "$JDGOLD_SCRIPTS" ]]; then
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete --delay-updates \
      -e "ssh -i '$PEM' -o StrictHostKeyChecking=no" \
      "$JDGOLD_SCRIPTS/" \
      "$HOST:$REMOTE_ROOT/.claude/skills/jdgold/scripts/"
  else
    "${SSH[@]}" "$HOST" "mkdir -p '$REMOTE_ROOT/.claude/skills/jdgold/scripts'"
    "${SCP[@]}" -r "$JDGOLD_SCRIPTS/." "$HOST:$REMOTE_ROOT/.claude/skills/jdgold/scripts/"
  fi
  echo "  ✅ jdgold skill scripts 已同步 (query_gold_analysis/jdjr_query_*/query_blogger_trend 等)"
else
  echo "  ⚠️ 本地无 .claude/skills/jdgold/scripts, jdgold 主源不生效, 监控将落 H5 兜底"
fi

"${SCP[@]}" \
  "$ROOT/scripts/hermes_gold_miner_config.json" \
  "$HOST:$REMOTE_ROOT/scripts/"

# 同步 .env (DeepSeek key 等), 供语义推理层 (AI 判定传导链) 在服务器端使用
if [[ -f "$ROOT/.env" ]]; then
  "${SCP[@]}" "$ROOT/.env" "$HOST:$REMOTE_ROOT/.env"
  echo "  ✅ .env (含 LLM_API_KEY, 突发新闻语义层)"
else
  echo "  ⚠️ 本地无 .env, 语义层在服务器端将回退关键词规则"
fi

echo "==> 同步持仓数据"
if [[ -f "$ROOT/data/private/portfolio.yaml" ]]; then
  "${SCP[@]}" "$ROOT/data/private/portfolio.yaml" "$HOST:$REMOTE_PORTFOLIO"
  "${SSH[@]}" "$HOST" "mkdir -p '$REMOTE_ROOT/data/private'"
  "${SCP[@]}" "$ROOT/data/private/portfolio.yaml" "$HOST:$REMOTE_ROOT/data/private/portfolio.yaml"
else
  echo "  本地无 data/private/portfolio.yaml，跳过"
fi
if [[ -f "$ROOT/data/private/conditional_orders.jsonl" ]]; then
  "${SCP[@]}" "$ROOT/data/private/conditional_orders.jsonl" "$HOST:$REMOTE_ORDERS"
  "${SCP[@]}" "$ROOT/data/private/conditional_orders.jsonl" "$HOST:$REMOTE_ROOT/data/private/conditional_orders.jsonl"
else
  echo "  本地无 data/private/conditional_orders.jsonl，跳过"
fi
if [[ -f "$ROOT/data/calendar_events.jsonl" ]]; then
  # 双路径同步: .hermes/gold/ (Hermes哨兵) + 项目 data/ (EventCalendar 默认读取)
  "${SCP[@]}" "$ROOT/data/calendar_events.jsonl" "$HOST:$REMOTE_CALENDAR"
  "${SCP[@]}" "$ROOT/data/calendar_events.jsonl" "$HOST:$REMOTE_ROOT/data/calendar_events.jsonl"
else
  echo "  本地无 data/calendar_events.jsonl，跳过"
fi
# 信号快照 (本地 pipeline 产出 → 服务器监控理由引擎读取)
if [[ -f "$ROOT/data/signal_snapshot.json" ]]; then
  "${SCP[@]}" "$ROOT/data/signal_snapshot.json" "$HOST:$REMOTE_ROOT/data/signal_snapshot.json"
else
  echo "  本地无 data/signal_snapshot.json，跳过 (下次 pipeline 运行后产生)"
fi

echo "==> 写入默认配置"
"${SSH[@]}" "$HOST" "test -f '$REMOTE_CFG' || cp '$REMOTE_ROOT/scripts/hermes_gold_miner_config.json' '$REMOTE_CFG'"

echo "==> 安装薄包装到 ~/.hermes/scripts"
"${SSH[@]}" "$HOST" "cat > /home/ubuntu/.hermes/scripts/gold_sentinel.py <<'EOF'
#!/usr/bin/env python3
import os, sys, json
from pathlib import Path
os.environ.setdefault('GOLD_MINER_ROOT', '${REMOTE_ROOT}')
os.environ.setdefault('GOLD_MINER_PORTFOLIO', '${REMOTE_PORTFOLIO}')
os.environ.setdefault('GOLD_MINER_ORDERS', '${REMOTE_ORDERS}')
os.environ.setdefault('GOLD_MINER_CALENDAR', '${REMOTE_CALENDAR}')
os.environ.setdefault('GOLD_MINER_STATE', '${REMOTE_STATE}')
os.environ.setdefault('GOLD_MINER_CONFIG', '${REMOTE_CFG}')
args = sys.argv[1:]
if '--config' not in args:
    args = ['--config', os.environ['GOLD_MINER_CONFIG']] + args
sys.argv = [sys.argv[0]] + args
sys.path.insert(0, os.environ['GOLD_MINER_ROOT'])
# 自包含: 同时注入 <root>/src, 使内部 'from gold_miner.x' 导入无需外部 PYTHONPATH
sys.path.insert(0, os.path.join(os.environ['GOLD_MINER_ROOT'], 'src'))
from src.gold_miner.sentinel.__main__ import main
raise SystemExit(main())
EOF
chmod +x /home/ubuntu/.hermes/scripts/gold_sentinel.py"

# 分频道薄入口
for mode in alert price orders calendar news full briefing weekly; do
  "${SSH[@]}" "$HOST" "cat > /home/ubuntu/.hermes/scripts/gold_${mode}.py <<EOFMODE
#!/usr/bin/env python3
import os, sys
from pathlib import Path
sys.argv = [sys.argv[0], '--mode', '${mode}'] + [a for a in sys.argv[1:] if a != '--mode']
main_py = Path('/home/ubuntu/.hermes/scripts/gold_sentinel.py')
code = main_py.read_text(encoding='utf-8')
exec(compile(code, str(main_py), 'exec'), {'__name__': '__main__'})
EOFMODE
chmod +x /home/ubuntu/.hermes/scripts/gold_${mode}.py"
done

echo ""
echo "==> 试跑 gold_sentinel.py (alert 模式)"
"${SSH[@]}" "$HOST" "python3 /home/ubuntu/.hermes/scripts/gold_sentinel.py --mode alert 2>&1 | head -30" || true

echo ""
echo "==> 试跑 gold_price.py (price 模式)"
"${SSH[@]}" "$HOST" "python3 /home/ubuntu/.hermes/scripts/gold_price.py 2>&1 | head -15" || true

echo ""
echo "==> 部署监控脚本"
for script in adaptive_gold_monitor.py gold_plan_alert.py overnight_news_scanner.py evening_event_preview.py price_surge_monitor.py profit_protection_monitor.py gold_stop_level_alert.py hermes_delivery_watchdog.py; do
    if [[ -f "$ROOT/scripts/$script" ]]; then
        "${SCP[@]}" "$ROOT/scripts/$script" "$HOST:$REMOTE_ROOT/scripts/"
        echo "  ✅ scripts/$script"
    else
        echo "  ⚠️ scripts/$script 不存在, 跳过"
    fi
done

echo "==> 部署 Hermes cron 包装器 (${ROOT}/scripts -> ~/.hermes/scripts)"
if [[ -f "$ROOT/scripts/hermes_wrapper_adaptive.py" ]]; then
    "${SCP[@]}" "$ROOT/scripts/hermes_wrapper_adaptive.py" "$HOST:/home/ubuntu/.hermes/scripts/gold_adaptive_monitor.py"
    "${SSH[@]}" "$HOST" "chmod +x /home/ubuntu/.hermes/scripts/gold_adaptive_monitor.py"
    echo "  ✅ gold_adaptive_monitor.py"
fi
if [[ -f "$ROOT/scripts/hermes_wrapper_evening.py" ]]; then
    "${SCP[@]}" "$ROOT/scripts/hermes_wrapper_evening.py" "$HOST:/home/ubuntu/.hermes/scripts/gold_evening_preview.py"
    "${SSH[@]}" "$HOST" "chmod +x /home/ubuntu/.hermes/scripts/gold_evening_preview.py"
    echo "  ✅ gold_evening_preview.py"
fi
if [[ -f "$ROOT/scripts/hermes_wrapper_overnight.py" ]]; then
    "${SCP[@]}" "$ROOT/scripts/hermes_wrapper_overnight.py" "$HOST:/home/ubuntu/.hermes/scripts/gold_overnight_news.py"
    "${SSH[@]}" "$HOST" "chmod +x /home/ubuntu/.hermes/scripts/gold_overnight_news.py"
    echo "  ✅ gold_overnight_news.py (timeout 240s + 超时优雅降级)"
fi

echo "==> 部署 crontab 配置文件"
"${SCP[@]}" "$ROOT/scripts/hermes_crontab.txt" "$HOST:$REMOTE_ROOT/scripts/"

echo "==> 同步黄金 crontab 条目到实际 crontab (追加方式, 保留白泽等其他任务)"
# 根因: 本地 hermes_crontab.txt 是真相源, 此前仅 scp 到 scripts/ 不安装,
#   本地新增条目后服务器 crontab 漂移 (事故: 11:35 watchdog 服务器缺条目).
# sync_gold_crontab.py 在服务器端对比 crontab -l, 缺失则追加 (只增不删).
if [[ -f "$ROOT/scripts/sync_gold_crontab.py" ]]; then
    "${SCP[@]}" "$ROOT/scripts/sync_gold_crontab.py" "$HOST:$REMOTE_ROOT/scripts/"
    "${SSH[@]}" "$HOST" "python3 $REMOTE_ROOT/scripts/sync_gold_crontab.py" || echo "  ⚠️ crontab 同步失败, 请手动核对"
else
    echo "  ⚠️ 无 scripts/sync_gold_crontab.py, 跳过 crontab 自动同步 (本地源文件变更需手动追加)"
fi

echo "==> 创建日志目录"
"${SSH[@]}" "$HOST" "mkdir -p '$REMOTE_ROOT/logs'"

echo ""
echo "✅ 黄金哨兵 + 定时任务部署完成"
echo "持仓: $REMOTE_PORTFOLIO"
echo "条件单: $REMOTE_ORDERS"
echo "日历: $REMOTE_CALENDAR"
echo "定时脚本: $REMOTE_ROOT/scripts/"
echo ""
echo "━━━━ 安装 crontab (在 Hermes 上执行) ━━━━"
echo "  ssh -i ~/Documents/hermes.pem $HOST"
echo "  cd /home/ubuntu/ai-gold-miner"
echo "  crontab scripts/hermes_crontab.txt"
echo ""
echo "━━━━ Hermes 定时任务清单 ━━━━"
echo "  1) 🛡️ 自适应监控  * * * * *           → adaptive_gold_monitor.py (主监控)"
echo "  2) 🌅 盘前扫描    30 7 * * 1-5         → overnight_news_scanner.py"
echo "  3) 🚨 价格异动    */2 9-23 * * 1-5    → price_surge_monitor.py (辅助)"
echo "  4) 盘中监控       */15 9-23 * * 1-5   → gold_alert.py"
echo "  5) 报价快照       0 10,14,20 * * 1-5  → gold_price.py"
echo "  6) 日历提醒       0 8 * * 1-5          → gold_calendar.py"
echo ""
echo "━━━━ 手动测试新脚本 ━━━━"
echo "  ssh -i ~/Documents/hermes.pem $HOST"
echo "  cd /home/ubuntu/ai-gold-miner"
echo "  PYTHONPATH=src python3 scripts/adaptive_gold_monitor.py"
echo "  PYTHONPATH=src python3 scripts/price_surge_monitor.py"
echo "  PYTHONPATH=src python3 scripts/overnight_news_scanner.py"
echo ""
echo "同步数据:"
echo "  scp -i ~/Documents/hermes.pem data/private/portfolio.yaml $HOST:$REMOTE_PORTFOLIO"
echo "  scp -i ~/Documents/hermes.pem data/private/conditional_orders.jsonl $HOST:$REMOTE_ORDERS"
echo "  scp -i ~/Documents/hermes.pem data/calendar_events.jsonl $HOST:$REMOTE_CALENDAR"
