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
    "NORMAL": 300,  # 5分钟
    "WATCHING": 120,  # 2分钟
    "ALERT": 60,  # 1分钟
    "CRITICAL": 60,  # 1分钟 (但每次都推送)
}

# 状态转换阈值
ESCALATION_PCT = {
    "to_watching": 0.3,  # 跌 >0.3% → WATCHING
    "to_alert": 0.5,  # 跌 >0.5% → ALERT
    "to_critical": 0.5,  # ALERT状态下继续跌 0.5% → CRITICAL
    "de_escalation_pct": 0.5,  # 回升 >0.5% → 降一级
}

# 稳定回退时间 (秒)
STABLE_TIMEOUTS = {
    "WATCHING": 600,  # 10分钟稳定 → NORMAL
    "ALERT": 900,  # 15分钟稳定 → WATCHING
    "CRITICAL": 1200,  # 20分钟稳定 → ALERT
}

# 连续下跌阈值
CONSECUTIVE_DOWN_DAYS = 3

# 日内逆转阈值
INTRADAY_REVERSAL_PCT = 1.5

# 反弹消息中"本轮跌幅"上下文的最小跌幅 — 低于此值的分钟级抖动不解释
REBOUND_CONTEXT_MIN_DROP_PCT = 1.0

# 高点回撤检测
PEAK_DRAWDOWN_WINDOWS = [3, 5, 7, 14]
PEAK_DRAWDOWN_THRESHOLDS = [
    (0.03, "⚠️ 距{window}d高点回撤3%, 短线获利盘开始出逃"),
    (0.05, "🔶 距{window}d高点回撤5%, 中线资金在撤退"),
    (0.08, "🔴 距{window}d高点回撤8%, 趋势可能反转"),
]

# 成本逼近预警级别 (参照净保本价: 已扣 0.4% 卖出手续费)
COST_PROXIMITY_BANDS = [
    (0.03, "🔴 仅剩3%净盈利! 距净保本线一步之遥"),
    (0.02, "🚨 仅剩2%净盈利! 获利盘快速出逃"),
    (0.01, "💀 仅剩1%净盈利! 即将实亏"),
    (0.00, "❌ 跌破净保本线!"),
]

# 冷却: 告警推送最小间隔 (秒)
ALERT_COOLDOWN = {
    "NORMAL": 600,  # 10分钟
    "WATCHING": 300,  # 5分钟
    "ALERT": 120,  # 2分钟
    "CRITICAL": 60,  # 1分钟 (急跌时每次推送)
}

# 路径
PROJECT_ROOT = Path(
    os.environ.get(
        "GOLD_MINER_ROOT",
        str(Path(__file__).resolve().parents[1]),
    )
)
STATE_FILE = Path(
    os.environ.get(
        "ADAPTIVE_MONITOR_STATE",
        os.path.expanduser("~/.hermes/gold/adaptive_monitor_state.json"),
    )
)
PORTFOLIO_PATH = PROJECT_ROOT / "data/private/portfolio.yaml"
LOG_FILE = Path(
    os.environ.get(
        "ADAPTIVE_MONITOR_LOG",
        str(PROJECT_ROOT / "logs/adaptive_monitor.log"),
    )
)

# ═══════════════════════════════════════════════════════════════
# 机会提醒配置 (止盈/抄底) — 可选 data/private/opportunity_config.yaml 覆盖
# ═══════════════════════════════════════════════════════════════

OPP_DEFAULTS: dict = {
    "require_surge": True,  # 止盈是否需要急涨速度条件
    "breakout_lookback_days": 20,  # N日新高窗口
    "min_profit_pct": 0.05,  # 浮盈阈值
    "dip_lookback_days": 20,  # N日低点窗口
    "key_levels": [921.0, 850.0],  # 元/克; 921≈$4000/oz (USD/CNY≈7.16)
    "key_level_band_pct": 0.01,  # 关键价位带宽 ±1%
    "cooldown_take_profit_min": 60,
    "cooldown_dip_low_min": 60,
    "realert_move_pct": 0.01,  # 冷却内同向再走1%可再提醒
    "snapshot_stale_hours": 48,  # 信号快照过期阈值
    # 突破前兆 (Req1B 2026-08-11): 整数关口逼近 / 距N日高点≤1.5% → 变盘窗口预警
    "breakout_key_levels": [950.0, 1000.0],  # 突破前兆整数关口 (元/克)
    "breakout_level_band_pct": 0.01,  # 关口带宽 ±1%
    "breakout_high_lookback_days": 20,  # N日高点窗口
    "breakout_high_approach_pct": 0.015,  # 距N日高点≤1.5% 视为逼近
    "cooldown_breakout_min": 60,  # 突破预警冷却
    "cooldown_rebound_min": 60,  # 反弹通知冷却 (2026-08-12: 反弹持续时每5分钟推送触发 iLink 限流)
    "trend_high_window_polls": 12,  # 本轮高点采样窗口(轮询次数, 5min×12≈1h): 用窗口内最高价而非下跌前单一快照
    "cooldown_atr_sl_min": 60,  # ATR浮亏轨破位提醒冷却 (2026-08-14: 908破位清机动仓评估, 60min不重复)
    "atr_sl_break_key_levels": [
        908.0
    ],  # 自定义破位警戒位(元/克) 优先级高于动态ATR浮亏轨; 空列表=[只跟动态]
    "atr_sl_break_band_pct": 0.003,  # 破位带宽 ±0.3% (接近但不触发, 供"逼近"提示)
    "atr_sl_recovery_pct": 0.01,  # 破位后回升超1%再跌破才重提醒 (防跌穿后反复)
    "order_near_pct": 1.5,  # 条件单接近阈值: 距触发价≤1.5%提醒 (与盘前哨兵同款)
    "cooldown_order_prox_min": 60,  # 条件单接近提醒冷却 (防价位居阈值带内反复横跳刷屏)
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


def _is_accum_trading_time() -> bool:
    """民生积存金是否处于交易时段 (交易日 9:05 — 次日 02:00).

    休市期间价格冻结, 价格类检查无新信号; 门禁在 main() 入口使用,
    防止同一冻结价反复触发 cost_proximity/rebound 等告警推送 (2026-08-16).
    """
    from gold_miner.data.trading_hours import is_accumulation_trading_time

    return is_accumulation_trading_time(_now())


# ═══════════════════════════════════════════════════════════════
# 价格获取
# ═══════════════════════════════════════════════════════════════


def _fetch_price() -> dict | None:
    """获取积存金当前价 — jdgold 主源 → latestPrice H5 兜底 (收口至 jdgold_client)."""
    from gold_miner.data.jdgold_client import fetch_accumulation_quote

    return fetch_accumulation_quote()


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


def _position_grams() -> float:
    """当前持仓克数; 文件缺失/解析失败视为 0 (空仓)."""
    p = _load_portfolio()
    if not p:
        return 0.0
    try:
        return float(p["positions"]["gold_jd"].get("grams", 0) or 0)
    except (KeyError, ValueError, TypeError, AttributeError):
        return 0.0


def _get_cost_basis() -> float | None:
    """成本基准价; 空仓 (grams<=0) 返回 None.

    2026-08-21 修复: 清仓后 portfolio.yaml 的 avg_cost 仍保留作历史参考,
    若只读 avg_cost 会误判"有仓"→ 微信推送仍展示浮盈/ATR。持仓存在性以 grams 为准。
    """
    if _position_grams() <= 0:
        return None
    p = _load_portfolio()
    if not p:
        return None
    try:
        return float(p["positions"]["gold_jd"]["avg_cost"])
    except (KeyError, ValueError, TypeError):
        return None


def _get_sell_fee_pct() -> float:
    """卖出费率(小数): 民生积存金 0.4% → 0.004. 读 portfolio.yaml sell_fee_pct."""
    p = _load_portfolio()
    if not p:
        return 0.0
    try:
        return float(p["positions"]["gold_jd"].get("sell_fee_pct", 0.0)) / 100
    except (KeyError, ValueError, TypeError, AttributeError):
        return 0.0


def _net_breakeven(cost_basis: float | None, sell_fee: float) -> float | None:
    """净保本价 — 卖出扣费后真正回本的价格 (r032)."""
    if cost_basis is None:
        return None
    return cost_basis / (1 - sell_fee) if sell_fee > 0 else cost_basis


def _get_stop_context(current: float) -> dict:
    """急涨/急跌时的风控上下文: ATR止盈位 + 硬止损 + 相对位置.

    用于在价格急变时提示"离止盈位/止损位还有多远", 让用户知道风险/机会临近.
    ATR 计算失败时静默降级 (返回空).
    """
    ctx: dict = {
        "atr_stop": 0.0,
        "atr_take_profit": 0.0,
        "atr_stop_loss": 0.0,
        "hard_stop": 0.0,
        "secondary_stop": 0.0,
        "to_atr_pct": None,
        "to_hard_pct": None,
        "to_secondary_pct": None,
        "tactical_g": 0.0,
    }
    p = _load_portfolio()
    if not p:
        return ctx
    # 空仓 (grams<=0): 无持仓, ATR止盈/止损与硬止损均无意义 → 直接返回空 ctx
    # (2026-08-21 修复: 清仓后不再误推 ATR 线。提前返回也避免拉取 JD K 线做无谓计算)
    if _position_grams() <= 0:
        return ctx
    try:
        gold = p["positions"]["gold_jd"]
        ctx["hard_stop"] = float(gold.get("hard_stop", 0) or 0)
        ctx["secondary_stop"] = float(gold.get("secondary_stop", 0) or 0)
        split = gold.get("split", {}) if isinstance(gold.get("split"), dict) else {}
        ctx["tactical_g"] = float(split.get("tactical", 0) or 0)
    except (KeyError, ValueError, TypeError):
        pass

    # ATR 移动止盈位 (复用 trailing_stop 逻辑)
    try:
        from gold_miner.data.jd_accumulation_gold import JdAccumulationGoldFetcher
        from gold_miner.strategy.trailing_stop import ATRTrailingStop

        jd = JdAccumulationGoldFetcher(bank="MS")
        df = jd.fetch(days=90)
        if df is not None and len(df) >= 14:
            cost_basis = _get_cost_basis()
            sell_fee = _get_sell_fee_pct()
            breakeven = _net_breakeven(cost_basis, sell_fee)
            hard = ctx["hard_stop"] or None
            entry_date = None
            if p and "positions" in p:
                try:
                    entry_date = p["positions"]["gold_jd"].get("entry_date")
                except (KeyError, TypeError):
                    pass
            ts = ATRTrailingStop(
                atr_period=14,
                profit_multiplier=2.5,
                loss_multiplier=3.0,
                cost_basis=cost_basis,
                hard_stop_price=hard,
                profit_action="reduce_half",
                loss_action="reduce_half",
                sell_fee_pct=sell_fee,
                entry_date=entry_date,
            )
            signal = ts.calculate(df)
            ctx["atr_stop"] = float(getattr(signal, "stop_price", 0) or 0)
            # ATR止盈 (浮盈轨): 最高 - profit_multiplier×ATR, 不低于分档锁利底线 (r025)
            high = float(getattr(signal, "highest_high", 0) or 0)
            atr_val = float(getattr(signal, "atr", 0) or 0)
            pmult = float(getattr(signal, "profit_multiplier", 2.5) or 2.5)
            lmult = float(getattr(signal, "loss_multiplier", 3.0) or 3.0)
            lock_floor = getattr(signal, "profit_lock_floor", None)
            if high > 0 and atr_val > 0:
                tp = high - pmult * atr_val
                if lock_floor is not None:
                    tp = max(tp, lock_floor)
                elif breakeven:
                    tp = max(tp, breakeven)
                ctx["atr_take_profit"] = round(tp, 2)
            # ATR止损 (浮亏轨): 成本 - loss_multiplier×ATR, 不低于硬止损
            if cost_basis is not None and atr_val > 0:
                sl = cost_basis - lmult * atr_val
                if hard:
                    sl = max(sl, hard)
                ctx["atr_stop_loss"] = round(sl, 2)
    except Exception:
        pass

    # 距离计算
    if ctx["atr_stop"] > 0 and current > 0:
        ctx["to_atr_pct"] = (current - ctx["atr_stop"]) / ctx["atr_stop"] * 100
    if ctx["hard_stop"] > 0 and current > 0:
        ctx["to_hard_pct"] = (current - ctx["hard_stop"]) / ctx["hard_stop"] * 100
    if ctx["secondary_stop"] > 0 and current > 0:
        ctx["to_secondary_pct"] = (
            (current - ctx["secondary_stop"]) / ctx["secondary_stop"] * 100
        )
    return ctx


def _format_stop_context(ctx: dict) -> str:
    """把风控上下文转成一行人话提示."""
    parts: list[str] = []
    if ctx["atr_stop"] > 0 and ctx["to_atr_pct"] is not None:
        if ctx["to_atr_pct"] <= 0:
            parts.append(f"🔴 已跌破ATR止盈位 {ctx['atr_stop']:.0f}, 按r025减仓一半")
        elif ctx["to_atr_pct"] <= 3:
            parts.append(
                f"⚠️ 逼近ATR止盈位 {ctx['atr_stop']:.0f} (仅剩{ctx['to_atr_pct']:.1f}%)"
            )
        elif ctx["to_atr_pct"] <= 8:
            parts.append(
                f"🎯 距ATR止盈位 {ctx['atr_stop']:.0f} 还有 {ctx['to_atr_pct']:.1f}%"
            )
    if ctx["secondary_stop"] > 0 and ctx["to_secondary_pct"] is not None:
        if 0 < ctx["to_secondary_pct"] <= 5:
            parts.append(
                f"⚠️ 逼近二级止损 {ctx['secondary_stop']:.0f} (仅剩{ctx['to_secondary_pct']:.1f}%)"
            )
        elif ctx["to_secondary_pct"] <= 0:
            parts.append(f"🔴 已跌破二级止损 {ctx['secondary_stop']:.0f}, 检查条件单")
    if ctx["hard_stop"] > 0 and ctx["to_hard_pct"] is not None:
        if 0 < ctx["to_hard_pct"] <= 8:
            parts.append(
                f"🚨 逼近硬止损 {ctx['hard_stop']:.0f} (仅剩{ctx['to_hard_pct']:.1f}%)"
            )
        elif ctx["to_hard_pct"] <= 0:
            parts.append(f"🚨 已跌破硬止损 {ctx['hard_stop']:.0f}, 立即清仓")
    return " · ".join(parts) if parts else ""


def _format_atr_levels(ctx: dict, current: float) -> list[str]:
    """ATR 止盈/止损状态行 (r025) — 每次监控卡片都带上, 说明当前价距两条线多远.

    ATR止盈 = 浮盈轨 (最高价 - 2.5×ATR, 保净本); ATR止损 = 浮亏轨 (成本 - 3.0×ATR).
    与 _format_stop_context 的区别: 后者只在价格逼近/跌破时才提示, 本函数常驻.
    返回两行 (止盈/止损各一行), 由 _format_card 逐行带 🎯 前缀输出.
    """
    lines: list[str] = []
    tp = ctx.get("atr_take_profit", 0.0) or 0.0
    sl = ctx.get("atr_stop_loss", 0.0) or 0.0
    if tp > 0 and current > 0:
        lines.append(
            f"ATR止盈：{tp:.1f}元，当前价距止盈位{(current - tp) / tp * 100:+.1f}%"
        )
    if sl > 0 and current > 0:
        lines.append(
            f"ATR止损：{sl:.1f}元，当前价距止损位{(current - sl) / sl * 100:+.1f}%"
        )
    return lines


def _get_historical(days: int = 30) -> list[dict]:
    """获取积存金历史."""
    try:
        from gold_miner.data.jd_accumulation_gold import JdAccumulationGoldFetcher

        f = JdAccumulationGoldFetcher(bank="MS")
        df = f.fetch(days=days)
        if df is None or df.empty:
            return []
        return [
            {
                "date": (
                    str(row["timestamp"].date())
                    if hasattr(row["timestamp"], "date")
                    else str(row["timestamp"])[:10]
                ),
                "close": float(row["close"]),
            }
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
    "trend_low": None,  # 本次下跌的最低点
    "trend_high": None,  # 下跌起点(阶段高点, 取最近N次轮询最高价)
    "recent_high": None,  # 最近 N 次轮询的最高价 (定位本轮真实峰值)
    "recent_high_polls": 0,  # recent_high 距今轮询数 (超过窗口则过期重启)
    "prev_change_pct": 0.0,  # 上次检查的涨跌幅
    # 机会提醒 (止盈/抄底)
    "tp_alert_at": None,
    "tp_alert_price": None,
    "dip_alert_at": None,
    "dip_alert_price": None,
    "in_band_levels": [],
    # 突破前兆 (Req1B 2026-08-11)
    "breakout_near_levels": [],  # 当前在关口下轨带内的价位列表
    "breakout_alert_at": None,
    "breakout_alert_price": None,
    # 进行中事件去重 (2026-08-13): 同一事件窗口内只推送一次的 key 列表
    "ongoing_notified": [],
    # ATR 浮亏轨破位去重 (2026-08-14): 跌破 908 后冷却期内不重复推送
    "atr_sl_break_at": None,
    "atr_sl_break_price": None,
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
                    downgrade = {
                        "CRITICAL": "ALERT",
                        "ALERT": "WATCHING",
                        "WATCHING": "NORMAL",
                    }
                    return downgrade.get(level, level)
            except (ValueError, TypeError):
                pass

    return level  # 保持不变


# ═══════════════════════════════════════════════════════════════
# 检测逻辑
# ═══════════════════════════════════════════════════════════════


def _check_cost_proximity(current: float, cost: float) -> dict | None:
    """成本逼近检测. cost 传入净保本价(扣卖出手续费后的回本线), 不是毛成本价."""
    if current <= cost:
        loss_pct = (cost - current) / cost * 100
        return {
            "type": "cost_below",
            "message": f"❌ 跌破净保本线 {cost:.2f}元! 当前 {current:.2f}, 卖出即实亏 {loss_pct:.1f}%",
            "severity": "CRITICAL",
        }
    profit_margin = (current - cost) / cost
    for threshold, msg in COST_PROXIMITY_BANDS:
        if profit_margin <= threshold:
            return {
                "type": "cost_proximity",
                "message": f"{msg} (净保本线 {cost:.2f}元, 当前 {current:.2f}, 净盈利仅剩 {profit_margin*100:.1f}%)",
                "severity": "HIGH" if threshold <= 0.02 else "MEDIUM",
            }
    return None


def _check_atr_stop_break(
    current: float, stop_ctx: dict, state: dict, cfg: dict
) -> dict | None:
    """ATR 浮亏轨破位检测 — 跌破 ATR 止损位(浮亏轨)时推送微信提醒.

    2026-08-14: 用户机动仓已降至 7.13g, 不再挂 908 卖出条件单(克数太小, 摩擦>保护),
    改用监控脚本对 ATR 浮亏轨(成本-3×ATR≈908)破位做提醒, 触发时人工决定是否清机动仓.

    破位判定: 现价 ≤ atr_stop_loss(浮亏轨). 但注意现价在净保本上方时走浮盈轨,
    stop_ctx["atr_stop_loss"] 恒为浮亏轨(成本-3ATR), 是保守止损位.
    去重: 同一次破位只在冷却期内推一次 (ATR_SL_COOLDOWN_SEC).

    返回 None 表示未破位/已冷却.
    """
    atr_sl = stop_ctx.get("atr_stop_loss") or 0.0
    if atr_sl <= 0 or current > atr_sl:
        return None
    if current <= atr_sl:
        # 已冷却: 冷却期内不重复推
        last_at = state.get("atr_sl_break_at")
        if last_at:
            try:
                from datetime import datetime as _dt

                elapsed = (_now() - _dt.fromisoformat(last_at)).total_seconds()
            except (ValueError, TypeError):
                elapsed = 99999.0
            if elapsed < cfg.get("cooldown_atr_sl_sec", 3600):
                return None
        # 记录本次破位时间
        state["atr_sl_break_at"] = _now().isoformat()
        return {
            "type": "atr_stop_break",
            "message": (
                f"🔴 跌破ATR浮亏轨止损位 {atr_sl:.2f}元/克! 当前 {current:.2f}\n"
                f"   💡 机动仓 {stop_ctx.get('tactical_g', 0):.2f}g 评估是否清仓; "
                f"880低吸档暂停(r021/r024, 不一边减一边接)"
            ),
            "severity": "CRITICAL",
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
                    "message": msg.format(window=window_days)
                    + f" | {window_days}日高点 {peak:.0f}→{current:.0f} ({drawdown*100:.1f}%)",
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
        total_change = (
            (closes[-1] - closes[-1 - down_count]) / closes[-1 - down_count] * 100
        )
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


def _check_intraday_reversal(
    change_pct: float, prev_close: float, current: float
) -> dict | None:
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


def _check_rebound(
    current: float,
    state: dict,
    cost_basis: float | None = None,
    cfg: dict | None = None,
) -> dict | None:
    """检测下跌后的反弹.

    触发条件: 之前在下行 (trend_low 存在且 < trend_high) + 当前在回升.
    返回反弹摘要: 从低点回升幅度/百分比 + 收复进度 (成本信息由卡片header统一展示).

    cfg: 机会提醒配置 (反弹冷却 cooldown_rebound_min). 为 None 时用 OPP_DEFAULTS.
    """
    trend_low = state.get("trend_low")
    trend_high = state.get("trend_high")
    if not trend_low or not trend_high:
        return None

    # 反弹冷却: 冷却期内不重复推送反弹进展 (2026-08-12)
    # 背景: 反弹持续 (低点→现价逐级收复) 时 _check_rebound 每次都返回非空,
    #   main() 里 should_output 含 "反弹始终通知" → 每5分钟推送一次微信.
    #   叠加 Hermes cron 每5分钟投递, 触发 iLink 限流 (ret=-2 prepare failed),
    #   导致早上黄金报告/创业日报等所有微信推送投递失败.
    # 冷却期内反弹进展 (74%→76%→78%) 不值得打扰用户, 价格大幅异动由 surge 等告警覆盖.
    cfg = cfg or OPP_DEFAULTS
    last_at = state.get("rebound_alert_at")
    if last_at:
        try:
            elapsed_min = (
                _now() - datetime.fromisoformat(last_at)
            ).total_seconds() / 60
        except (ValueError, TypeError):
            elapsed_min = 999.0
        if elapsed_min < cfg.get("cooldown_rebound_min", 60):
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

    # 主行: 低点→现价, 与卡片 💰 行同精度 (2位小数), 只陈述事实
    lines = [
        f"📈 低点反弹 +{rebound_pct:.1f}% | {trend_low:.2f} → {current:.2f} (回升 ¥{rebound:.2f})",
    ]

    # 上下文行: 仅真实下跌 (≥阈值) 才解释收复进度; 分钟级微跌不制造噪音
    if drop_pct >= REBOUND_CONTEXT_MIN_DROP_PCT and drop_total > 0:
        if rebound >= drop_total:
            progress = "已收复全部跌幅 ✅"
        else:
            progress = f"已收复 {rebound/drop_total*100:.0f}%"
        lines.append(
            f"   本轮 {trend_high:.2f} → {trend_low:.2f} 跌 {drop_pct:.1f}%, {progress}"
        )

    return {
        "type": "rebound",
        "message": "\n".join(lines),
        "severity": "MEDIUM" if rebound_pct > 1.0 else "INFO",
    }


# ═══════════════════════════════════════════════════════════════
# 进行中高影响事件检测
# ═══════════════════════════════════════════════════════════════


def _check_ongoing_events(state: dict | None = None) -> list[dict]:
    """检查是否有正在进行中的高/极影响宏观事件 (FOMC/CPI/PCE/非农等).

    窗口: 事件时间前后 N 小时, extreme 事件 ±2h, high 事件 ±1h.
    用于 main() 中触发推送, 确保 Hermes cron 在重大数据发布时通知到微信.

    🔴 2026-08-13 系统性修复 (结果已出仍反复提醒 bug):
      1. 结果已出的事件 (actual 非空) → 不再提醒. 用户已知结果, 反复推
         "正在进行…结果已出✓" 是纯噪音.
      2. MONITOR 观测事件 → 不在此提醒. 观测/评估触发器由分析 pipeline 的
         get_active_monitors()/close_monitor() 评估, 价格监控只通知"重大数据
         发布"本身.
      3. 同一事件窗口内只推送一次 (state 持久化已推送 key), 防 5 分钟粒度
         在 ±1h/±2h 窗口内反复推送.
    """
    try:
        from gold_miner.data.calendar import EventCalendar, EventImpact, EventType

        cal = EventCalendar()
        now = datetime.now(BEIJING)  # 北京时间用于比较
        events: list[dict] = []

        # 已推送事件 key (跨 cron 进程持久化在 state 文件)
        notified = set(state.get("ongoing_notified", []) or []) if state else set()

        for e in cal.events:
            if not e.scheduled_at:
                continue
            # 1) 结果已出 → 不提醒 (反复推送"正在进行…结果已出"是 bug)
            if e.actual:
                continue
            # 2) 观测/评估触发器 → 交给分析 pipeline, 价格监控不推
            if e.event_type == EventType.MONITOR:
                continue
            # 北京时间差值
            delta_h = abs((e.scheduled_at - now).total_seconds() / 3600)
            # 窗口: extreme ±2h, high ±1h, 其余跳过
            if e.impact == EventImpact.EXTREME:
                window = 2.0
            elif e.impact == EventImpact.HIGH:
                window = 1.0
            else:
                continue
            if delta_h > window:
                continue

            # 3) 同一事件窗口内只推一次
            key = f"{e.name}|{e.scheduled_at.isoformat()}"
            if key in notified:
                continue

            from gold_miner.data.calendar_time_rules import dual_clock_str

            clock = dual_clock_str(e.scheduled_at)
            when = "即将" if e.scheduled_at > now else "正在进行"
            notified.add(key)
            events.append(
                {
                    "type": "ongoing_event",
                    "message": f"📅 {when}: {e.name} | {clock}",
                    "severity": "HIGH",
                    "_event_name": e.name,
                    "_impact": e.impact.value,
                }
            )
        if state is not None:
            # 只保留最近通知过的 key, 防无限增长
            state["ongoing_notified"] = sorted(notified)[-60:]
        return events
    except Exception:
        return []


def _update_trend_bookkeeping(
    current: float, prev_price: float | None, state: dict, cfg: dict | None = None
) -> None:
    """维护反弹检测所需的趋势状态.

    下跌中追踪低点; 回升后当价格远离低点 >2% 时重置趋势.
    本轮高点 (trend_high) 取「最近 N 次轮询的最高价」而非下跌前单一快照,
    避免 5 分钟轮询粒度漏掉两次采样之间的真实峰值 (曾出现 958.04 只反映
    下跌前一拍快照价, 而真实阶段高点可能更高).
    """
    if prev_price is None or prev_price <= 0:
        # 无前价(首轮/重启)时只播种近期高点, 不判定趋势
        if current > 0:
            state["recent_high"] = current
            state["recent_high_polls"] = 0
        return

    cfg = cfg or OPP_DEFAULTS
    window = int(cfg.get("trend_high_window_polls", 12))

    change_pct = (current - prev_price) / prev_price * 100

    # 维护短期高点窗口: 记录最近 window 次轮询内的最高价 (含当前价)
    recent_high = state.get("recent_high")
    recent_polls = state.get("recent_high_polls", 0)
    if recent_high is None or current >= recent_high:
        recent_high = current
        recent_polls = 0
    else:
        recent_polls += 1
        if recent_polls > window:
            # 窗口过期: 用当前价重启, 防止把久远高点算作"本轮"起点
            recent_high = current
            recent_polls = 0
    state["recent_high"] = recent_high
    state["recent_high_polls"] = recent_polls

    # 下跌中 → 更新低点 + 记录阶段高点 (取窗口最高价, 更贴近真实峰值)
    if change_pct < -0.15:
        if state.get("trend_low") is None or current < state["trend_low"]:
            state["trend_low"] = current
        if state.get("trend_high") is None:
            state["trend_high"] = max(prev_price, recent_high)

    # 回升 + 已远离低点 >2% → 这波跌完了, 重置
    if change_pct > 0.15 and state.get("trend_low"):
        recovery_pct = (current - state["trend_low"]) / state["trend_low"] * 100
        if recovery_pct > 2.0:
            state["trend_low"] = None
            state["trend_high"] = None
            state["recent_high"] = None
            state["recent_high_polls"] = 0


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


def _check_breakout_approach(
    current: float,
    state: dict,
    historical: list[dict],
    cfg: dict,
) -> dict | None:
    """突破前兆: 价格升入整数关口下轨带(带外→带内) 或 逼近N日高点(未破高).

    2026-08-11 Req1B: FOMO 暴涨提前捕捉 — 只出预警, 不自动挂单,
    人工决策是否提前布局 (符合 r029 不追涨纪律).

    每次调用更新 state["breakout_near_levels"].
    """
    if len(historical) < 10:
        return None

    band = cfg["breakout_level_band_pct"]
    levels = [float(lv) for lv in cfg["breakout_key_levels"]]

    # 1) 整数关口带: 价格在下轨带内且未破 (从下方逼近, 升入带内)
    #    与 dip_buy 的对称检测不同 — 这里只关心 current < level (未破关口)
    near_now = [lv for lv in levels if lv * (1 - band) <= current < lv]
    prev_near = [float(lv) for lv in state.get("breakout_near_levels", [])]
    entered = [lv for lv in near_now if lv not in prev_near]
    state["breakout_near_levels"] = near_now

    # 2) 逼近N日高点 (仅"未突破"才报, 防止与 take_profit_breakout 重叠)
    lookback = int(cfg["breakout_high_lookback_days"])
    window = historical[-lookback:] if len(historical) >= lookback else list(historical)
    high_n = max(p["close"] for p in window)
    approach_high = (
        high_n > 0
        and current <= high_n
        and (high_n - current) / high_n <= cfg["breakout_high_approach_pct"]
    )

    if not entered and not approach_high:
        return None
    return {
        "type": "breakout_approach",
        "entered_level": entered[0] if entered else None,
        "approach_high": approach_high,
        "high_n": high_n,
        "lookback": lookback,
        "current": current,
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


# 条件单类型 → 中文 (供接近提醒展示)
_ORDER_TYPE_CN: dict[str, str] = {
    "limit_buy": "限价买入",
    "limit_sell": "限价卖出",
    "take_profit": "止盈",
    "stop_loss": "止损",
    "oco": "OCO止盈止损",
}


def _check_order_proximity(current: float, state: dict, cfg: dict) -> list[dict]:
    """条件单接近提醒 — 现价距活跃买入/卖出条件单触发价 ≤ order_near_pct 时推送.

    复用哨兵 check_order_proximity (含 OCO 双腿), 与盘前哨兵口径一致.
    冷却: 每单 60min (state.order_prox_at_<id>), 防价位居阈值带内反复横跳刷屏.
    休市静默由 main() 门禁统一处理 (休市期不进入本函数).
    """
    if current <= 0 or not ORDERS_PATH.exists():
        return []
    try:
        from gold_miner.sentinel.orders import check_order_proximity, load_active_orders

        orders = load_active_orders(ORDERS_PATH)
        if not orders:
            return []
        near_pct = float(cfg.get("order_near_pct", 1.5))
        cooldown_sec = int(cfg.get("cooldown_order_prox_min", 60)) * 60
        alerts: list[dict] = []
        for o, dist in check_order_proximity(orders, current, near_pct)[:3]:  # 最多3条
            # 冷却去重: 同单冷却期内不重复提醒
            key = (
                f"order_prox_at_{o.id}" if o.id else f"order_prox_at_{o.trigger_price}"
            )
            last_at = state.get(key)
            if last_at:
                try:
                    elapsed = (_now() - datetime.fromisoformat(last_at)).total_seconds()
                except (ValueError, TypeError):
                    elapsed = cooldown_sec + 1
                if elapsed < cooldown_sec:
                    continue
            type_cn = _ORDER_TYPE_CN.get(o.type, o.type)
            direction_sym = "↓" if o.direction == "卖出" else "↑"
            qty = f" ×{o.quantity_g:g}g" if o.quantity_g else ""
            msg = (
                f"🎯 条件单接近: {type_cn}@{o.trigger_price:.0f}元 "
                f"({direction_sym}{dist:.1f}%){qty} — 当前 {current:.0f}元"
            )
            alerts.append(
                {"type": "order_proximity", "message": msg, "severity": "MEDIUM"}
            )
            state[key] = _now().isoformat()
        return alerts
    except Exception:
        return []  # 读取/导入失败 → 静默降级, 不阻断监控


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
        from gold_miner.data.calendar import EventCalendar, EventImpact, EventType

        cal = EventCalendar()
        # 只列真实"待落地"的数据事件: 排除 MONITOR 观测 (由分析 pipeline 评估, 不是即将公布的数据)
        # 和 actual 已填的结果已出事件. 与 _check_ongoing_events 同一套过滤 (2026-08-13 系统修复).
        ev["events"] = [
            {"name": e.name, "time": e.beijing_time_str, "impact": e.impact.value}
            for e in cal.get_upcoming(days=2, min_impact=EventImpact.MEDIUM)
            if e.event_type != EventType.MONITOR and not e.actual
        ][:3]
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
    snap_line = (
        f"信号快照({snap['timestamp'][5:16].replace('T', ' ')})：多{bull}维 空{bear}维"
    )
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
        elif clarity == "conflicted":
            reasons.append(f"{snap_line}，多空分歧，落袋为安")
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
    resonance = (
        bool(candidate.get("broke_low")) and candidate.get("key_level") is not None
    )
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


_COOLDOWN_KEYS = {
    "tp": "cooldown_take_profit_min",
    "dip": "cooldown_dip_low_min",
    "breakout": "cooldown_breakout_min",
}


def _opp_cooldown_ok(
    state: dict, prefix: str, current: float, direction: str, cfg: dict
) -> bool:
    """冷却判定: 过冷却期 或 冷却内同向价格再走 realert_move_pct."""
    last_at = state.get(f"{prefix}_alert_at")
    if not last_at:
        return True
    try:
        elapsed_min = (_now() - datetime.fromisoformat(last_at)).total_seconds() / 60
    except (ValueError, TypeError):
        return True
    minutes = cfg.get(_COOLDOWN_KEYS.get(prefix, "cooldown_dip_low_min"), 60)
    if elapsed_min >= minutes:
        return True
    last_price = state.get(f"{prefix}_alert_price")
    if last_price:
        move = (current - last_price) / last_price
        if direction == "up" and move >= cfg["realert_move_pct"]:
            return True
        if direction == "down" and move <= -cfg["realert_move_pct"]:
            return True
    return False


def _build_opp_alert(
    action: str,
    candidate: dict,
    verdict: dict,
    ev: dict,
    current: float,
    cost_basis: float | None,
) -> dict:
    """构造机会提醒告警 (含📋理由节). veto 时返回 vetoed 类型."""
    strength = verdict["strength"]
    lines: list[str] = []

    if action == "take_profit":
        if strength == "veto":
            return {
                "type": "take_profit_vetoed",
                "message": f"🔕 急涨破{candidate['lookback']}日新高，但{verdict['veto_note']}",
                "severity": "INFO",
            }
        profit_pct = candidate["profit_pct"] * 100
        lines.append(
            f"🎯 止盈机会 | 急涨破{candidate['lookback']}日新高 "
            f"{candidate['high_n']:.0f}→{current:.2f}，浮盈{profit_pct:+.1f}%"
        )
        lines.append("   💡 建议: 卖出机动仓15g的1/3~1/2锁定利润，核心仓不动")
        lines.append("   📏 纪律: 卖出后若继续新高不追回，等回调再接")
    elif action == "breakout_approach":
        if strength == "veto":
            return {
                "type": "breakout_approach_vetoed",
                "message": f"🔕 逼近关口/前高，但{verdict['veto_note']}",
                "severity": "INFO",
            }
        if candidate.get("entered_level") and candidate.get("approach_high"):
            cond = (
                f"逼近整数关口 {candidate['entered_level']:.0f} "
                f"+ 距{candidate['lookback']}日高点 {candidate['high_n']:.0f} "
                f"仅 {candidate['high_n'] - candidate['current']:.2f} 元/克(现价 {candidate['current']:.2f})"
            )
        elif candidate.get("entered_level"):
            cond = f"价格升入整数关口 {candidate['entered_level']:.0f} 带 (现价 {candidate['current']:.2f})"
        else:
            cond = f"逼近{candidate['lookback']}日高点 {candidate['high_n']:.0f}，仅差 {candidate['high_n'] - candidate['current']:.2f} 元/克"
        lines.append(f"🚀 突破前兆 (变盘窗口开启) | {cond}")
        lines.append("   💡 建议: 仅预警，人工决策是否提前布局，不自动挂单")
        lines.append("   📏 纪律: 突破未确认前不加仓，等放量突破站稳后再动作")
    else:
        if strength == "veto":
            return {
                "type": "dip_buy_vetoed",
                "message": f"🔕 价格触及买入区，但{verdict['veto_note']}",
                "severity": "INFO",
            }
        if candidate.get("broke_low") and candidate.get("key_level") is not None:
            cond = (
                f"破{candidate['lookback']}日低点 {candidate['low_n']:.0f} "
                f"+ 关键价位{candidate['key_level']:.0f}共振"
            )
        elif candidate.get("broke_low"):
            cond = f"跌破{candidate['lookback']}日低点 {candidate['low_n']:.0f}"
        else:
            cond = f"接近关键价位 {candidate['key_level']:.0f}"
        lines.append(f"🛒 买入机会 | {cond}，当前 {current:.2f}")
        lines.append("   💡 建议: 分批低吸(参考活跃条件单)，单品种仓位≤20万上限")

    if verdict["reasons"]:
        lines.append("   📋 理由(客观事实):")
        for r in verdict["reasons"]:
            lines.append(f"   • {r}")
    if ev.get("active_orders"):
        lines.append(f"   📑 活跃条件单: {'；'.join(ev['active_orders'][:3])}")
    label = {"strong": "强", "medium": "中", "weak": "弱"}.get(strength, strength)
    lines.append(f"   理由强度: {label}")

    alert_type = {
        "take_profit": "take_profit_breakout",
        "breakout_approach": "breakout_approach",
    }.get(action, "dip_buy_opportunity")
    return {
        "type": alert_type,
        "message": "\n".join(lines),
        "severity": "HIGH" if strength == "strong" else "MEDIUM",
    }


# 通知: macOS 桌面通知 (osascript) + Hermes weixin (如已配置)
# 无告警时静默退出, 有告警时多渠道推送


_WEIXIN_TARGET = os.environ.get(
    "GOLD_WEIXIN_TARGET", "weixin:o9cq80613_z9qxqE69G94f-0CzGk@im.wechat"
)


def _push_weixin(text: str) -> bool:
    """推送 markdown 到 Hermes 微信 (hermes send, 服务器上可用).

    2026-08-11 修复: 之前 _send_alert 只有 macOS osascript, 服务器 (Ubuntu)
    上 osascript 不存在 → 微信推送静默失败。改用 hermes send 作为主通道,
    macOS osascript 仅保留为本地开发时的补充通知。
    """
    import shutil
    import subprocess

    if shutil.which("hermes") is None:
        return False
    try:
        r = subprocess.run(
            ["hermes", "send", "-t", _WEIXIN_TARGET, "-q", text],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return r.returncode == 0
    except Exception as e:
        print(f"⚠️ weixin 推送失败: {e}", file=sys.stderr)
        return False


def _send_alert(message: str) -> bool:
    """多渠道推送通知.

    优先级: Hermes weixin (hermes send, 服务器/本地均可) → macOS 通知 (osascript)。
    Hermes 微信为主通道 — 服务器 cron 场景下 macOS osascript 不可用。
    """
    # 2026-08-12 修复: Hermes cron --no-agent 模式 stdout 已投递微信,
    # 脚本内再 hermes send 会造成双投递 → 每5分钟×2次触发 iLink 限流。
    # hermes_wrapper_adaptive.py 设置 GOLD_MONITOR_STDOUT_DELIVERY=1, 此模式跳过 hermes send.
    if os.environ.get("GOLD_MONITOR_STDOUT_DELIVERY") == "1":
        return True
    import subprocess

    success = _push_weixin(message)

    # macOS 桌面通知 (本地开发补充, 服务器上 osascript 不存在自动跳过)
    try:
        # 截取第一行作为标题, 其余为内容
        lines = message.strip().split("\n")
        title = lines[0][:100] if lines else "金价监控"
        body = "\n".join(lines[1:5])[:200] if len(lines) > 1 else ""
        # 清理特殊字符防止 osascript 报错
        title_clean = title.replace('"', "'").replace("\\", "")
        body_clean = body.replace('"', "'").replace("\\", "")
        subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "{body_clean}" with title "{title_clean}" sound name "Glass"',
            ],
            capture_output=True,
            timeout=10,
        )
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
        _FLOW_CACHE_FILE.write_text(
            json.dumps(
                {
                    "cached_at": _now().isoformat(),
                    "result": result,
                },
                ensure_ascii=False,
            )
        )
    except Exception:
        pass


def _fmt_data_date(iso: str | None) -> str:
    """数据日期紧凑化: '2026-08-29T00:00:00' -> '8/29'; 解析失败原样前10位."""
    if not iso:
        return "?"
    try:
        d = datetime.fromisoformat(str(iso))
        return f"{d.month}/{d.day}"
    except ValueError:
        return str(iso)[:10]


def _analyze_drop_reason(state: dict) -> dict:
    """分析金价下跌驱动因素: 机构抛售 vs 宏观压制 vs 消息面.

    返回 {'category': str, 'institutional_selling': bool, 'detail': str,
          'short': str, 'fingerprint': str}
    category: 'institutional' | 'mixed' | 'macro' | 'degraded' | 'unknown'
    fingerprint: category + 两源数据日期, 供 main 比对 state.last_flow_fp
                 实现「结论未变化」去重 (COT周更/ETF日更, 期间结论天然不变).
    文案铁律: 注入实时数字+数据截至日期; 只陈述两通道机制, 不做「散户出逃」
    之类规则引擎给不出的归因断言.
    """
    # 1) 检查文件缓存 (跨 cron 进程)
    cached = _load_flow_cache()
    if cached:
        return cached

    result: dict = {
        "category": "unknown",
        "institutional_selling": False,
        "detail": "",
        "short": "",
    }

    # 数据源三态: 'ok' 有数据 / 'no_data' 无信号 / 'error' 拉取异常
    # (旧实现把「拿不到COT数据」当「COT未减仓」-> 单ETF源也敢下「COT未同步」结论)
    cot = {
        "status": "no_data",
        "selling": False,
        "score": 0.0,
        "net": None,
        "change": None,
        "date": None,
        "error": "",
    }
    etf = {
        "status": "no_data",
        "selling": False,
        "score": 0.0,
        "tonnes_delta": None,
        "holdings": None,
        "date": None,
        "error": "",
    }

    # 2) COT 机构持仓 - 周度, 判断聪明钱方向
    try:
        from gold_miner.signals.cot_signal import CotSignalGenerator

        for s in CotSignalGenerator().generate_signals():
            if "减仓" in s.name or "加仓" in s.name:
                m = s.metadata or {}
                cot.update(
                    status="ok",
                    score=s.score,
                    selling="减仓" in s.name and s.score < -0.3,
                    net=m.get("latest_net"),
                    change=m.get("change"),
                    date=m.get("report_date"),
                )
                break
    except Exception as e:
        cot["status"] = "error"
        cot["error"] = str(e)[:80]

    # 3) ETF 资金流 - 日度, 补充确认 (只认 GLD 真实持仓通道, 价格 proxy 不算)
    try:
        from gold_miner.signals.etf_flow_signal import EtfFlowSignalGenerator

        for s in EtfFlowSignalGenerator().generate_signals():
            if ("流出" in s.name or "流入" in s.name) and (s.metadata or {}).get(
                "is_real_flow"
            ):
                m = s.metadata or {}
                etf.update(
                    status="ok",
                    score=s.score,
                    selling="流出" in s.name and s.score < -0.3,
                    tonnes_delta=m.get("tonnes_delta"),
                    holdings=m.get("holdings_tonnes"),
                    date=m.get("as_of"),
                )
                break
    except Exception as e:
        etf["status"] = "error"
        etf["error"] = str(e)[:80]

    # 4) 通道事实描述 (数字+截至日期, 机器可核查)
    cot_date = _fmt_data_date(cot["date"])
    etf_date = _fmt_data_date(etf["date"])

    if cot["status"] == "ok":
        chg = cot["change"] if cot["change"] is not None else 0
        net = cot["net"] if cot["net"] is not None else 0
        cot_txt = (
            f"COT非商业净多{chg:+,}手(现{net:,}手, 截至{cot_date})"
            f"{'机构减仓' if cot['selling'] else '机构未转向'}"
        )
    elif cot["status"] == "error":
        cot_txt = f"COT源拉取异常({cot['error']})"
    else:
        cot_txt = "COT源无信号数据"

    if etf["status"] == "ok":
        td = etf["tonnes_delta"] if etf["tonnes_delta"] is not None else 0.0
        h = etf["holdings"] if etf["holdings"] is not None else 0.0
        etf_txt = (
            f"GLD持仓{td:+.2f}吨(现{h:.1f}吨, 截至{etf_date})"
            f"{'真实流出' if etf['selling'] else '未现流出'}"
        )
    elif etf["status"] == "error":
        etf_txt = f"ETF源拉取异常({etf['error']})"
    else:
        etf_txt = "ETF源无信号数据"

    # 5) 交叉验证判断 (仅两源都有数据才下完整结论)
    if cot["status"] == "ok" and etf["status"] == "ok":
        if cot["selling"] and etf["selling"]:
            result["institutional_selling"] = True
            result["category"] = "institutional"
            result["detail"] = (
                f"🔴 机构资金撤退确认: {cot_txt} + {etf_txt}, "
                "两通道一致看空, 聪明钱在卖, 建议跟随减仓别死扛"
            )
            result["short"] = "两通道一致: 机构在卖"
        elif cot["selling"]:
            result["institutional_selling"] = True
            result["category"] = "mixed"
            result["detail"] = (
                f"🟠 {cot_txt}, 但{etf_txt}. 仅COT通道减仓, "
                "部分机构撤退, 关注ETF是否跟进"
            )
            result["short"] = "仅COT通道减仓"
        elif etf["selling"]:
            result["category"] = "mixed"
            result["detail"] = (
                f"🟠 {etf_txt}, 但{cot_txt}. ETF通道流出与COT通道背离, "
                "规则仅监测这两条通道, 无法归因至机构整体行为, "
                "不构成'散户出逃'证据, 待新数据落地再评估"
            )
            result["short"] = "通道背离: ETF流出/COT未转向"
        else:
            result["category"] = "macro"
            result["detail"] = (
                f"🟡 两通道均无卖出信号: {cot_txt}, {etf_txt}. "
                "下跌驱动不在已监测资金通道, 更可能是宏观利率/美元/消息面, "
                "待FOMC/PCE明朗后再决定"
            )
            result["short"] = "资金通道无卖出, 疑宏观驱动"
    elif cot["status"] == "ok" or etf["status"] == "ok":
        # 单源可用 -> 结论降级, 明说缺哪个源
        failed = "COT" if cot["status"] != "ok" else "ETF"
        only = cot_txt if cot["status"] == "ok" else etf_txt
        result["category"] = "degraded"
        result["detail"] = (
            f"🟡 数据降级({failed}源不可用), 仅单通道: {only}. "
            "结论置信度低, 不作为减仓依据"
        )
        result["short"] = f"单通道({failed}源缺), 置信度低"
    else:
        result["category"] = "unknown"
        result["detail"] = "⚠️ COT/ETF数据源均不可用, 无法评估下跌驱动"
        result["short"] = "数据源均不可用"

    # 6) 指纹: 结论类别+两源数据日期 (新COT周报/新GLD日更才会变化)
    result["fingerprint"] = (
        f"{result['category']}|cot:{cot['date'] or cot['status']}"
        f"|etf:{etf['date'] or etf['status']}"
    )

    # 7) 缓存并返回
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
    stop_ctx: dict | None = None,
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

    # ── 成本/盈亏 (破净保本线时同行警示, 全卡片成本只说这一次) ──
    if cost_basis:
        price = price_info["price"]
        sell_fee = _get_sell_fee_pct()
        pnl = (price - cost_basis) / cost_basis * 100
        line = (
            f"📊 成本¥{cost_basis:.2f} | 浮{'盈' if pnl >= 0 else '亏'} {abs(pnl):.1f}%"
        )
        net_pnl = pnl
        if sell_fee > 0:
            net_breakeven = _net_breakeven(cost_basis, sell_fee)
            net_pnl = (price * (1 - sell_fee) - cost_basis) / cost_basis * 100
            line += f" | 净{net_pnl:+.1f}%(已扣{sell_fee*100:.1f}%卖出费) 净保本¥{net_breakeven:.2f}"
        if net_pnl < 0:
            line += " ⚠️ 已破净保本线, 卖出即实亏"
        lines.append(line)
    elif _position_grams() <= 0:
        # 空仓: 无持仓, 不展示浮盈/ATR (2026-08-21 清仓后误推持仓信息 bug 修复)
        lines.append("🈳 空仓 — 无持仓, 仅监控价格, 待回调择机重建(V9)")

    # ── ATR 止盈/止损 (r025) — 常驻, 无论是否有告警都带上 (止盈/止损各一行) ──
    if stop_ctx:
        for atr_line in _format_atr_levels(stop_ctx, price_info["price"]):
            lines.append(f"🎯 {atr_line}")

    # ── 趋势摘要: 从告警中提取去重展示 ──
    # 分类告警: 趋势类 (合并到摘要行) vs 事件类 (单独展示)
    alert_by_type = {a["type"]: a for a in alerts}

    # 趋势行: 连续下跌 + 高点回撤 → 合并为一句话
    trend_parts = []
    consecutive = alert_by_type.get("consecutive_down")
    peak = alert_by_type.get("peak_drawdown")

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

    # ── 突破前兆置顶展示 (Req1B 2026-08-11: FOMO暴涨提前捕捉核心预警) ──
    bk_alert = alert_by_type.get("breakout_approach")
    if bk_alert:
        lines.append("")
        lines.append(bk_alert["message"])

    # ── 频率调整 ──
    if old_level != level:
        lines.append(f"⚡ 监控频率调整: {old_level} → {level}")
        intervals = {l: f"{v//60}分{v%60}秒" for l, v in MIN_INTERVALS.items()}
        lines.append(f"   当前检查间隔: {intervals.get(level, '?')}")

    # ── 事件类告警: 仅展示新增信息的 (急变/逆转) ──
    # 已处理: consecutive_down, peak_drawdown, cost_proximity, cost_below, breakout_approach
    # 当趋势摘要已覆盖时, 急变/逆转信号冗余 → 跳过
    shown_types = {
        "consecutive_down",
        "peak_drawdown",
        "cost_proximity",
        "cost_below",
        "breakout_approach",
        "breakout_approach_vetoed",
    }
    if trend_parts:
        # 趋势摘要已覆盖下跌方向, price_surge + intraday_reversal 是重复信息
        shown_types.update({"price_surge", "intraday_reversal"})

    remaining = [a for a in alerts if a["type"] not in shown_types]
    if remaining:
        lines.append("")
        for a in remaining:
            lines.append(f"  {a['message']}")

    # 🆕 下跌原因分析 (unchanged=结论指纹与上次推送相同 -> 一行短格式, 不复读全文)
    if drop_reason and (drop_reason.get("detail") or drop_reason.get("short")):
        action = drop_reason.get("action_hint", "")
        lines.append("")
        if drop_reason.get("unchanged"):
            lines.append("━━━ 🔍 谁在卖？(与上次结论相同) ━━━")
            lines.append(drop_reason.get("short") or drop_reason.get("detail", ""))
        else:
            lines.append("━━━ 🔍 谁在卖？━━━")
            lines.append(drop_reason.get("detail", ""))
        if action:
            lines.append(f"  💡 {action}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════


def main() -> int:
    state = _load_state()

    # 0. 休市时段门禁 (2026-08-16): 积存金休市 → 价格冻结, 同一冻结价反复触发
    #    cost_proximity/rebound/surge 等告警是纯噪音 (每5分钟推一次微信).
    #    跳过全部价格类检查, 仅保留宏观事件提醒 (FOMC/CPI/PCE 可发生在任意钟点,
    #    如 FOMC 02:00 北京) — _check_ongoing_events 内部已按事件窗口去重.
    if not _is_accum_trading_time():
        ongoing = _check_ongoing_events(state)
        if ongoing:
            card = "📅 休市期宏观事件提醒\n" + "\n".join(a["message"] for a in ongoing)
            print(card, flush=True)
            _send_alert(card)
        _save_state(state)
        return 0

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
    historical = _get_historical(days=90)
    alerts: list[dict] = []

    # 4a. 价格急变 (所有级别都检测, 这是快速通道)
    # 风控上下文 (ATR止盈/止损距离) 延迟到输出时统一计算, 避免每次完整检查都拉取JD数据
    surge = _check_surge(current, state)
    if surge:
        alerts.append(surge)

    # 4b. 成本逼近 (用净保本价: 卖出扣 0.4% 手续费后真正回本的价格)
    if cost_basis:
        net_breakeven = _net_breakeven(cost_basis, _get_sell_fee_pct())
        cost_alert = _check_cost_proximity(current, net_breakeven)
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

    # 4f. 反弹检测 (下跌趋势中的回升) — 冷却期内不重复推送
    opp_cfg = _load_opp_config()
    rebound = _check_rebound(current, state, cost_basis, opp_cfg)
    if rebound:
        alerts.append(rebound)

    # 4g. 机会提醒 (止盈/抄底/突破前兆) — 触发层 + 理由引擎
    tp_candidate = _check_take_profit_breakout(
        current, historical, cost_basis, opp_cfg, surge
    )
    dip_candidate = _check_dip_buy_opportunity(current, state, historical, opp_cfg)
    bk_candidate = _check_breakout_approach(current, state, historical, opp_cfg)
    if tp_candidate or dip_candidate or bk_candidate:
        ev = _gather_evidence(current, historical, opp_cfg)
        if tp_candidate and _opp_cooldown_ok(state, "tp", current, "up", opp_cfg):
            verdict = _evaluate_reason("take_profit", tp_candidate, ev, opp_cfg)
            alerts.append(
                _build_opp_alert(
                    "take_profit", tp_candidate, verdict, ev, current, cost_basis
                )
            )
            state["tp_alert_at"] = _now().isoformat()
            state["tp_alert_price"] = current
        if dip_candidate and _opp_cooldown_ok(state, "dip", current, "down", opp_cfg):
            verdict = _evaluate_reason("dip_buy", dip_candidate, ev, opp_cfg)
            alerts.append(
                _build_opp_alert(
                    "dip_buy", dip_candidate, verdict, ev, current, cost_basis
                )
            )
            state["dip_alert_at"] = _now().isoformat()
            state["dip_alert_price"] = current
        if bk_candidate and _opp_cooldown_ok(state, "breakout", current, "up", opp_cfg):
            verdict = _evaluate_reason("breakout_approach", bk_candidate, ev, opp_cfg)
            alerts.append(
                _build_opp_alert(
                    "breakout_approach", bk_candidate, verdict, ev, current, cost_basis
                )
            )
            state["breakout_alert_at"] = _now().isoformat()
            state["breakout_alert_price"] = current

    # 4h. 进行中高影响宏观事件 (FOMC/CPI/PCE/非农等)
    # 每次完整检查都运行, 确保重大数据发布时推送通知到微信
    # 传 state 去重: 结果已出/MONITOR观测不再提醒, 同一事件窗口内只推一次 (2026-08-13 系统修复)
    ongoing_events = _check_ongoing_events(state)
    for evt in ongoing_events:
        alerts.append(evt)

    # 4i. ATR 浮亏轨破位检测 (2026-08-14): 跌破 ATR止损位(≈908) 推送微信提醒
    # 现价在净保本上方时走浮盈轨, 但 atr_stop_loss 恒为浮亏轨(成本-3ATR),
    # 作为保守保护位检测. 冷却 1h 内不重复 (state.atr_sl_break_at).
    # 提前计算 stop_ctx (原在输出时延迟计算, 此处需要 atr_stop_loss 判破位),
    # 输出卡片时复用同一 stop_ctx 避免重复计算.
    stop_ctx = _get_stop_context(current)
    atr_stop_break = _check_atr_stop_break(current, stop_ctx, state, opp_cfg)
    if atr_stop_break:
        alerts.append(atr_stop_break)

    # 4j. 条件单接近提醒 (2026-08-21): 现价逼近活跃买入/卖出条件单触发价 → 微信提醒
    #     复用哨兵 check_order_proximity, 阈值 1.5%, 每单 60min 冷却 (与反弹同款)
    for order_alert in _check_order_proximity(current, state, opp_cfg):
        alerts.append(order_alert)

    # 5. 🆕 下跌原因分析 — 检测是否是机构在抛售
    #    价格下跌超阈值时触发, 结果缓存30分钟
    drop_reason = None
    if price_info["change_pct"] < -0.5 or new_level in ("ALERT", "CRITICAL"):
        drop_reason = _analyze_drop_reason(state)
        if drop_reason.get("institutional_selling"):
            drop_reason["action_hint"] = "机构在跑, 你也应该考虑减仓, 不要死扛"
        elif drop_reason.get("category") == "macro":
            drop_reason["action_hint"] = (
                "非机构抛售, 观察宏观事件(FOMC/PCE)明朗后再决定"
            )

    # 6. 通知 — 仅在有实质内容时输出
    # 已去重: 单独的成本对比不再触发通知 (趋势摘要替代)
    has_real_alerts = any(a["type"] not in ("cost_proximity",) for a in alerts)
    should_output = (
        has_real_alerts
        or level_changed
        or new_level != "NORMAL"
        or (drop_reason and drop_reason.get("category") == "institutional")
        or (rebound is not None)  # 反弹始终通知
    )

    if should_output:
        # 谁在卖 结论去重 (2026-08-31): fingerprint=category+两源数据日期,
        # 与上次已推送结论相同 -> 卡片改一行短格式; 只有新 COT 周报/新 GLD
        # 日更或结论类别变化才复读全文. 指纹仅在真实推送时落 state,
        # 未推送的周期不计入 (否则会误吞下一次推送的全文).
        if drop_reason and drop_reason.get("fingerprint"):
            if state.get("last_flow_fp") == drop_reason["fingerprint"]:
                drop_reason["unchanged"] = True
            state["last_flow_fp"] = drop_reason["fingerprint"]
        # 风控上下文已在 4i 提前计算 (stop_ctx 复用), 卡片常驻 ATR止盈/止损数值行
        if surge and stop_ctx:
            ctx_line = _format_stop_context(stop_ctx)
            if ctx_line:
                # 直接改 surge 原对象 (alerts 列表内也是它); copy 会生成新dict导致告警不带上下文
                surge["message"] = f"{surge['message']}\n    {ctx_line}"
        card = _format_card(
            new_level,
            old_level,
            price_info,
            cost_basis,
            alerts,
            state,
            drop_reason,
            historical,
            stop_ctx=stop_ctx,
        )
        print(card, flush=True)  # 同时输出到 log 文件
        _send_alert(card)  # Hermes → 微信
        # 记录反弹通知时间戳, 供 _check_rebound 冷却判定
        if rebound is not None:
            state["rebound_alert_at"] = _now().isoformat()
            state["rebound_alert_price"] = current

    # 6. 更新状态
    now_iso = _now().isoformat()
    # ── 趋势簿记: 跟踪下跌低点, 用于反弹检测 ──
    prev_price = state.get("last_price")
    state["prev_change_pct"] = (
        (current - prev_price) / prev_price * 100
        if prev_price and prev_price > 0
        else 0.0
    )
    _update_trend_bookkeeping(current, prev_price, state, opp_cfg)

    state["level"] = new_level
    state["last_check_time"] = now_iso
    state["last_price"] = current
    state["consecutive_skips"] = 0
    if level_changed:
        state["level_entered_at"] = now_iso
    if should_output:
        state["last_alert_time"] = now_iso
        state["last_alert_type"] = (
            ",".join(a["type"] for a in alerts) if alerts else "level_change"
        )

    _save_state(state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"❌ 自适应监控异常: {e}", file=sys.stderr)
        sys.exit(1)
