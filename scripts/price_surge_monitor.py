#!/usr/bin/env python3
"""实时金价异动监控 — 每2分钟轮询, 检测快速涨跌 → Hermes → 个人微信.

Hermes 约定 (与 sentinel/__main__.py 一致):
  - 无异动: stdout 为空, exit 0
  - 有异动: stdout 打印人话卡片, exit 0
  - 致命错误: stderr 打印, exit 1

用法:
  PYTHONPATH=src python3 scripts/price_surge_monitor.py

cron (北京时间 9:00-23:30 每2分钟, Mon-Fri):
  */2 9-23 * * 1-5 cd /path/to/ai-gold-miner && PYTHONPATH=src python3 scripts/price_surge_monitor.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

BEIJING = timezone(timedelta(hours=8))

# ── 配置 ──
SURGE_THRESHOLD_PCT = 0.5       # 2分钟内涨跌 > 0.5% 触发告警
WINDOW_MINUTES = 10              # 滑动窗口
WINDOW_CUMULATIVE_PCT = 1.0     # 窗口内累计涨跌 > 1.0% 也告警
COOLDOWN_MINUTES = 10            # 同方向冷却
COOLDOWN_REVERSE_MINUTES = 3     # 反向突破冷却更短, 立即告警

# State 文件路径 — Hermes 上用绝对路径, 本地 fallback
STATE_FILE = Path(os.environ.get(
    "SURGE_MONITOR_STATE",
    os.path.expanduser("~/.hermes/gold/surge_monitor_state.json"),
))


def _now() -> datetime:
    return datetime.now(BEIJING)


def _load_state() -> dict:
    """加载上次价格快照."""
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    """保存价格快照."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


_JD_API_URL = "https://ms.jr.jd.com/gw/generic/hj/h5/m/latestPrice"


def _fetch_price() -> dict | None:
    """获取积存金当前价 — 直调京东金融 H5 接口 (不依赖 gold_miner 模块)."""
    try:
        resp = httpx.get(
            _JD_API_URL,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)"
                ),
                "Referer": "https://m.jd.com/",
            },
            timeout=8.0,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not data.get("success"):
            return None
        result_data = data.get("resultData", {})
        if isinstance(result_data, dict):
            datas = result_data.get("datas", {})
        else:
            datas = {}
        price = float(datas.get("price", 0))
        yesterday = float(datas.get("yesterdayPrice", 0))
        if price <= 0:
            return None
        change_pct = (price - yesterday) / yesterday * 100 if yesterday > 0 else 0.0
        return {
            "price": round(price, 2),
            "prev_close": round(yesterday, 2),
            "change_pct": round(change_pct, 2),
            "source": "京东金融",
        }
    except Exception:
        return None


def _in_cooldown(last_alert_at: str | None, direction: str,
                 last_direction: str | None) -> bool:
    """检查是否在冷却期内."""
    if not last_alert_at:
        return False

    try:
        last_at = datetime.fromisoformat(last_alert_at)
    except (ValueError, TypeError):
        return False

    elapsed = (_now() - last_at).total_seconds() / 60

    # 反向突破 — 更短冷却
    if last_direction and direction != last_direction:
        return elapsed < COOLDOWN_REVERSE_MINUTES

    return elapsed < COOLDOWN_MINUTES


def _check_surge(current: float, state: dict) -> dict | None:
    """检测快速涨跌.

    Returns:
        告警信息 dict 或 None (无异动).
    """
    last_price = state.get("last_price")
    last_ts = state.get("last_timestamp")
    if not last_price or not last_ts:
        return None  # 首次运行, 无基准

    # 计算 2 分钟变动
    change = current - last_price
    change_pct = change / last_price * 100

    if abs(change_pct) < SURGE_THRESHOLD_PCT:
        # 检查累计窗口
        window = state.get("window_prices", [])
        if not window:
            return None
        window_change = current - window[0]
        window_pct = window_change / window[0] * 100
        if abs(window_pct) < WINDOW_CUMULATIVE_PCT:
            return None
        # 累计触发 — 用窗口首条计算
        trigger_type = "cumulative"
        from_price = window[0]
        from_ts = state.get("window_start_ts", last_ts)
        alert_pct = window_pct
    else:
        trigger_type = "instant"
        from_price = last_price
        from_ts = last_ts
        alert_pct = change_pct

    direction = "up" if alert_pct > 0 else "down"

    # 冷却检查
    if _in_cooldown(
        state.get("last_alert_at"),
        direction,
        state.get("last_alert_direction"),
    ):
        return None

    return {
        "direction": direction,
        "trigger_type": trigger_type,
        "current": current,
        "from_price": from_price,
        "from_ts": from_ts,
        "change": current - from_price,
        "change_pct": alert_pct,
        "last_price": last_price,
    }


def _format_alert(alert: dict) -> str:
    """格式化告警为人话卡片."""
    direction = alert["direction"]
    emoji = "📈" if direction == "up" else "📉"
    label = "快速上涨!" if direction == "up" else "快速下跌!"
    arrow = "↑" if direction == "up" else "↓"

    now = _now()
    lines = [
        f"🚨 金价异动告警 | {now.strftime('%m-%d %H:%M')}",
        "",
        f"{emoji} {label}",
        f"  当前: {alert['current']:.2f}元/克",
        f"  {alert['from_ts'][:16] if 'T' in str(alert['from_ts']) else alert['from_ts']}: {alert['from_price']:.2f}元/克",
        f"  变动: {arrow}{abs(alert['change']):.2f}元 ({alert['change_pct']:+.2f}%)",
    ]

    # 额外信息
    if alert["trigger_type"] == "cumulative":
        lines.append(f"  ⏱ 类型: {WINDOW_MINUTES}分钟累计")
    else:
        lines.append("  ⏱ 类型: 2分钟内急变")

    return "\n".join(lines)


def main() -> int:
    price_data = _fetch_price()
    if not price_data:
        # 网络不可用, 静默跳过
        return 0

    current = price_data["price"]
    now = _now().isoformat()

    state = _load_state()

    # 更新滑动窗口
    window = state.get("window_prices", [])
    window_start = state.get("window_start_ts")
    if window_start:
        try:
            start_dt = datetime.fromisoformat(window_start)
            if (_now() - start_dt).total_seconds() / 60 > WINDOW_MINUTES:
                # 窗口过期, 重置
                window = []
                window_start = None
        except (ValueError, TypeError):
            window = []
            window_start = None

    if not window_start:
        window_start = now
    window.append(current)

    # 检测异动
    alert_info = _check_surge(current, state)

    # 更新持久化状态
    new_state = {
        "last_price": current,
        "last_timestamp": now,
        "window_prices": window,
        "window_start_ts": window_start,
        "last_alert_at": state.get("last_alert_at"),
        "last_alert_direction": state.get("last_alert_direction"),
    }

    if alert_info:
        new_state["last_alert_at"] = now
        new_state["last_alert_direction"] = alert_info["direction"]
        _save_state(new_state)
        print(_format_alert(alert_info), flush=True)
        return 0

    _save_state(new_state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"❌ 价格监控异常: {e}", file=sys.stderr)
        sys.exit(1)
