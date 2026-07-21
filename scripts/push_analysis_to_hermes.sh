#!/usr/bin/env bash
# 将本地完整分析报告推送到 Hermes → 微信
# 用法:
#   bash scripts/push_analysis_to_hermes.sh <报告文本>
#   echo "报告内容" | bash scripts/push_analysis_to_hermes.sh
#   bash scripts/push_analysis_to_hermes.sh --file /path/to/report.md
#
# 注意：Hermes 的 Weixin iLink 有 30s 冷却机制，每次调用都重置。
# 不要加客户端重试 — 会放大限流。如果失败，等 60s+ 再手动重试。
set -euo pipefail

PEM="${HERMES_PEM:-}"
HOST="${HERMES_HOST:-}"

if [ -z "$PEM" ] || [ -z "$HOST" ]; then
  echo "❌ 请设置 HERMES_PEM 和 HERMES_HOST 环境变量"
  echo "   export HERMES_PEM=~/Documents/hermes.pem"
  echo "   export HERMES_HOST=ubuntu@<your-server-ip>"
  exit 1
fi
SSH=(ssh -i "$PEM" -o StrictHostKeyChecking=no)

MESSAGE=""

if [ "${1:-}" = "--file" ] && [ -f "${2:-}" ]; then
  MESSAGE=$(cat "$2")
elif [ -n "${1:-}" ] && [ "${1:-}" != "--file" ]; then
  MESSAGE="$1"
else
  MESSAGE=$(cat)
fi

if [ -z "$MESSAGE" ]; then
  echo "❌ 无内容可推送"
  exit 1
fi

# 截断过长的消息 (微信单条限制约 2048 字符)
MAX_LEN=1800
if [ ${#MESSAGE} -gt $MAX_LEN ]; then
  MESSAGE="${MESSAGE:0:$MAX_LEN}
...
(内容过长已截断, 完整报告见终端)"
fi

# 写入 Hermes 端的临时队列文件，由 gold_sentinel cron 代为发送
# 避免客户端 iLink rate-limit 死循环 (每次调用重置 30s 冷却)
REMOTE_TMP="/tmp/analysis_push_$(date +%Y%m%d_%H%M%S).txt"
echo "$MESSAGE" | "${SSH[@]}" "$HOST" "cat > $REMOTE_TMP && echo '📝 已写入队列:' $REMOTE_TMP" 2>&1

echo ""
echo "📝 分析摘要已写入 Hermes 队列文件: $REMOTE_TMP"
echo "   下次 gold_sentinel cron 将代为推送到微信"
echo "   如需立即推送: ssh hermes 'cat /tmp/analysis_push_*.txt | tail -1 | xargs -I{} hermes send -t weixin -s \"[黄金分析]\" -f - <<< {}'"
