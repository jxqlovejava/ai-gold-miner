"""黄金哨兵引擎 — 持仓监控 + 价格告警 + 条件单检查."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import yaml

from .models import (
    AlertLevel,
    ConditionalOrder,
    PortfolioSnapshot,
    SentinelAlert,
    SentinelConfig,
    SentinelResult,
    currency_cn,
    symbol_cn,
)
from .orders import check_order_proximity, load_active_orders
from .quotes import fetch_quotes

BEIJING = timezone(timedelta(hours=8))

# 国内主要节假日 (非交易日) — 覆盖 2026 下半年 (日期为北京时间)
# 周六/周日永远非交易日; 法定节假日补班/放假在此维护
_CN_HOLIDAYS: set[str] = {
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",  # 国庆
}

# 例行观察 → 通俗人话解释 (按 monitor 名字关键词匹配, 让用户一眼看懂在监控什么)
# 结构: 多个 (关键词, 通俗解释)。命中多个关键词时拼接。
_MONITOR_PLAIN_HINTS: list[tuple[str, str]] = [
    ("60/200日线趋势过滤", "金价趋势风向标：短期均线在长期上方=多头上涨趋势，跌破=转弱信号"),
    ("COT月度聪明钱闸门", "机构动向监控：连续2周机构在加仓=放心持有；机构在减仓=别急着加仓"),
    ("ATR移动止盈位", "自动止盈保护：金价跌到止盈线就减半仓，把浮盈锁住，不用盯盘"),
    ("结构牛情景确认", "大行情确认开关：央行持续买金+降息预期时触发，是机会池出击的信号"),
    ("9月加息概率", "美联储加息监控：加息概率高→金价承压，破关键位要重估长线"),
    ("非农分情形", "非农数据应对：数据好→回调接货，数据差→撤单观望，都有预案"),
    ("美伊冲突升级", "地缘冲突监控：中东局势升级会推高金价（避险）"),
    ("停火达成", "停火监控：局势缓和金价可能急跌，提前挂好低吸单"),
    ("美伊协议正式签署", "协议签署监控：利好落地可能回调，等深水区加仓"),
    ("谈判破裂", "谈判破裂监控：冲突再起金价可能冲高，关注止盈"),
    ("长期-", "长期战略观察（V9计划内置监控）"),
]


def monitor_plain_hint(name: str) -> str:
    """把 monitor 名字转成通俗人话解释.

    按关键词匹配 _MONITOR_PLAIN_HINTS, 命中的首个解释优先。
    """
    for kw, hint in _MONITOR_PLAIN_HINTS:
        if kw in name:
            return hint
    return ""


def is_cn_trading_day(day: datetime) -> bool:
    """判断北京时间 day 是否为国内黄金交易日.

    积存金/上金所交易日 = 工作日且非法定节假日。
    """
    bj = day.astimezone(BEIJING)
    if bj.weekday() >= 5:  # 周六(5)/周日(6)
        return False
    return bj.strftime("%Y-%m-%d") not in _CN_HOLIDAYS


def next_cn_trading_day(day: datetime) -> datetime:
    """返回 day 之后的第一个国内交易日 (北京时间)."""
    bj = day.astimezone(BEIJING)
    candidate = bj + timedelta(days=1)
    while not is_cn_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


class SentinelEngine:
    """黄金哨兵引擎."""

    def __init__(self, config: SentinelConfig):
        self.cfg = config

    def run(self) -> SentinelResult:
        """执行一次哨兵检查."""
        alerts: list[SentinelAlert] = []

        # 1. 获取报价
        quotes = fetch_quotes()
        if not quotes:
            return SentinelResult(alerts=alerts)

        xauusd = next((q for q in quotes if q.symbol == "XAUUSD"), None)
        jd_gold = next((q for q in quotes if "积存金" in q.symbol), None)
        current_price = jd_gold.price if jd_gold else (xauusd.price if xauusd else 0)

        # 2. 价格异动告警
        for q in quotes:
            if abs(q.change_pct) >= self.cfg.day_drop_pct:
                direction = "大跌" if q.change_pct < 0 else "大涨"
                level = AlertLevel.P1 if abs(q.change_pct) < self.cfg.day_rise_pct + 2 else AlertLevel.P0
                unit = currency_cn(q.currency)
                # 国际金价异动 → 提示国内开盘联动（国内银行未开盘时积存金是昨收）
                suggestion = ""
                if q.symbol == "XAUUSD":
                    # 只在下个国内交易日临近时提示联动; 若明后天是周末/节假日则提示最近交易日
                    nxt = next_cn_trading_day(datetime.now(UTC))
                    nxt_bj = nxt.strftime("%m-%d")
                    nxt_weekday = "周" + "一二三四五六日"[nxt.weekday()]
                    direction_cn = "补涨" if q.change_pct > 0 else "补跌"
                    suggestion = (
                        f"国内黄金下个交易日 {nxt_bj}({nxt_weekday}) 开盘大概率{direction_cn}，留意开盘价"
                    )
                alerts.append(SentinelAlert(
                    level=level,
                    title=f"{symbol_cn(q.symbol)}日内{direction} {abs(q.change_pct):.2f}%",
                    detail=f"当前 {q.price:.2f} {unit}，前收 {q.prev_close:.2f} {unit}",
                    suggestion=suggestion,
                ))

        # 3. 持仓分析
        portfolio = self._load_portfolio(current_price)
        if portfolio:
            alerts.extend(self._check_portfolio(portfolio))

        # 4. 条件单检查
        if current_price > 0:
            orders = load_active_orders(self.cfg.orders_path)
            alerts.extend(self._check_orders(orders, current_price))

        # 5. 日历事件提醒
        alerts.extend(self._check_calendar())

        return SentinelResult(
            alerts=alerts,
            quotes=quotes,
            portfolio=portfolio,
        )

    def _load_portfolio(self, current_price: float) -> PortfolioSnapshot | None:
        """加载持仓并计算快照."""
        pf_path = self.cfg.portfolio_path
        if not pf_path.exists():
            return None
        try:
            data = yaml.safe_load(pf_path.read_text(encoding="utf-8"))
        except Exception:
            return None

        positions = data.get("positions", {})
        gold = positions.get("gold_jd", {})
        data.get("limits", {})

        grams = gold.get("grams", 0)
        avg_cost = gold.get("avg_cost", 0)
        hard_stop = gold.get("hard_stop", 0)
        secondary_stop = gold.get("secondary_stop", 0)

        if grams <= 0 or avg_cost <= 0:
            return None

        market_value = grams * current_price
        cost_value = grams * avg_cost
        unrealized_pnl = market_value - cost_value
        unrealized_pnl_pct = (unrealized_pnl / cost_value * 100) if cost_value > 0 else 0

        return PortfolioSnapshot(
            instrument=gold.get("instrument", "积存金"),
            platform=gold.get("platform", "京东金融"),
            grams=grams,
            avg_cost=avg_cost,
            current_price=current_price,
            market_value=market_value,
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_pct=unrealized_pnl_pct,
            hard_stop=hard_stop,
            secondary_stop=secondary_stop,
        )

    def _check_portfolio(self, p: PortfolioSnapshot) -> list[SentinelAlert]:
        """持仓风险检查."""
        alerts: list[SentinelAlert] = []

        # 硬止损触发
        if p.current_price <= p.hard_stop:
            alerts.append(SentinelAlert(
                level=AlertLevel.P0,
                title="🔴 硬止损触发!",
                detail=f"当前价{p.current_price:.0f}元 ≤ 硬止损{p.hard_stop}元",
                suggestion="立即按止损价清仓, 认赔离场",
            ))
            return alerts

        # 接近硬止损
        dist_to_hard = (p.current_price - p.hard_stop) / p.hard_stop * 100
        if dist_to_hard <= 5:
            alerts.append(SentinelAlert(
                level=AlertLevel.P1,
                title=f"接近硬止损 ({dist_to_hard:.1f}% 距离)",
                detail=f"当前{p.current_price:.0f}元, 硬止损{p.hard_stop}元",
            ))

        # 二级止损
        if p.secondary_stop > 0 and p.current_price <= p.secondary_stop:
            alerts.append(SentinelAlert(
                level=AlertLevel.P0,
                title="🔴 二级止损触发!",
                detail=f"当前价{p.current_price:.0f}元 ≤ 二级止损{p.secondary_stop}元",
                suggestion="检查条件单 co_20260716_003 是否已触发卖出9g",
            ))
        elif p.secondary_stop > 0:
            dist_to_sec = (p.current_price - p.secondary_stop) / p.secondary_stop * 100
            if dist_to_sec <= self.cfg.stop_near_pct:
                alerts.append(SentinelAlert(
                    level=AlertLevel.P1,
                    title=f"接近二级止损 ({dist_to_sec:.1f}% 距离)",
                    detail=f"当前{p.current_price:.0f}元, 止损{p.secondary_stop}元",
                ))

        # 大幅浮亏
        if p.unrealized_pnl_pct <= -10:
            alerts.append(SentinelAlert(
                level=AlertLevel.P1,
                title=f"浮亏达 {p.unrealized_pnl_pct:+.1f}%",
                detail=f"成本{p.avg_cost:.0f}元, 当前{p.current_price:.0f}元, 浮亏{p.unrealized_pnl:+.0f}元",
                suggestion="r022: 浮亏超10%后决策质量骤降, 提前动作不要等",
            ))

        # 浮盈上移止损提醒
        if p.unrealized_pnl_pct >= 20:
            alerts.append(SentinelAlert(
                level=AlertLevel.P2,
                title=f"浮盈 {p.unrealized_pnl_pct:+.1f}%, 检查止损上移",
                detail="r010: 浮盈>20%时止损必须上移至成本价以上",
            ))

        return alerts

    def _check_orders(
        self,
        orders: list[ConditionalOrder],
        current_price: float,
    ) -> list[SentinelAlert]:
        """条件单接近检查."""
        alerts: list[SentinelAlert] = []
        nearby = check_order_proximity(orders, current_price, self.cfg.order_near_pct)
        order_type_cn = {"oco": "止盈止损单", "limit_buy": "限价买入单"}
        for o, dist in nearby[:3]:  # 最多3条
            direction_sym = "↓" if o.direction == "卖出" else "↑"
            type_cn = order_type_cn.get(o.type, o.type)
            alerts.append(SentinelAlert(
                level=AlertLevel.P2,
                title=f"条件单接近: {type_cn} {o.direction} "
                      f"@{o.trigger_price:.0f}元 ({direction_sym}{dist:.1f}%)",
                detail=f"{o.note[:50] if o.note else o.id}",
            ))
        return alerts

    def _should_push_monitor(self, name: str, freq: str, now: datetime) -> bool:
        """按 check_frequency 决定 monitor 是否本次该推送.

        用 state_path 记录每个 monitor 上次推送时间, 避免每日重复推同一个例行观察.
        - weekly → 7 天一次;  daily → 24h 一次;  on_analysis → 每次都推
        - 无状态文件/首次 → 推 (让用户先看到哨兵清单)
        """
        intervals = {"weekly": timedelta(days=7), "daily": timedelta(days=1)}
        interval = intervals.get(freq)
        # on_analysis 或无频率标注 → 每次都推
        if interval is None:
            return True

        state_path = self.cfg.state_path
        pushed: dict[str, str] = {}
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                pushed = state.get("monitor_last_pushed", {})
            except (ValueError, OSError, TypeError):
                pushed = {}

        last = pushed.get(name)
        if not last:
            # 首次: 推, 并记录
            self._record_monitor_push(name, now, state_path)
            return True
        try:
            last_dt = datetime.fromisoformat(last)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=UTC)
            if now - last_dt >= interval:
                self._record_monitor_push(name, now, state_path)
                return True
            return False
        except (ValueError, TypeError):
            return True

    @staticmethod
    def _record_monitor_push(name: str, now: datetime, state_path: Path) -> None:
        """记录 monitor 推送时间到状态文件."""
        try:
            state: dict = {}
            if state_path.exists():
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                except (ValueError, OSError, TypeError):
                    state = {}
            pushed = state.get("monitor_last_pushed", {})
            pushed[name] = now.isoformat()
            state["monitor_last_pushed"] = pushed
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _check_calendar(self) -> list[SentinelAlert]:
        """检查未来 N 小时内的日历事件."""
        alerts: list[SentinelAlert] = []
        cal_path = self.cfg.calendar_path
        if not cal_path.exists():
            return alerts

        now = datetime.now(UTC)
        window = now + timedelta(hours=self.cfg.event_remind_hours)
        happening_now = timedelta(minutes=30)  # ±30分钟视为"正在发生"

        try:
            for line in cal_path.read_text(encoding="utf-8").strip().split("\n"):
                if not line.strip():
                    continue
                d = json.loads(line)
                sat_str = d.get("scheduled_at", "")
                if not sat_str:
                    continue
                try:
                    sat = datetime.fromisoformat(sat_str)
                except ValueError:
                    continue

                if sat.tzinfo is None:
                    continue

                name = d.get("name", "")
                impact = d.get("impact", "medium")
                status = d.get("status", "")
                expires_at = d.get("expires_at", "")

                # 已有结果的事件不提醒
                if d.get("actual"):
                    continue

                # monitor 事件: 检查过期/状态/推送频率 (兼容旧格式: event_type 缺失但 name 以"观测:"开头)
                is_monitor = d.get("event_type") == "monitor" or name.startswith("观测:")
                if is_monitor:
                    # 非 active 状态不提醒 (已触发/已关闭)
                    if status and status != "active":
                        continue
                    # 已过期不提醒
                    if expires_at:
                        try:
                            exp = datetime.fromisoformat(expires_at)
                            if exp.tzinfo is None:
                                exp = exp.replace(tzinfo=UTC)
                            if now > exp:
                                continue
                        except (ValueError, TypeError):
                            pass
                    # 按 check_frequency 控制例行观察推送频率
                    freq = d.get("check_frequency", "")
                    if not self._should_push_monitor(name, freq, now):
                        continue

                bj_time = sat.astimezone(BEIJING).strftime("%m-%d %H:%M")

                # ── 正在发生 (±30min) → P0 即时通知 ──
                if abs((now - sat).total_seconds()) <= happening_now.total_seconds():
                    alerts.append(SentinelAlert(
                        level=AlertLevel.P0,
                        title=f"🔔 正在进行: {name}",
                        detail=f"时间: {bj_time} (北京), 影响: {impact}",
                        suggestion="关注市场即时反应, 谨慎操作",
                    ))
                # ── 即将到来 (24h内) → P2 提醒 ──
                elif now <= sat <= window and impact == "high":
                    if name.startswith("观测:"):
                        # 例行观察: 人话解释 + 触发条件, 让用户一眼看懂
                        title = "🔍 " + name[len("观测:"):].strip()
                        cond = d.get("trigger_condition", "")
                        freq = d.get("check_frequency", "")
                        hint = monitor_plain_hint(name)
                        parts = []
                        if hint:
                            parts.append(hint)
                        elif cond:
                            # 无人话映射时, 条件取前3个分句避免太长
                            cond_parts = [c.strip() for c in cond.split("/") if c.strip()][:3]
                            parts.append("；".join(cond_parts))
                        if freq:
                            freq_cn = {"on_analysis": "每次分析", "daily": "每日", "weekly": "每周"}.get(freq, freq)
                            parts.append(f"检查: {freq_cn}")
                        detail = " · ".join(parts) if parts else ""
                    else:
                        title = f"📅 即将: {name}"
                        detail = f"时间: {bj_time}（北京）"
                    alerts.append(SentinelAlert(
                        level=AlertLevel.P2,
                        title=title,
                        detail=detail,
                    ))
        except Exception:
            return []
        return alerts
