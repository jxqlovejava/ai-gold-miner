#!/usr/bin/env bash
# 将本地完整分析报告推送到 Hermes → 微信
# 用法:
#   bash scripts/push_analysis_to_hermes.sh <报告文本>
#   echo "报告内容" | bash scripts/push_analysis_to_hermes.sh
#   bash scripts/push_analysis_to_hermes.sh --file /path/to/report.md
set -euo pipefail

PEM="${HERMES_PEM:-$HOME/Documents/hermes.pem}"
HOST="${HERMES_HOST:-ubuntu@124.220.236.129}"
SSH=(ssh -i "$PEM" -o StrictHostKeyChecking=no)

MESSAGE=""

if [ "${1:-}" = "--file" ] && [ -f "${2:-}" ]; then
  MESSAGE=$(cat "$2")
elif [ -n "${1:-}" ] && [ "${1:-}" != "--file" ]; then
  MESSAGE="$1"
else
  # 从 stdin 读取
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

# 通过 Hermes send 推送到微信
echo "$MESSAGE" | "${SSH[@]}" "$HOST" "hermes send --to weixin --subject '[黄金分析]' -f -" 2>&1

echo ""
echo "✅ 分析报告已推送到微信"
