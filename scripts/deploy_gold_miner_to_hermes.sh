#!/usr/bin/env bash
# 部署黄金哨兵到 Hermes 服务器
# 用法: bash scripts/deploy_gold_miner_to_hermes.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PEM="${HERMES_PEM:-$HOME/Documents/hermes.pem}"
HOST="${HERMES_HOST:-ubuntu@124.220.236.129}"
REMOTE_ROOT="${HERMES_GOLD_ROOT:-/home/ubuntu/ai-gold-miner}"
REMOTE_PORTFOLIO="${HERMES_GOLD_PORTFOLIO:-/home/ubuntu/.hermes/gold/portfolio.yaml}"
REMOTE_ORDERS="${HERMES_GOLD_ORDERS:-/home/ubuntu/.hermes/gold/conditional_orders.jsonl}"
REMOTE_CALENDAR="${HERMES_GOLD_CALENDAR:-/home/ubuntu/.hermes/gold/calendar_events.jsonl}"
REMOTE_STATE="${HERMES_GOLD_STATE:-/home/ubuntu/.hermes/gold/sentinel_state.json}"
REMOTE_CFG="${HERMES_GOLD_CFG:-/home/ubuntu/.hermes/gold/sentinel_config.json}"

if [[ ! -f "$PEM" ]]; then
  echo "缺少 SSH 密钥: $PEM"
  exit 1
fi

SSH=(ssh -i "$PEM" -o StrictHostKeyChecking=no)
SCP=(scp -i "$PEM" -o StrictHostKeyChecking=no)

echo "==> 创建远程目录"
"${SSH[@]}" "$HOST" "mkdir -p '$REMOTE_ROOT/src/gold_miner/sentinel' '$REMOTE_ROOT/scripts' '$(dirname "$REMOTE_PORTFOLIO")'"

echo "==> 同步哨兵代码"
"${SCP[@]}" -r \
  "$ROOT/src/gold_miner/sentinel" \
  "$HOST:$REMOTE_ROOT/src/gold_miner/"
# 确保包可导入
"${SSH[@]}" "$HOST" "touch '$REMOTE_ROOT/src/gold_miner/__init__.py' 2>/dev/null || true"
"${SSH[@]}" "$HOST" "touch '$REMOTE_ROOT/src/__init__.py' 2>/dev/null || true"

"${SCP[@]}" \
  "$ROOT/scripts/hermes_gold_miner_config.json" \
  "$HOST:$REMOTE_ROOT/scripts/"

echo "==> 同步持仓数据"
if [[ -f "$ROOT/data/private/portfolio.yaml" ]]; then
  "${SCP[@]}" "$ROOT/data/private/portfolio.yaml" "$HOST:$REMOTE_PORTFOLIO"
else
  echo "  本地无 data/private/portfolio.yaml，跳过"
fi
if [[ -f "$ROOT/data/private/conditional_orders.jsonl" ]]; then
  "${SCP[@]}" "$ROOT/data/private/conditional_orders.jsonl" "$HOST:$REMOTE_ORDERS"
else
  echo "  本地无 data/private/conditional_orders.jsonl，跳过"
fi
if [[ -f "$ROOT/data/calendar_events.jsonl" ]]; then
  "${SCP[@]}" "$ROOT/data/calendar_events.jsonl" "$HOST:$REMOTE_CALENDAR"
else
  echo "  本地无 data/calendar_events.jsonl，跳过"
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
from src.gold_miner.sentinel.__main__ import main
raise SystemExit(main())
EOF
chmod +x /home/ubuntu/.hermes/scripts/gold_sentinel.py"

# 分频道薄入口
for mode in alert price orders calendar full; do
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
echo "✅ 黄金哨兵部署完成"
echo "持仓: $REMOTE_PORTFOLIO"
echo "条件单: $REMOTE_ORDERS"
echo "日历: $REMOTE_CALENDAR"
echo "入口: gold_sentinel.py --mode <alert|price|orders|calendar|full>"
echo ""
echo "建议 Hermes cron:"
echo "  1) 盘中监控  */15 9-23 * * 1-5  → gold_alert.py"
echo "  2) 报价快照  0 10,14,20 * * 1-5 → gold_price.py"
echo "  3) 开盘简报  30 9 * * 1-5         → gold_full.py"
echo "  4) 日历提醒  0 8 * * 1-5          → gold_calendar.py"
echo ""
echo "同步数据:"
echo "  scp -i ~/Documents/hermes.pem data/private/portfolio.yaml ubuntu@124.220.236.129:$REMOTE_PORTFOLIO"
echo "  scp -i ~/Documents/hermes.pem data/private/conditional_orders.jsonl ubuntu@124.220.236.129:$REMOTE_ORDERS"
echo "  scp -i ~/Documents/hermes.pem data/calendar_events.jsonl ubuntu@124.220.236.129:$REMOTE_CALENDAR"
