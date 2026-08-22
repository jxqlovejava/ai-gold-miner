#!/usr/bin/env bash
# 应用 Hermes 微信适配器限流调优 (systemd drop-in, 幂等可重跑).
#
# 背景 (2026-08-22 诊断): Hermes weixin 适配器 (gateway/platforms/weixin.py) 默认
#   WEIXIN_RATE_LIMIT_CIRCUIT_THRESHOLD=1   — 一次 iLink 限流即在 30s 窗口内熔断
#   WEIXIN_RATE_LIMIT_CIRCUIT_OPEN_SECONDS=30 — 熔断黑窗 30s
#   WEIXIN_SEND_CHUNK_DELAY_SECONDS=1.5      — 长卡片分 chunk 间隔 1.5s
#   WEIXIN_SEND_CHUNK_RETRIES=4              — 每次发送最多重试 4 次
# 事故: 黄金周报(周六10:00)/夜间突发新闻(01:52)/日历 等多个 job 在整点整分并发投递,
#   撞上 iLink 频率限制 → 一次限流熔断 30s → 期间所有微信推送全失败
#   (gateway.log 见 "iLink sendmessage rate limited; cooldown active for 30.0s")。
#
# 调优目标: 降低熔断误伤(3 次限流才熔断/黑窗 10s) + 拉大 chunk 间隔降瞬时频率
#   + 减少无谓重试(避免加重限流)。不改适配器代码, env 覆盖在 Hermes 升级后仍保留。
#
# 用法: bash scripts/apply_hermes_weixin_tuning.sh
set -euo pipefail

HERMES_PEM="$HOME/Documents/hermes.pem"
HERMES_HOST="ubuntu@124.220.236.129"
DROPDIR=".config/systemd/user/hermes-gateway.service.d"
DROP="$DROPDIR/weixin-rate-tuning.conf"

read -r -d '' CONF <<'EOF' || true
# Hermes weixin 适配器限流调优 (2026-08-22) — 一次限流不误伤全通道
[Service]
Environment="WEIXIN_RATE_LIMIT_CIRCUIT_THRESHOLD=3"
Environment="WEIXIN_RATE_LIMIT_CIRCUIT_OPEN_SECONDS=10"
Environment="WEIXIN_SEND_CHUNK_DELAY_SECONDS=2.5"
Environment="WEIXIN_SEND_CHUNK_RETRIES=2"
EOF

ssh -i "$HERMES_PEM" -o ConnectTimeout=20 -o StrictHostKeyChecking=no "$HERMES_HOST" "
set -e
mkdir -p \"\$HOME/$DROPDIR\"
cat > \"\$HOME/$DROP\" <<'SERVEREOF'
$CONF
SERVEREOF
systemctl --user daemon-reload
systemctl --user restart hermes-gateway
sleep 4
echo '--- service status ---'
systemctl --user is-active hermes-gateway
echo '--- 进程 env 验证 (应看到 WEIXIN_RATE_LIMIT_CIRCUIT_THRESHOLD=3) ---'
PID=\$(systemctl --user show -p MainPID --value hermes-gateway)
tr '\0' '\n' < /proc/\$PID/environ | grep -E 'WEIXIN_RATE_LIMIT|WEIXIN_SEND_CHUNK' || echo '!! env 未注入'
echo '--- drop-in 内容 ---'
cat \"\$HOME/$DROP\"
"
echo "✅ Hermes 微信适配器限流调优已应用"
