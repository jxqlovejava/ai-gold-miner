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
        return {
            "type": "consecutive_down",
            "message": f"📉 连续{down_count}日下跌! {closes[-1-down_count]:.0f}→{closes[-1]:.0f} ({total_change:+.1f}%)",
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
            "message": f"{'📈' if direction == 'up' else '📉'} 价格{'急涨' if direction == 'up' else '急跌'}! "
                       f"{last_price:.0f}→{current:.0f} ({change_pct:+.2f}%)",
            "severity": "CRITICAL" if abs(change_pct) > 1.0 else "HIGH",
        }
    return None


# 通知: 使用 hermes send 直接推送到微信
# 无告警时静默退出, 有告警时调用 hermes send --to weixin


def _send_alert(message: str) -> bool:
    """通过 Hermes 推送微信通知."""
    import subprocess
    try:
        result = subprocess.run(
            ["hermes", "send", "--to", "weixin", message],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False


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
) -> str:
    """格式化人话卡片."""
    now = _now()
    level_emoji = {"NORMAL": "🟢", "WATCHING": "🟡", "ALERT": "🟠", "CRITICAL": "🔴"}

    lines = [
        f"{level_emoji.get(level, '⚪')} 金价监控 | {now.strftime('%H:%M:%S')} | 模式: {level}",
        f"💰 {price_info['price']:.2f}元/克 ({price_info['change_pct']:+.2f}%)",
    ]

    if cost_basis:
        pnl = (price_info["price"] - cost_basis) / cost_basis * 100
        lines.append(f"📊 成本: {cost_basis:.0f} | 盈亏: {pnl:+.1f}%")

    if old_level != level:
        lines.append(f"⚡ 监控频率调整: {old_level} → {level}")
        intervals = {l: f"{v//60}分{v%60}秒" for l, v in MIN_INTERVALS.items()}
        lines.append(f"   当前检查间隔: {intervals.get(level, '?')}")

    if alerts:
        lines.append("")
        for a in alerts:
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

    # 5. 🆕 下跌原因分析 — 检测是否是机构在抛售
    #    只在价格下跌且有告警时触发, 结果缓存30分钟
    has_alerts = len(alerts) > 0
    drop_reason = None
    if has_alerts and price_info["change_pct"] < -0.5:
        drop_reason = _analyze_drop_reason(state)
        # 注入操作建议
        if drop_reason.get("institutional_selling"):
            drop_reason["action_hint"] = "机构在跑, 你也应该考虑减仓, 不要死扛"
        elif drop_reason.get("category") == "macro":
            drop_reason["action_hint"] = "非机构抛售, 观察宏观事件(ECB/FOMC/PCE)明朗后再决定"

    # 6. 通知
    should_output = has_alerts or level_changed or new_level != "NORMAL"

    if should_output:
        card = _format_card(new_level, old_level, price_info, cost_basis, alerts, state, drop_reason)
        print(card, flush=True)  # 同时输出到 log 文件
        _send_alert(card)        # Hermes → 微信

    # 6. 更新状态
    now_iso = _now().isoformat()
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
