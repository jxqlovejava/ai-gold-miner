#!/usr/bin/env python3
"""自适应频率金价监控 — 状态机驱动, 急跌时自动缩短检查间隔 → 微信推送.

设计:
  cron 每分钟触发, 脚本内部状态机决定是否执行完整检查:
    NORMAL   → 每5分钟检查一次 (价格平稳)
    WATCHING → 每2分钟检查一次 (价格开始下跌)
    ALERT    → 每1分钟检查一次 (加速下跌中)
    CRITICAL → 每1分钟检查+立即推送 (恐慌性急跌)

状态转换:
  NORMAL ──跌>0.5%/次──→ ALERT
  NORMAL ──跌>0.3%/次──→ WATCHING
  WATCHING ──继续跌>0.5%──→ ALERT
  WATCHING ──稳定10分钟──→ NORMAL
  ALERT ──继续跌>0.5%──→ CRITICAL
  ALERT ──稳定15分钟──→ WATCHING
  CRITICAL ──稳定20分钟──→ ALERT
  任何状态 ──回升>0.5%──→ 降一级

检测维度 (每次完整检查):
  1. 成本逼近 — 价格从上方逼近成本线
  2. 高点回撤 — 距N日高点跌幅超阈值
  3. 连续下跌 — 连续N日收阴
  4. 日内逆转 — 当日从涨转跌幅度
  5. 急涨/急跌 — 短时间内价格突变 (继承 price_surge_monitor)

用法:
  PYTHONPATH=src python3 scripts/adaptive_gold_monitor.py

cron (每分钟触发, 内部自适应节流):
  * * * * * cd /path/to/ai-gold-miner && PYTHONPATH=src python3 scripts/adaptive_gold_monitor.py >> logs/adaptive_monitor.log 2>&1
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BEIJING = timezone(timedelta(hours=8))

# ═══════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════

# 各状态的检查间隔 (秒)
MIN_INTERVALS: dict[str, int] = {
    "NORMAL": 300,    # 5分钟
    "WATCHING": 120,  # 2分钟
    "ALERT": 60,      # 1分钟
    "CRITICAL": 60,   # 1分钟 (但每次都推送)
}

# 状态转换阈值
ESCALATION_PCT = {
    "to_watching": 0.3,    # 跌 >0.3% → WATCHING
    "to_alert": 0.5,       # 跌 >0.5% → ALERT
    "to_critical": 0.5,    # ALERT状态下继续跌 0.5% → CRITICAL
    "de_escalation_pct": 0.5,  # 回升 >0.5% → 降一级
}

# 稳定回退时间 (秒)
STABLE_TIMEOUTS = {
    "WATCHING": 600,   # 10分钟稳定 → NORMAL
    "ALERT": 900,      # 15分钟稳定 → WATCHING
    "CRITICAL": 1200,  # 20分钟稳定 → ALERT
}

# 连续下跌阈值
CONSECUTIVE_DOWN_DAYS = 3

# 日内逆转阈值
INTRADAY_REVERSAL_PCT = 1.5

# 高点回撤检测
PEAK_DRAWDOWN_WINDOWS = [3, 5, 7, 14]
PEAK_DRAWDOWN_THRESHOLDS = [
    (0.03, "⚠️ 距{window}d高点回撤3%, 短线获利盘开始出逃"),
    (0.05, "🔶 距{window}d高点回撤5%, 中线资金在撤退"),
    (0.08, "🔴 距{window}d高点回撤8%, 趋势可能反转"),
]

# 成本逼近预警级别
COST_PROXIMITY_BANDS = [
    (0.03, "🔴 仅剩3%盈利! 距成本线一步之遥"),
    (0.02, "🚨 仅剩2%盈利! 获利盘快速出逃"),
    (0.01, "💀 仅剩1%盈利! 即将亏损"),
    (0.00, "❌ 跌破成本线!"),
]

# 冷却: 告警推送最小间隔 (秒)
ALERT_COOLDOWN = {
    "NORMAL": 600,     # 10分钟
    "WATCHING": 300,   # 5分钟
    "ALERT": 120,      # 2分钟
    "CRITICAL": 60,    # 1分钟 (急跌时每次推送)
}

# 路径
PROJECT_ROOT = Path(os.environ.get(
    "GOLD_MINER_ROOT",
    str(Path(__file__).resolve().parents[1]),
))
STATE_FILE = Path(os.environ.get(
    "ADAPTIVE_MONITOR_STATE",
    os.path.expanduser("~/.hermes/gold/adaptive_monitor_state.json"),
))
PORTFOLIO_PATH = PROJECT_ROOT / "data/private/portfolio.yaml"
LOG_FILE = Path(os.environ.get(
    "ADAPTIVE_MONITOR_LOG",
    str(PROJECT_ROOT / "logs/adaptive_monitor.log"),
))

# ═══════════════════════════════════════════════════════════════
# 机会提醒配置 (止盈/抄底) — 可选 data/private/opportunity_config.yaml 覆盖
# ═══════════════════════════════════════════════════════════════

OPP_DEFAULTS: dict = {
    "require_surge": True,          # 止盈是否需要急涨速度条件
    "breakout_lookback_days": 20,   # N日新高窗口
    "min_profit_pct": 0.05,         # 浮盈阈值
    "dip_lookback_days": 20,        # N日低点窗口
    "key_levels": [921.0, 850.0],   # 元/克; 921≈$4000/oz (USD/CNY≈7.16)
    "key_level_band_pct": 0.01,     # 关键价位带宽 ±1%
    "cooldown_take_profit_min": 60,
    "cooldown_dip_low_min": 60,
    "realert_move_pct": 0.01,       # 冷却内同向再走1%可再提醒
    "snapshot_stale_hours": 48,     # 信号快照过期阈值
}
OPP_CONFIG_PATH = PROJECT_ROOT / "data/private/opportunity_config.yaml"
SIGNAL_SNAPSHOT_PATH = PROJECT_ROOT / "data/signal_snapshot.json"
ORDERS_PATH = PROJECT_ROOT / "data/private/conditional_orders.jsonl"


def _load_opp_config() -> dict:
    """顶部默认值 + 可选 YAML 覆盖 (只覆盖已知键)."""
    cfg = dict(OPP_DEFAULTS)
    if OPP_CONFIG_PATH.exists():
        try:
            import yaml
            user_cfg = yaml.safe_load(OPP_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            if isinstance(user_cfg, dict):
                cfg.update({k: v for k, v in user_cfg.items() if k in cfg})
        except Exception:
            pass
    return cfg


def _now() -> datetime:
    return datetime.now(BEIJING)


# ═══════════════════════════════════════════════════════════════
# 价格获取
# ═══════════════════════════════════════════════════════════════

def _fetch_price() -> dict | None:
    """获取积存金当前价."""
    try:
        import httpx
        resp = httpx.get(
            "https://ms.jr.jd.com/gw/generic/hj/h5/m/latestPrice",
            headers={
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
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
        datas = result_data.get("datas", {}) if isinstance(result_data, dict) else {}
        price = float(datas.get("price", 0))
        yesterday = float(datas.get("yesterdayPrice", 0))
        if price <= 0:
            return None
        return {
            "price": round(price, 2),
            "prev_close": round(yesterday, 2),
            "change_pct": round((price - yesterday) / yesterday * 100, 2) if yesterday > 0 else 0.0,
        }
    except Exception:
        return None


def _load_portfolio() -> dict | None:
    """读取持仓."""
    if not PORTFOLIO_PATH.exists():
        return None
    try:
        import yaml
        with open(PORTFOLIO_PATH) as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def _get_cost_basis() -> float | None:
    p = _load_portfolio()
    if not p:
        return None
    try:
        return float(p["positions"]["gold_jd"]["avg_cost"])
    except (KeyError, ValueError, TypeError):
        return None


def _get_historical(days: int = 30) -> list[dict]:
    """获取积存金历史."""
    try:
        from gold_miner.data.jd_accumulation_gold import JdAccumulationGoldFetcher
        f = JdAccumulationGoldFetcher(bank="MS")
        df = f.fetch(days=days)
        if df is None or df.empty:
            return []
        return [
            {"date": str(row["timestamp"].date()) if hasattr(row["timestamp"], "date") else str(row["timestamp"])[:10],
             "close": float(row["close"])}
            for _, row in df.iterrows()
        ]
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════
# 状态管理
# ═══════════════════════════════════════════════════════════════

DEFAULT_STATE = {
    "level": "NORMAL",
    "last_check_time": None,
    "last_price": None,
    "last_alert_time": None,
    "last_alert_type": "",
    "level_entered_at": None,
    "consecutive_skips": 0,
    # 反弹检测
    "trend_low": None,       # 本次下跌的最低点
    "trend_high": None,      # 下跌起点(阶段高点)
    "prev_change_pct": 0.0,  # 上次检查的涨跌幅
    # 机会提醒 (止盈/抄底)
    "tp_alert_at": None,
    "tp_alert_price": None,
    "dip_alert_at": None,
    "dip_alert_price": None,
    "in_band_levels": [],
}


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return dict(DEFAULT_STATE)
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        # 合并默认值 (新字段兼容)
        for k, v in DEFAULT_STATE.items():
            if k not in state:
                state[k] = v
        return state
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_STATE)


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


# ═══════════════════════════════════════════════════════════════
# 自适应频率核心: 状态机
# ═══════════════════════════════════════════════════════════════

def _should_check(state: dict) -> tuple[bool, str]:
    """决定本次是否执行完整检查."""
    level = state.get("level", "NORMAL")
    last_check = state.get("last_check_time")

    if not last_check:
        return True, "首次运行"

    try:
        last_dt = datetime.fromisoformat(last_check)
        elapsed = (_now() - last_dt).total_seconds()
    except (ValueError, TypeError):
        return True, "时间解析失败"

    min_interval = MIN_INTERVALS.get(level, 300)

    if elapsed >= min_interval:
        return True, f"距上次检查 {elapsed:.0f}s ≥ {min_interval}s ({level}模式)"
    else:
        skip_reason = f"距上次检查 {elapsed:.0f}s < {min_interval}s ({level}模式), 跳过"
        return False, skip_reason


def _determine_escalation(current: float, state: dict) -> str:
    """根据价格变化决定状态升级/降级."""
    level = state.get("level", "NORMAL")
    last_price = state.get("last_price")
    level_entered = state.get("level_entered_at")

    if not last_price:
        return level  # 首次运行, 保持当前

    change_pct = (current - last_price) / last_price * 100

    # ── 升级逻辑 ──
    if level == "NORMAL":
        if change_pct <= -ESCALATION_PCT["to_alert"]:
            return "ALERT"
        if change_pct <= -ESCALATION_PCT["to_watching"]:
            return "WATCHING"
        # 价格在涨, 保持 NORMAL

    elif level == "WATCHING":
        if change_pct <= -ESCALATION_PCT["to_alert"]:
            return "ALERT"

    elif level == "ALERT":
        if change_pct <= -ESCALATION_PCT["to_critical"]:
            return "CRITICAL"

    elif level == "CRITICAL":
        # CRITICAL 不自动升级 (已是最高)
        pass

    # ── 降级逻辑 (价格回升或稳定) ──
    if change_pct >= ESCALATION_PCT["de_escalation_pct"]:
        # 明显回升 → 降一级
        downgrade = {"CRITICAL": "ALERT", "ALERT": "WATCHING", "WATCHING": "NORMAL"}
        return downgrade.get(level, level)

    # ── 稳定回退 (价格变动小, 且在当前级别够久) ──
    if level in ("WATCHING", "ALERT", "CRITICAL") and abs(change_pct) < 0.15:
        if level_entered:
            try:
                entered_dt = datetime.fromisoformat(level_entered)
                stable_seconds = (_now() - entered_dt).total_seconds()
                timeout = STABLE_TIMEOUTS.get(level, 99999)
                if stable_seconds >= timeout:
                    downgrade = {"CRITICAL": "ALERT", "ALERT": "WATCHING", "WATCHING": "NORMAL"}
                    return downgrade.get(level, level)
            except (ValueError, TypeError):
                pass

    return level  # 保持不变


# ═══════════════════════════════════════════════════════════════
# 检测逻辑
# ═══════════════════════════════════════════════════════════════

def _check_cost_proximity(current: float, cost: float) -> dict | None:
    if current <= cost:
        loss_pct = (cost - current) / cost * 100
        return {
            "type": "cost_below",
            "message": f"❌ 跌破成本线 {cost:.0f}元! 当前 {current:.2f}, 浮亏 {loss_pct:.1f}%",
            "severity": "CRITICAL",
        }
    profit_margin = (current - cost) / cost
    for threshold, msg in COST_PROXIMITY_BANDS:
        if profit_margin <= threshold:
            return {
                "type": "cost_proximity",
                "message": f"{msg} ({cost:.0f}元成本线, 当前 {current:.2f}, 仅剩 {profit_margin*100:.1f}%盈利)",
                "severity": "HIGH" if threshold <= 0.02 else "MEDIUM",
            }
    return None


def _check_peak_drawdown(current: float, historical: list[dict]) -> dict | None:
    if len(historical) < 3:
        return None
    for window_days in PEAK_DRAWDOWN_WINDOWS:
        if len(historical) < window_days:
            continue
        window = historical[-window_days:]
        peak = max(p["close"] for p in window)
        drawdown = (peak - current) / peak
        for threshold, msg in PEAK_DRAWDOWN_THRESHOLDS:
            if drawdown >= threshold:
                return {
                    "type": "peak_drawdown",
                    "message": msg.format(window=window_days) + f" | {window_days}日高点 {peak:.0f}→{current:.0f} ({drawdown*100:.1f}%)",
                    "severity": "HIGH" if drawdown > 0.05 else "MEDIUM",
                }
    return None


def _check_consecutive_down(historical: list[dict]) -> dict | None:
    if len(historical) < CONSECUTIVE_DOWN_DAYS + 1:
        return None
    closes = [p["close"] for p in historical]
    down_count = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] < closes[i - 1]:
            down_count += 1
        else:
            break
    if down_count >= CONSECUTIVE_DOWN_DAYS:
        total_change = (closes[-1] - closes[-1 - down_count]) / closes[-1 - down_count] * 100
        avg_daily = total_change / down_count
        return {
            "type": "consecutive_down",
            "message": (
                f"连跌{down_count}日 {closes[-1-down_count]:.0f}→{closes[-1]:.0f} "
                f"({total_change:+.1f}%, 日均{avg_daily:+.1f}%)"
            ),
            "severity": "MEDIUM",
        }
    return None


def _check_intraday_reversal(change_pct: float, prev_close: float, current: float) -> dict | None:
    if change_pct <= -INTRADAY_REVERSAL_PCT:
        return {
            "type": "intraday_reversal",
            "message": f"⚡ 日内急跌! 昨收 {prev_close:.0f}→当前 {current:.2f} ({change_pct:+.1f}%)",
            "severity": "HIGH",
        }
    return None


def _check_surge(current: float, state: dict) -> dict | None:
    """价格急变检测 (继承 price_surge_monitor 逻辑)."""
    last_price = state.get("last_price")
    if not last_price:
        return None
    change_pct = (current - last_price) / last_price * 100
    if abs(change_pct) >= 0.5:
        direction = "up" if change_pct > 0 else "down"
        return {
            "type": "price_surge",
            "direction": direction,
            "change_pct": round(change_pct, 2),
            "message": f"{'📈' if direction == 'up' else '📉'} 价格{'急涨' if direction == 'up' else '急跌'}! "
                       f"{last_price:.0f}→{current:.0f} ({change_pct:+.2f}%)",
            "severity": "CRITICAL" if abs(change_pct) > 1.0 else "HIGH",
        }
    return None


def _check_rebound(current: float, state: dict, cost_basis: float | None = None) -> dict | None:
    """检测下跌后的反弹.

    触发条件: 之前在下行 (trend_low 存在且 < trend_high) + 当前在回升.
    返回反弹摘要: 从低点回升幅度/百分比, 距成本距离.
    """
    trend_low = state.get("trend_low")
    trend_high = state.get("trend_high")
    if not trend_low or not trend_high:
        return None

    # 还在跌或持平 → 不是反弹
    if current <= trend_low:
        return None

    rebound = current - trend_low
    rebound_pct = rebound / trend_low * 100

    # 反弹 < 0.3% → 不通知 (噪音)
    if rebound_pct < 0.3:
        return None

    drop_total = trend_high - trend_low
    drop_pct = drop_total / trend_high * 100

    lines = [
        f"📈 反弹 {rebound_pct:+.1f}% | {trend_low:.0f}→{current:.0f} (低点回升 ¥{rebound:.0f})",
        f"   本轮跌幅: {trend_high:.0f}→{trend_low:.0f} ({drop_pct:.1f}%), "
        f"已收复 {rebound/drop_total*100:.0f}%",
    ]
    if cost_basis and current < cost_basis:
        to_cost = cost_basis - current
        lines.append(f"   距成本线还差 ¥{to_cost:.0f} ({to_cost/cost_basis*100:.1f}%)")
    elif cost_basis:
        above_cost = (current - cost_basis) / cost_basis * 100
        lines.append(f"   已回到成本线上方 (+{above_cost:.1f}%) ✅")

    return {
        "type": "rebound",
        "message": "\n".join(lines),
        "severity": "MEDIUM" if rebound_pct > 1.0 else "INFO",
    }


def _update_trend_bookkeeping(current: float, prev_price: float | None, state: dict) -> None:
    """维护反弹检测所需的趋势状态.

    下跌中追踪低点; 回升后当价格远离低点 >2% 时重置趋势.
    """
    if prev_price is None or prev_price <= 0:
        return

    change_pct = (current - prev_price) / prev_price * 100

    # 下跌中 → 更新低点 + 记录阶段高点
    if change_pct < -0.15:
        if state.get("trend_low") is None or current < state["trend_low"]:
            state["trend_low"] = current
        if state.get("trend_high") is None:
            state["trend_high"] = prev_price

    # 回升 + 已远离低点 >2% → 这波跌完了, 重置
    if change_pct > 0.15 and state.get("trend_low"):
        recovery_pct = (current - state["trend_low"]) / state["trend_low"] * 100
        if recovery_pct > 2.0:
            state["trend_low"] = None
            state["trend_high"] = None


def _rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder RSI; 数据不足返回 None."""
    if len(closes) < period + 1:
        return None
    avg_gain = 0.0
    avg_loss = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            avg_gain += d
        else:
            avg_loss -= d
    avg_gain /= period
    avg_loss /= period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        gain = d if d > 0 else 0.0
        loss = -d if d < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _ma(closes: list[float], window: int) -> float | None:
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def _check_take_profit_breakout(
    current: float,
    historical: list[dict],
    cost_basis: float | None,
    cfg: dict,
    surge: dict | None,
) -> dict | None:
    """止盈候选: 急涨 + 破N日新高 + 浮盈≥阈值, 三条件同时."""
    if cost_basis is None or len(historical) < 10:
        return None
    if cfg["require_surge"]:
        if not surge or surge.get("direction") != "up":
            return None
    lookback = int(cfg["breakout_lookback_days"])
    window = historical[-lookback:] if len(historical) >= lookback else list(historical)
    high_n = max(p["close"] for p in window)
    if current <= high_n:
        return None
    profit_pct = (current - cost_basis) / cost_basis
    if profit_pct < cfg["min_profit_pct"]:
        return None
    return {
        "type": "take_profit_breakout",
        "high_n": high_n,
        "lookback": lookback,
        "profit_pct": profit_pct,
    }


def _check_dip_buy_opportunity(
    current: float,
    state: dict,
    historical: list[dict],
    cfg: dict,
) -> dict | None:
    """买入候选: 破N日低点 或 边沿进入关键价位带 (带外→带内才触发).

    每次调用都把 state["in_band_levels"] 更新为当前在带内的价位列表.
    """
    if len(historical) < 10:
        return None
    band = cfg["key_level_band_pct"]
    levels = [float(lv) for lv in cfg["key_levels"]]
    in_band_now = [lv for lv in levels if abs(current - lv) / lv <= band]
    prev_in_band = [float(lv) for lv in state.get("in_band_levels", [])]
    entered = [lv for lv in in_band_now if lv not in prev_in_band]
    state["in_band_levels"] = in_band_now

    lookback = int(cfg["dip_lookback_days"])
    window = historical[-lookback:] if len(historical) >= lookback else list(historical)
    low_n = min(p["close"] for p in window)
    broke_low = current < low_n

    if not broke_low and not entered:
        return None
    return {
        "type": "dip_buy_opportunity",
        "broke_low": broke_low,
        "low_n": low_n,
        "lookback": lookback,
        "key_level": entered[0] if entered else None,
    }


def _load_signal_snapshot(cfg: dict) -> dict | None:
    """读取 pipeline 信号快照; 缺失/损坏/超时/无有效维度 → None."""
    if not SIGNAL_SNAPSHOT_PATH.exists():
        return None
    try:
        snap = json.loads(SIGNAL_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(snap["timestamp"])
        age_h = (_now() - ts).total_seconds() / 3600
        if age_h > cfg["snapshot_stale_hours"]:
            return None
        bull = int(snap.get("bull_dims", 0))
        bear = int(snap.get("bear_dims", 0))
        if bull + bear == 0:
            return None
        return {
            "bull": bull,
            "bear": bear,
            "clarity": snap.get("direction_clarity", "mixed"),
            "age_h": age_h,
            "timestamp": snap["timestamp"],
        }
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return None


def _gather_evidence(current: float, historical: list[dict], cfg: dict) -> dict:
    """理由引擎证据包: 技术面 + 信号快照 + 48h事件 + 活跃条件单. 各源失败独立降级."""
    closes = [p["close"] for p in historical] + [current]
    ev: dict = {
        "rsi14": _rsi(closes),
        "ma20": _ma(closes, 20),
        "ma60": _ma(closes, 60),
        "snapshot": _load_signal_snapshot(cfg),
        "events": [],
        "active_orders": [],
    }
    try:
        from gold_miner.data.calendar import EventCalendar, EventImpact
        cal = EventCalendar()
        ev["events"] = [
            {"name": e.name, "time": e.beijing_time_str, "impact": e.impact.value}
            for e in cal.get_upcoming(days=2, min_impact=EventImpact.MEDIUM)[:3]
        ]
    except Exception:
        pass
    try:
        if ORDERS_PATH.exists():
            for line in ORDERS_PATH.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                o = json.loads(line)
                if o.get("status") != "active":
                    continue
                if o.get("type") == "oco" and o.get("oco"):
                    tp = o["oco"]["take_profit"]["price"]
                    sl = o["oco"]["stop_loss"]["price"]
                    ev["active_orders"].append(f"OCO止盈{tp:.0f}/止损{sl:.0f}")
                else:
                    ev["active_orders"].append(
                        f"{o.get('direction', '?')}@{o.get('trigger_price', 0):.0f}"
                    )
    except Exception:
        pass
    return ev


def _evaluate_reason(action: str, candidate: dict, ev: dict, cfg: dict) -> dict:
    """理由强度判定: strong/medium/weak/veto."""
    snap = ev.get("snapshot")
    rsi = ev.get("rsi14")
    events = ev.get("events") or []

    if snap is None:
        return {
            "strength": "weak",
            "reasons": ["无近期信号快照(>48h或未运行pipeline)，理由弱，请自行确认"],
            "veto_note": "",
        }

    bull, bear, clarity = snap["bull"], snap["bear"], snap["clarity"]
    snap_line = f"信号快照({snap['timestamp'][5:16].replace('T', ' ')})：多{bull}维 空{bear}维"
    reasons: list[str] = []

    if action == "take_profit":
        if clarity == "bullish" and (rsi is None or rsi < 70):
            return {
                "strength": "veto",
                "reasons": [],
                "veto_note": f"{snap_line}，方向仍偏多，未触发止盈建议",
            }
        if clarity == "mixed":
            reasons.append(f"{snap_line}，方向不明，落袋为安")
            strength = "strong"
        elif clarity == "bearish":
            reasons.append(f"{snap_line}，信号转空，反弹止盈")
            strength = "strong"
        else:
            reasons.append(f"{snap_line}，偏多但超买")
            strength = "medium"
        if rsi is not None and rsi >= 70:
            reasons.append(f"RSI(14)={rsi:.0f}，超买区")
        if events:
            names = "、".join(e["name"] for e in events[:2])
            reasons.append(f"48h内事件：{names}，数据前落袋符合军规")
        return {"strength": strength, "reasons": reasons, "veto_note": ""}

    # action == "dip_buy"
    if clarity == "bearish":
        return {
            "strength": "veto",
            "reasons": [],
            "veto_note": f"{snap_line}，信号仍偏空，支撑未确认，未触发买入建议",
        }
    resonance = bool(candidate.get("broke_low")) and candidate.get("key_level") is not None
    if resonance and rsi is not None and rsi < 30:
        reasons.append(
            f"破{candidate['lookback']}日低点与关键价位{candidate['key_level']:.0f}共振"
        )
        reasons.append(f"RSI(14)={rsi:.0f}，超卖区")
        strength = "strong"
    else:
        strength = "medium"
        reasons.append(snap_line)
        if rsi is not None and rsi < 30:
            reasons.append(f"RSI(14)={rsi:.0f}，超卖区")
    if events:
        names = "、".join(e["name"] for e in events[:2])
        reasons.append(f"⚠️ 48h内事件：{names}，数据前不接飞刀，建议等落地")
    return {"strength": strength, "reasons": reasons, "veto_note": ""}


# 通知: macOS 桌面通知 (osascript) + Hermes weixin (如已配置)
# 无告警时静默退出, 有告警时多渠道推送


def _send_alert(message: str) -> bool:
    """多渠道推送通知.

    优先级: macOS 通知 (osascript) → Hermes weixin (需 gateway 运行)
    macOS 通知永远可用; Hermes weixin 需先配置 gateway.
    """
    import subprocess
    success = False

    # 1. macOS 桌面通知 (最可靠)
    try:
        # 截取第一行作为标题, 其余为内容
        lines = message.strip().split("\n")
        title = lines[0][:100] if lines else "金价监控"
        body = "\n".join(lines[1:5])[:200] if len(lines) > 1 else ""
        # 清理特殊字符防止 osascript 报错
        title_clean = title.replace('"', "'").replace("\\", "")
        body_clean = body.replace('"', "'").replace("\\", "")
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{body_clean}" with title "{title_clean}" sound name "Glass"'],
            capture_output=True, timeout=10,
        )
        success = True
    except Exception:
        pass

    return success


# ═══════════════════════════════════════════════════════════════
# 下跌原因分析 — 判断是否是机构抛售
#
# 数据源:
#   1. COT 非商业净多仓 (周度, T+3) — CFTC 官方
#   2. 国际黄金 ETF 资金流 (日度) — GLD/IAU
# 两源交叉验证: 同时出逃=机构抛售确认; 单源=关注; 无信号=宏观/消息面
#
# 缓存: 结果写入 STATE_FILE 同目录的 flow_reason_cache.json
#   cron 每次调用是新进程, 内存缓存不跨进程, 必须用文件缓存
# ═══════════════════════════════════════════════════════════════

_FLOW_CACHE_FILE = STATE_FILE.with_name("flow_reason_cache.json")
_FLOW_CHECK_TTL = 1800  # 30分钟


def _load_flow_cache() -> dict | None:
    if not _FLOW_CACHE_FILE.exists():
        return None
    try:
        data = json.loads(_FLOW_CACHE_FILE.read_text(encoding="utf-8"))
        cached_at = data.get("cached_at")
        if cached_at:
            cache_time = datetime.fromisoformat(cached_at)
            if (_now() - cache_time).total_seconds() < _FLOW_CHECK_TTL:
                return data.get("result")
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return None


def _save_flow_cache(result: dict) -> None:
    try:
        _FLOW_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _FLOW_CACHE_FILE.write_text(json.dumps({
            "cached_at": _now().isoformat(),
            "result": result,
        }, ensure_ascii=False))
    except Exception:
        pass


def _analyze_drop_reason(state: dict) -> dict:
    """分析金价下跌驱动因素: 机构抛售 vs 宏观压制 vs 消息面.

    返回 {'category': str, 'institutional_selling': bool, 'detail': str}
    category: 'institutional' | 'mixed' | 'macro' | 'unknown'
    """
    # 1) 检查文件缓存 (跨 cron 进程)
    cached = _load_flow_cache()
    if cached:
        return cached

    result = {
        "category": "unknown",
        "institutional_selling": False,
        "detail": "无法确认下跌驱动因素",
    }

    has_cot_data = False
    has_etf_data = False
    cot_selling = False
    etf_selling = False
    cot_score = 0.0
    etf_score = 0.0

    try:
        # 2) COT 机构持仓 — 周度, 判断聪明钱方向
        try:
            from gold_miner.signals.cot_signal import CotSignalGenerator
            cot_sigs = CotSignalGenerator().generate_signals()
            for s in cot_sigs:
                if "减仓" in s.name:
                    has_cot_data = True
                    cot_score = s.score  # 负值
                    cot_selling = cot_score < -0.3
                    break
                elif "加仓" in s.name:
                    has_cot_data = True
                    cot_score = s.score  # 正值
                    cot_selling = False
                    break
        except Exception:
            pass

        # 3) ETF 资金流 — 日度, 补充确认
        try:
            from gold_miner.signals.etf_flow_signal import EtfFlowSignalGenerator
            etf_sigs = EtfFlowSignalGenerator().generate_signals()
            for s in etf_sigs:
                name = s.name
                if "流出" in name:
                    has_etf_data = True
                    etf_score = s.score
                    # ETF流出信号分两档: "大幅流出" vs "资金流出"
                    etf_selling = etf_score < -0.3
                    break
                elif "流入" in name:
                    has_etf_data = True
                    etf_score = s.score
                    etf_selling = False
                    break
        except Exception:
            pass

        # 4) 交叉验证判断
        if has_cot_data or has_etf_data:
            if cot_selling and etf_selling:
                # 两源一致 → 机构抛售确认
                result["institutional_selling"] = True
                result["category"] = "institutional"
                result["detail"] = (
                    "🔴 机构资金在撤退 — COT净多仓减少 + ETF资金流出, "
                    "聪明钱在卖, 不是普通回调, 建议跟随减仓别死扛"
                )
            elif cot_selling:
                # 仅COT
                result["institutional_selling"] = True
                result["category"] = "mixed"
                result["detail"] = (
                    "🟠 COT非商业净多仓减少, 但ETF未同步流出. "
                    "部分机构在减仓, 关注是否加速. 可考虑小幅减仓"
                )
            elif etf_selling:
                # 仅ETF
                result["category"] = "mixed"
                result["detail"] = (
                    "🟠 黄金ETF资金流出, 但COT未同步减仓. "
                    "可能是散户/短线资金出逃, 机构尚未转向. 继续观察"
                )
            else:
                # 有数据但无卖出信号
                result["category"] = "macro"
                result["detail"] = (
                    "🟡 未见机构大规模出逃 — COT和ETF均无显著卖出. "
                    "下跌来自宏观压力(加息预期/强美元/油价)或消息面, "
                    "待FOMC/PCE明朗后再决定"
                )
        # else: 无任何数据 → 保持 unknown

    except Exception:
        pass

    # 5) 缓存并返回
    _save_flow_cache(result)
    return result


# ═══════════════════════════════════════════════════════════════
# 格式化
# ═══════════════════════════════════════════════════════════════

def _format_card(
    level: str,
    old_level: str,
    price_info: dict,
    cost_basis: float | None,
    alerts: list[dict],
    state: dict,
    drop_reason: dict | None = None,
    historical: list[dict] | None = None,
) -> str:
    """格式化人话卡片.

    去重规则:
    - 成本/盈亏已在 header 展示, cost_proximity 类告警不再重复
    - 趋势类告警 (连续下跌/高点回撤) 合并到趋势摘要行
    - 仅保留新增信息的告警 (急变/日内逆转/首次破位)
    """
    now = _now()
    level_emoji = {"NORMAL": "🟢", "WATCHING": "🟡", "ALERT": "🟠", "CRITICAL": "🔴"}

    lines = [
        f"{level_emoji.get(level, '⚪')} 金价监控 | {now.strftime('%H:%M:%S')}",
    ]

    # ── 价格 + 日涨跌 ──
    change_pct = price_info["change_pct"]
    lines.append(f"💰 {price_info['price']:.2f}元/克 ({change_pct:+.2f}%)")

    # ── 成本/盈亏 (compact) ──
    if cost_basis:
        pnl = (price_info["price"] - cost_basis) / cost_basis * 100
        lines.append(f"📊 成本¥{cost_basis:.0f} | 浮{'盈' if pnl >= 0 else '亏'} {abs(pnl):.1f}%")

    # ── 趋势摘要: 从告警中提取去重展示 ──
    # 分类告警: 趋势类 (合并到摘要行) vs 事件类 (单独展示)
    alert_by_type = {a["type"]: a for a in alerts}

    # 趋势行: 连续下跌 + 高点回撤 → 合并为一句话
    trend_parts = []
    consecutive = alert_by_type.get("consecutive_down")
    peak = alert_by_type.get("peak_drawdown")
    cost_alert = alert_by_type.get("cost_proximity") or alert_by_type.get("cost_below")

    if consecutive:
        # 提取关键数字: "连续N日下跌! X→Y (Z%)"
        trend_parts.append(consecutive["message"])
    if peak and peak.get("severity") in ("HIGH",):
        # 简化: "14日高点917→当前883 (回撤3.7%)"
        trend_parts.append(peak["message"])

    if trend_parts:
        lines.append(f"📉 {' | '.join(trend_parts)}")
    elif change_pct < -0.15 and not trend_parts:
        # 轻微下跌但无连续/回撤告警 — 给个简洁的一日趋势
        lines.append(f"📉 日内走弱 ({price_info['change_pct']:+.2f}%)")

    # 成本告警: 仅首次破成本线时简洁提醒 (不再重复数字)
    if cost_alert and cost_alert["type"] in ("cost_below",):
        loss_pct = (cost_basis - price_info["price"]) / cost_basis * 100
        lines.append(f"⚠️ 已破成本线 (浮亏{loss_pct:.1f}%), 注意止损位")

    # ── 频率调整 ──
    if old_level != level:
        lines.append(f"⚡ 监控频率调整: {old_level} → {level}")
        intervals = {l: f"{v//60}分{v%60}秒" for l, v in MIN_INTERVALS.items()}
        lines.append(f"   当前检查间隔: {intervals.get(level, '?')}")

    # ── 事件类告警: 仅展示新增信息的 (急变/逆转) ──
    # 已处理: consecutive_down, peak_drawdown, cost_proximity, cost_below
    # 当趋势摘要已覆盖时, 急变/逆转信号冗余 → 跳过
    shown_types = {"consecutive_down", "peak_drawdown", "cost_proximity", "cost_below"}
    if trend_parts:
        # 趋势摘要已覆盖下跌方向, price_surge + intraday_reversal 是重复信息
        shown_types.update({"price_surge", "intraday_reversal"})

    remaining = [
        a for a in alerts
        if a["type"] not in shown_types
    ]
    if remaining:
        lines.append("")
        for a in remaining:
            lines.append(f"  {a['message']}")

    # 🆕 下跌原因分析
    if drop_reason and drop_reason.get("detail"):
        is_institutional = drop_reason.get("institutional_selling", False)
        header = "━━━ 🔍 谁在卖？━━━"
        action = drop_reason.get("action_hint", "")
        lines.append("")
        lines.append(header)
        lines.append(drop_reason["detail"])
        if action:
            lines.append(f"  💡 {action}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def main() -> int:
    state = _load_state()

    # 1. 自适应频率: 决定是否执行完整检查
    should_check, reason = _should_check(state)
    if not should_check:
        state["consecutive_skips"] = state.get("consecutive_skips", 0) + 1
        _save_state(state)
        return 0  # 静默跳过

    # 2. 获取价格
    price_info = _fetch_price()
    if not price_info:
        return 0  # 网络不可用

    current = price_info["price"]
    old_level = state.get("level", "NORMAL")

    # 3. 状态机: 决定升级/降级
    new_level = _determine_escalation(current, state)
    level_changed = new_level != old_level

    # 4. 完整检查 (在当前状态下)
    cost_basis = _get_cost_basis()
    historical = _get_historical(days=30)
    alerts: list[dict] = []

    # 4a. 价格急变 (所有级别都检测, 这是快速通道)
    surge = _check_surge(current, state)
    if surge:
        alerts.append(surge)

    # 4b. 成本逼近
    if cost_basis:
        cost_alert = _check_cost_proximity(current, cost_basis)
        if cost_alert and cost_alert["severity"] in ("CRITICAL", "HIGH"):
            alerts.append(cost_alert)

    # 4c. 高点回撤
    peak = _check_peak_drawdown(current, historical)
    if peak and peak["severity"] in ("HIGH",):
        alerts.append(peak)

    # 4d. 连续下跌
    down = _check_consecutive_down(historical)
    if down and new_level in ("WATCHING", "ALERT", "CRITICAL"):
        alerts.append(down)

    # 4e. 日内逆转
    reversal = _check_intraday_reversal(
        price_info["change_pct"], price_info["prev_close"], current
    )
    if reversal:
        alerts.append(reversal)

    # 4f. 反弹检测 (下跌趋势中的回升)
    rebound = _check_rebound(current, state, cost_basis)
    if rebound:
        alerts.append(rebound)

    # 5. 🆕 下跌原因分析 — 检测是否是机构在抛售
    #    价格下跌超阈值时触发, 结果缓存30分钟
    drop_reason = None
    if price_info["change_pct"] < -0.5 or new_level in ("ALERT", "CRITICAL"):
        drop_reason = _analyze_drop_reason(state)
        if drop_reason.get("institutional_selling"):
            drop_reason["action_hint"] = "机构在跑, 你也应该考虑减仓, 不要死扛"
        elif drop_reason.get("category") == "macro":
            drop_reason["action_hint"] = "非机构抛售, 观察宏观事件(FOMC/PCE)明朗后再决定"

    # 6. 通知 — 仅在有实质内容时输出
    # 已去重: 单独的成本对比不再触发通知 (趋势摘要替代)
    has_real_alerts = any(
        a["type"] not in ("cost_proximity",)
        for a in alerts
    )
    should_output = (
        has_real_alerts
        or level_changed
        or new_level != "NORMAL"
        or (drop_reason and drop_reason.get("category") == "institutional")
        or (rebound is not None)  # 反弹始终通知
    )

    if should_output:
        card = _format_card(new_level, old_level, price_info, cost_basis, alerts, state, drop_reason, historical)
        print(card, flush=True)  # 同时输出到 log 文件
        _send_alert(card)        # Hermes → 微信

    # 6. 更新状态
    now_iso = _now().isoformat()
    # ── 趋势簿记: 跟踪下跌低点, 用于反弹检测 ──
    prev_price = state.get("last_price")
    state["prev_change_pct"] = (
        (current - prev_price) / prev_price * 100
        if prev_price and prev_price > 0 else 0.0
    )
    _update_trend_bookkeeping(current, prev_price, state)

    state["level"] = new_level
    state["last_check_time"] = now_iso
    state["last_price"] = current
    state["consecutive_skips"] = 0
    if level_changed:
        state["level_entered_at"] = now_iso
    if should_output:
        state["last_alert_time"] = now_iso
        state["last_alert_type"] = ",".join(a["type"] for a in alerts) if alerts else "level_change"

    _save_state(state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"❌ 自适应监控异常: {e}", file=sys.stderr)
        sys.exit(1)
