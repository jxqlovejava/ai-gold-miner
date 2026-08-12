#!/usr/bin/env python3
"""gold-cmd — 微信端金价命令封装器 (Hermes ai-gold-miner skill 调用).

用法:
    python3 gold_cmd.py <subcommand> [args]

子命令 — 同步 (<30s, markdown 输出到 stdout, 由 Hermes agent 转述):
    quote      行情快报: 现价 / 持仓浮盈(净口径) / ATR止盈位
    watch      技术面: RSI/MACD/布林/MA200/缠论
    doctrine   军规自查表 (r001-r032)
    orders     条件单状态 (仅 active)
    position   持仓详情
    track      预测追踪 (列表 + 统计)
    analyze    文章分析  (需 --url)

子命令 — 异步 (>30s, 后台运行 + 完成后推微信, 立即返回"已启动"):
    scan       完整 9 步分析        (可选 --quick 加速)
    advisor    投资顾问咨询          (可选 --question "问题")
    scenario   情景推演              (需 --text "情景描述")

环境变量:
    GOLD_MINER_ROOT     repo 根目录   (默认 /home/ubuntu/ai-gold-miner)
    GOLD_WEIXIN_TARGET  微信投递目标   (默认 weixin:o9cq8...@im.wechat)
    GOLD_CMD_ASYNC       非空则强制异步 (测试用)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 启动样板: 定位 repo 根, 注入 PYTHONPATH (照抄 hermes_wrapper_adaptive.py)
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve()
_REPO = Path(os.environ.get("GOLD_MINER_ROOT", "/home/ubuntu/ai-gold-miner"))
if not _REPO.exists():
    _REPO = _HERE.parent.parent  # 本地: <repo>/scripts/gold_cmd.py
os.chdir(_REPO)
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "src"))
os.environ["PYTHONPATH"] = "src:" + os.environ.get("PYTHONPATH", "")
os.environ.setdefault("GOLD_MINER_ROOT", str(_REPO))

from loguru import logger  # noqa: E402

# ---------------------------------------------------------------------------
# 常量与工具
# ---------------------------------------------------------------------------
WEIXIN_TARGET = os.environ.get(
    "GOLD_WEIXIN_TARGET", "weixin:o9cq80613_z9qxqE69G94f-0CzGk@im.wechat"
)
ASYNC = bool(os.environ.get("GOLD_CMD_ASYNC"))
BEIJING_TZ = "Asia/Shanghai"


def _now_cn() -> str:
    """北京时间的 HH:MM."""
    try:
        import zoneinfo
        return datetime.now(zoneinfo.ZoneInfo("Asia/Shanghai")).strftime("%H:%M")
    except Exception:
        return datetime.now().strftime("%H:%M")


def _push_weixin(text: str) -> bool:
    """推送 markdown 到 Hermes 微信 (hermes send, 服务器上可用)."""
    try:
        r = subprocess.run(
            ["hermes", "send", "-t", WEIXIN_TARGET, "-q", text],
            capture_output=True, text=True, timeout=120,
        )
        return r.returncode == 0
    except Exception as e:
        logger.error(f"weixin 推送失败: {e}")
        return False


def _spawn_background(cmd: list[str], log_name: str) -> None:
    """detach 后台运行 cmd, 输出到 logs/<log_name>.log."""
    _REPO.mkdir(parents=True, exist_ok=True)
    log_dir = _REPO / "logs"
    log_dir.mkdir(exist_ok=True)
    logf = open(log_dir / log_name, "a", encoding="utf-8")
    env = os.environ.copy()
    env["GOLD_CMD_ASYNC"] = "1"
    subprocess.Popen(
        cmd,
        stdout=logf, stderr=logf, env=env,
        start_new_session=True, close_fds=True,
    )


def _current_price() -> float | None:
    """获取民生积存金现价 (优先京东真实价)."""
    try:
        from gold_miner.data.jd_accumulation_gold import JdAccumulationGoldFetcher
        p = JdAccumulationGoldFetcher(bank="MS").fetch_price()
        if p:
            return float(p.price)
    except Exception as e:
        logger.debug(f"JD 现价失败: {e}")
    return None


def _atr_stop_signal(current: float | None = None):
    """计算 ATR 移动止盈信号 (r025). 返回 (stop_price, atr, triggered) 或 (None,None,None)."""
    try:
        import yaml
        from gold_miner.strategy.trailing_stop import ATRTrailingStop
        from gold_miner.data.jd_accumulation_gold import JdAccumulationGoldFetcher

        cfg = yaml.safe_load((_REPO / "data/private/portfolio.yaml").read_text())
        pos = cfg["positions"]["gold_jd"]
        avg_cost = float(pos["avg_cost"])
        hard_stop = float(pos["hard_stop"])
        fee = float(pos.get("sell_fee_pct", 0.4)) / 100
        entry_date = pos.get("entry_date")

        df = JdAccumulationGoldFetcher(bank="MS").fetch(days=90)
        sig = ATRTrailingStop(
            atr_period=14, profit_multiplier=2.5, loss_multiplier=3.0,
            cost_basis=avg_cost, hard_stop_price=hard_stop,
            sell_fee_pct=fee, entry_date=entry_date,
        ).calculate(df)
        return sig.stop_price, sig.atr, sig.triggered
    except Exception as e:
        logger.warning(f"ATR 计算失败: {e}")
        return None, None, None


# ---------------------------------------------------------------------------
# 同步子命令
# ---------------------------------------------------------------------------

def cmd_quote() -> str:
    """行情快报: 现价 + 持仓浮盈(净口径) + ATR止盈位."""
    lines: list[str] = [f"## 🪙 积存金快报 · {_now_cn()}"]

    # 行情
    quotes = None
    try:
        from gold_miner.sentinel.quotes import fetch_quotes
        quotes = fetch_quotes(bank="MS")
    except Exception as e:
        logger.debug(f"quote 获取失败: {e}")
    jd_price = _current_price()

    if quotes:
        for q in quotes:
            arrow = "🟢" if q.change_pct >= 0 else "🔴"
            lines.append(f"{arrow} **{q.symbol}** ¥{q.price:.2f} ({q.change_pct:+.2f}%) · {q.source}")
    elif jd_price:
        lines.append(f"💰 积存金 ¥{jd_price:.2f}/克")
    else:
        return "⚠️ 行情获取失败（网络不可用且无缓存）。请稍后再试。"

    # 持仓浮盈 (净口径 r032)
    try:
        from gold_miner.agent.portfolio import PortfolioTracker
        price = jd_price or (quotes[-1].price if quotes and quotes[-1].price else None)
        if price:
            snap = PortfolioTracker().snapshot(float(price))
            pos = snap.positions[0]
            gross = pos.pnl(price)
            net = pos.net_pnl(price)
            net_pct = pos.net_pnl_pct(price)
            be = pos.breakeven_price
            lines.append(f"📊 持仓 {snap.positions[0].grams:.2f}g | 成本 {pos.avg_cost:.2f} | "
                         f"毛 {gross:+,.0f}元({pos.pnl_pct(price):+.1f}%) | 净 {net:+,.0f}元({net_pct:+.1f}%)")
            lines.append(f"🎯 净保本 ¥{be:.2f} | 占比 {snap.gold_allocation_pct:.1f}% | 现金 ¥{snap.cash:,.0f}")
    except Exception as e:
        logger.debug(f"持仓浮盈失败: {e}")

    # ATR 移动止盈
    stop, atr, triggered = _atr_stop_signal()
    if stop:
        status = "🔴 已触发减仓!" if triggered else "未触发"
        lines.append(f"🛡️ ATR止盈位 ¥{stop:.2f} (14日ATR {atr:.2f}, {status})")

    return "\n".join(lines)


def cmd_watch() -> str:
    """技术面: RSI/MACD/布林/MA200/缠论."""
    lines: list[str] = [f"## 📊 技术面 · {_now_cn()}"]

    try:
        from gold_miner.data.jd_accumulation_gold import JdAccumulationGoldFetcher
        from gold_miner.signals.technical import TechnicalAnalyzer

        df = JdAccumulationGoldFetcher(bank="MS").fetch(days=90)
        ta = TechnicalAnalyzer(df)
        rsi = ta.rsi()
        macd = ta.macd()
        bb = ta.bollinger()
        atr = ta.atr()

        lines.append(f"| 指标 | 数值 | 解读 |")
        lines.append(f"|---|---|---|")
        lines.append(f"| RSI(14) | {rsi:.0f} | {'超买' if rsi>70 else '超卖' if rsi<30 else '中性'} |")
        macd_cross = macd.get("crossover", "none")
        macd_txt = f"{macd.get('histogram', 0):+.2f} {'金叉' if macd_cross=='bullish' else '死叉' if macd_cross=='bearish' else ''}"
        lines.append(f"| MACD | {macd_txt} | 趋势方向 |")
        bb_pos = bb.get("position", 0.5)
        lines.append(f"| 布林(20,2) | 上{bb.get('upper',0):.0f}/中{bb.get('middle',0):.0f}/下{bb.get('lower',0):.0f} | 位置{bb_pos*100:.0f}% |")
        lines.append(f"| ATR(14) | {atr.get('atr',0):.2f} | 波动 {atr.get('volatility_regime','')} |")
    except Exception as e:
        lines.append(f"⚠️ 技术指标获取失败: {e}")

    # MA200 趋势闸门 (需 600 天窗口)
    try:
        from gold_miner.signals.ma_trend_gate import MaTrendGateSignal
        from gold_miner.data.spot_gold import SpotGoldFetcher
        gate_df = SpotGoldFetcher().fetch(days=600)
        gate = MaTrendGateSignal(gate_df).analyze()
        if gate["state"] in ("bull", "bear"):
            state = "🟢 开启(多头)" if gate["state"] == "bull" else "🔴 关闭(空头)"
            lines.append(f"| MA200 | {gate['ma200']:.0f} (乖离{gate['vs_ma200_pct']:+.1f}%) | {state} |")
    except Exception as e:
        lines.append(f"| MA200 | — | ⚠️ {e} |")

    # 缠论
    try:
        from gold_miner.signals.chanlun_signal import ChanlunSignalGenerator
        from gold_miner.data.spot_gold import SpotGoldFetcher
        cl_df = SpotGoldFetcher().fetch(days=600)
        cl = ChanlunSignalGenerator(cl_df, symbol="Au99.99", name="黄金").summary_dict()
        if cl.get("buy_points"):
            bp = cl["buy_points"][0]
            lines.append(f"| 缠论 | {bp.get('label','三买')}@{bp.get('price','?')} | 建仓锚点参考 |")
        elif cl.get("struct"):
            lines.append(f"| 缠论 | {cl['struct']} | 结构参考 |")
    except Exception as e:
        lines.append(f"| 缠论 | — | ⚠️ {e} |")

    return "\n".join(lines)


def cmd_doctrine() -> str:
    """军规自查表 (r001-r032, 按 category 分组)."""
    from gold_miner.doctrine.rules import ALL_RULES
    sev_icon = {"block": "🔴", "warn": "🟠", "info": "🔵"}
    cat_label = {
        "position_sizing": "仓位管理", "timing": "时机与节奏",
        "emotion": "情绪纪律", "process": "流程与记录", "trend": "趋势与止盈",
        "entry": "建仓与估值", "operations": "操作纪律", "psychology": "心理防御",
        "signal_discipline": "信号纪律", "info_discipline": "信息纪律",
    }
    groups: dict[str, list] = {}
    for r in ALL_RULES:
        if not r.enabled:
            continue
        groups.setdefault(r.category, []).append(r)

    lines = [f"## 📜 军规自查表 ({len(ALL_RULES)}条)"]
    for cat in sorted(groups):
        label = cat_label.get(cat, cat)
        lines.append(f"\n### {label}")
        lines.append("| 规则 | 级别 | 要求 |")
        lines.append("|---|---|---|")
        for r in groups[cat]:
            lines.append(f"| {r.id} {r.name} | {sev_icon.get(r.severity,'⚪')} {r.severity} | {r.description[:80]} |")
    return "\n".join(lines)


def cmd_orders() -> str:
    """条件单状态 (仅 active)."""
    from gold_miner.sentinel.orders import load_active_orders, check_order_proximity
    from gold_miner.config import settings
    path = Path(settings.private_data_path) / "conditional_orders.jsonl"
    if not path.exists():
        path = _REPO / "data/private/conditional_orders.jsonl"

    orders = load_active_orders(path)
    if not orders:
        return "## 📋 条件单\n当前无活跃条件单。"

    price = _current_price()
    lines = [f"## 📋 条件单 ({len(orders)}条 active)"]
    lines.append("| 单号 | 类型 | 方向 | 触发价 | 克数 | 备注 |")
    lines.append("|---|---|---|---|---|---|")
    for o in orders:
        t = "限价" if o.type == "limit_buy" else "OCO"
        note = (o.note or "")[:30]
        lines.append(f"| {o.id[-8:]} | {t} | {o.direction} | {o.trigger_price:.0f} | {o.quantity_g:.0f}g | {note} |")

    if price and orders:
        near = check_order_proximity(orders, float(price), near_pct=5.0)
        if near:
            lines.append("\n**距触发较近:**")
            for o, dist in near:
                lines.append(f"- {o.id[-8:]} @{o.trigger_price:.0f} 距现价 {dist:.1f}%")
    return "\n".join(lines)


def cmd_position() -> str:
    """持仓详情 (净口径)."""
    try:
        from gold_miner.agent.portfolio import PortfolioTracker
    except Exception as e:
        return f"⚠️ 持仓模块加载失败: {e}"

    price = _current_price()
    if not price:
        return "⚠️ 无法获取现价，无法计算持仓浮盈。"
    tracker = PortfolioTracker()
    snap = tracker.snapshot(float(price))
    pos = snap.positions[0]
    stop, atr, triggered = _atr_stop_signal()

    lines = [f"## 💼 持仓 · 积存金 ({_now_cn()})"]
    lines.append(f"现价 **¥{price:.2f}/克**")
    lines.append("")
    lines.append(f"- 持有: **{pos.grams:.2f}克** (核心 {pos.grams - 0.57:.2f} / 机动 0.57)")
    lines.append(f"- 成本均价: ¥{pos.avg_cost:.2f}")
    lines.append(f"- 毛盈亏: **{pos.pnl(price):+,.0f}元 ({pos.pnl_pct(price):+.1f}%)**")
    lines.append(f"- 净盈亏(扣0.4%): **{pos.net_pnl(price):+,.0f}元 ({pos.net_pnl_pct(price):+.1f}%)**")
    lines.append(f"- 净保本价: ¥{pos.breakeven_price:.2f}")
    lines.append(f"- 黄金占比: {snap.gold_allocation_pct:.1f}% (上限80%) | 现金 ¥{snap.cash:,.0f}")
    if stop:
        lines.append(f"- ATR止盈位: ¥{stop:.2f} ({'🔴已触发' if triggered else '未触发'})")
    lines.append(f"- 硬止损: ¥{pos.hard_stop:.0f} | 预警线: ¥{pos.warn_line:.0f}")
    return "\n".join(lines)


def cmd_sync() -> str:
    """jdgold 登录对账: 条件单 + 持仓 + 交易记录 (幂等, 失败保留旧账本)."""
    from gold_miner.data.jdgold_sync import run_all_sync

    return run_all_sync()


def cmd_sim() -> str:
    """jdgold 模拟盘沙盒 (V9/L1 策略零风险验证, 本机, 需登录)."""
    from gold_miner.backtest.sim_engine import SimSandboxEngine

    engine = SimSandboxEngine()
    return engine.format_report(engine.evaluate(execute=False))


def cmd_track() -> str:
    """预测追踪: 统计 + 最近记录."""
    try:
        from gold_miner.improvement.tracker import PredictionTracker
    except Exception as e:
        return f"⚠️ 预测模块加载失败: {e}"

    tracker = PredictionTracker()
    stats = tracker.stats()
    recent = tracker.recent(n=10)

    lines = [f"## 🎯 预测追踪 · {_now_cn()}"]
    lines.append(f"共 {stats.get('total', 0)} 条 | 已结算 {stats.get('resolved', 0)} | "
                 f"准确率 {stats.get('accuracy', 0):.0%}")
    if not recent:
        lines.append("\n暂无预测记录。")
        return "\n".join(lines)

    lines.append("\n| ID | 时间 | 方向 | 当时价 | 状态 |")
    lines.append("|---|---|---|---|---|")
    dir_icon = {"buy": "🟢多", "sell": "🔴空", "hold": "⚪持"}
    for r in recent:
        d = getattr(r, "direction", "hold") or "hold"
        status = "✅对" if getattr(r, "was_correct", None) else ("❌错" if getattr(r, "was_correct", None) is not None else "⏳未结算")
        ts = getattr(r, "timestamp", "")
        if hasattr(ts, "strftime"):
            ts = ts.strftime("%m-%d")
        lines.append(f"| {r.id[:8]} | {ts} | {dir_icon.get(d,'⚪')} | {getattr(r,'current_price',0):.0f} | {status} |")
    return "\n".join(lines)


def cmd_analyze(url: str) -> str:
    """文章分析 (URL → 正文 → 情感/操纵检测)."""
    try:
        from gold_miner.intelligence.reader import ArticleReader
        from gold_miner.intelligence.analyzer import ArticleAnalyzer
    except Exception as e:
        return f"⚠️ 文章分析模块加载失败: {e}"

    text = ArticleReader.from_url(url, timeout=20)
    if not text:
        return f"⚠️ 文章抓取失败或正文为空: {url[:60]}"

    a = ArticleAnalyzer().analyze(text)
    dir_icon = {"bullish": "🟢看涨", "bearish": "🔴看跌", "neutral": "⚪中性"}
    lines = [f"## 📰 文章分析 · {_now_cn()}"]
    lines.append(f"情感: {dir_icon.get(a.sentiment_direction, '⚪中性')} ({a.sentiment_score:+.2f}) | "
                 f"看涨词 {a.bullish_count} / 看跌词 {a.bearish_count}")
    lines.append(f"操纵检测: {a.manipulation_score}/7 {'⚠️可疑' if a.is_suspicious else '✅未见明显'}")
    if a.summary:
        lines.append(f"\n**摘要**: {a.summary}")
    if a.claims:
        lines.append("\n**关键主张**:")
        for i, c in enumerate(a.claims[:5], 1):
            lines.append(f"{i}. {str(c)[:100]}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 异步子命令
# ---------------------------------------------------------------------------

def _build_scan_markdown(result) -> str:
    """把 AnalysisResult 拼成 9 节 markdown 完整报告."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    b = result.bundle
    lines: list[str] = []
    lines.append(f"# 🥇 金价完整分析 · {now}")
    lines.append("")

    # 行情概览
    price = result.minsheng_accumulation_price or result.current_price
    lines.append(f"> 积存金 **¥{price:.2f}** | 国际 ${result.intl_price:.2f}/oz")
    lines.append("")

    # 1. 决策摘要
    fd = result.final_decision or result.decision or {}
    direction = fd.get("direction", "neutral")
    dir_label = {"buy": "买入", "sell": "卖出", "hold": "持有", "neutral": "观望"}.get(direction, "观望")
    lines.append(f"## 📌 决策: **{dir_label}** | 综合评分 {b.composite_score:+.2f} | 置信度 {b.confidence:.0%}")
    if fd.get("position_pct"):
        lines.append(f"建议仓位: {fd['position_pct']:.0%}")
    if fd.get("stop_loss"):
        lines.append(f"止损: {fd['stop_loss']}")
    if fd.get("take_profit"):
        lines.append(f"止盈: {fd['take_profit']}")
    lines.append("")

    # 2. 8 维信号表
    lines.append("## 维度信号")
    lines.append(b.format_dimension_table())
    lines.append("")

    # 3. Agent 博弈
    lines.append("## Agent 博弈")
    if result.bull_opinion:
        bull = result.bull_opinion
        lines.append(f"🐮 **{bull.agent_name}** (信心 {bull.confidence:.0%})")
        for a in bull.arguments[:3]:
            lines.append(f"  ✓ {a}")
    else:
        lines.append("（本期无多头发言）")
    if result.bear_opinion:
        bear = result.bear_opinion
        lines.append(f"🐻 **{bear.agent_name}** (信心 {bear.confidence:.0%})")
        for a in bear.arguments[:3]:
            lines.append(f"  ✗ {a}")
    else:
        lines.append("（本期无空头发言）")
    lines.append("")

    # 4. 军规
    lines.append("## 军规自查")
    if result.doctrine_result:
        dr = result.doctrine_result
        n_pass = sum(1 for v in getattr(dr, "violations", []) if v.passed)
        n_total = len(getattr(dr, "violations", []))
        lines.append(f"通过 {n_pass}/{n_total}")
        for v in getattr(dr, "violations", [])[:5]:
            if not v.passed:
                lines.append(f"  ⚠️ {v.rule.id} {v.message}")
        if not any(not v.passed for v in getattr(dr, "violations", [])[:5]):
            lines.append("（本期无违规）")
    elif result.doctrine_ctx:
        lines.append(f"军规上下文已加载 (r026 趋势闸门: {result.doctrine_ctx.get('trend_gate_state', '?')})")
    else:
        lines.append("（军规数据缺失）")
    lines.append("")

    # 5. Munger
    lines.append("## Munger 模型")
    if result.munger_models:
        for m in result.munger_models[:3]:
            name = m.get("name_cn", m.get("name_en", "?"))
            desc = m.get("gold_relevance_reason") or m.get("description", "")
            lines.append(f"- **{name}**: {desc[:80]}")
    else:
        lines.append("（本期无触发）")
    lines.append("")

    # 6. 画像匹配
    lines.append("## 画像匹配")
    if result.profile_match:
        pm = result.profile_match
        if isinstance(pm, dict):
            ok = pm.get("ok", pm.get("within_limits", True))
            lines.append(f"{'✅ 兼容' if ok else '⚠️ 超出约束'}")
            if pm.get("notes"):
                lines.append(f"{pm['notes']}")
        else:
            lines.append(str(pm))
    else:
        lines.append("（画像数据缺失）")
    lines.append("")

    # 7. 条件单审查
    lines.append("## 条件单审查")
    if result.conditional_order_review:
        for c in result.conditional_order_review[:6]:
            if isinstance(c, dict):
                lines.append(f"- {c.get('id', '')} {c.get('action', '')} {c.get('reason', '')[:60]}")
    else:
        lines.append("（本期无触发/无活跃条件单）")
    lines.append("")

    # 8. 情景/后续事件
    lines.append("## 后续关注")
    if result.scenario_plan:
        sp = result.scenario_plan
        events = (sp.get("upcoming_events") or [])[:5]
        if events:
            for ev in events:
                if isinstance(ev, dict):
                    lines.append(f"- {ev.get('name', ev.get('event', ''))} {ev.get('time', ev.get('date', ''))}")
        else:
            lines.append("（无近期待关注事件）")
        monitors = sp.get("monitors_triggered") or []
        if monitors:
            lines.append(f"Monitor 触发: {len(monitors)} 条")
    else:
        lines.append("（情景数据缺失）")
    lines.append("")

    # 9. 提示
    lines.append("## 📚 经验提醒")
    if result.experience_reminders:
        for r in result.experience_reminders[:3]:
            lines.append(f"- {r}")
    else:
        lines.append("（本期无触发）")

    return "\n".join(lines)


def _run_scan_background(quick: bool) -> str:
    """异步后台跑 scan, 完成后推微信."""
    async_log = _REPO / "logs" / "gold_scan_async.log"
    cmd = [sys.executable, str(_HERE), "scan", "--async-worker"]
    if quick:
        cmd.append("--quick")
    _spawn_background(cmd, "gold_scan_async.log")
    logger.info(f"scan 后台任务已启动 → {async_log}")
    eta = "40-70 秒" if quick else "1-2 分钟"
    return f"⏳ 完整分析已启动 ({'快速' if quick else '完整'}模式)，约 {eta} 后推送微信。"


def _scan_worker(quick: bool) -> int:
    """后台 worker: 真正跑 AnalysisPipeline."""
    try:
        from gold_miner.pipeline.analysis import AnalysisPipeline, AnalysisContext

        ctx = AnalysisContext(
            days=30,
            with_news=not quick,
            with_sentiment=not quick,
            deep=False,
            skip_alerts=True,
            skip_notification=True,
            skip_tracking=True,
        )
        if quick:
            from gold_miner.config import settings
            settings.demo_mode = True

        result = AnalysisPipeline().run(ctx)

        md = _build_scan_markdown(result)

        # 归档
        today = datetime.now().strftime("%Y%m%d")
        archive = _REPO / "data/private" / f"analysis_wechat_{today}.md"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text(md, encoding="utf-8")

        # 生成本地 HTML 报告 (Req3C 2026-08-11) — 渲染失败不阻塞推送
        try:
            import sys as _sys
            sys.path.insert(0, str(_REPO / "scripts"))
            from render_report_html import render
            html_out = _REPO / "data/output" / f"金价分析_{datetime.now().strftime('%Y-%m-%d')}.html"
            render(md, html_out)
            logger.info(f"HTML 报告已生成: file://{html_out.resolve()}")
        except Exception as _e:
            logger.warning(f"HTML 报告渲染失败, 不影响推送: {_e}")

        # 推送 (微信单条限长, 按节拆多条)
        ok = _push_weixin(md[:1800])
        if len(md) > 1800:
            _push_weixin(md[1800:3600])
        logger.info(f"scan 完成, 归档 {archive}, 推送 {'成功' if ok else '失败'}")
        return 0
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        logger.error(f"scan worker 失败: {e}\n{err}")
        _push_weixin(f"❌ 完整分析失败: {e}")
        return 1


def _run_advisor_background(question: str) -> str:
    """异步后台跑 advisor."""
    cmd = [sys.executable, str(_HERE), "advisor", "--async-worker"]
    if question:
        cmd += ["--question", question]
    _spawn_background(cmd, "gold_advisor_async.log")
    logger.info("advisor 后台任务已启动")
    return "⏳ 投资顾问正在思考，约 30-60 秒后推送微信。"


def _advisor_worker(question: str) -> int:
    try:
        from gold_miner.advisor.orchestrator import Advisor
        from gold_miner.agent.portfolio import PortfolioTracker

        # 从持仓读取当前仓位/均价
        pos_pct = 0.0
        avg_cost = 0.0
        try:
            price = _current_price() or 0.0
            tracker = PortfolioTracker()
            snap = tracker.snapshot(price) if price else None
            if snap:
                pos_pct = snap.gold_allocation_pct / 100
                avg_cost = snap.positions[0].avg_cost
        except Exception:
            pass

        report = Advisor().consult(
            question=question or "当前金价走势下，我的积存金持仓该如何操作？",
            current_position_pct=pos_pct,
            avg_cost=avg_cost,
        )
        md = report.to_markdown()
        ok = _push_weixin(md[:1800])
        if len(md) > 1800:
            _push_weixin(md[1800:3600])
        logger.info(f"advisor 完成, 推送 {'成功' if ok else '失败'}")
        return 0
    except Exception as e:
        import traceback
        logger.error(f"advisor worker 失败: {e}\n{traceback.format_exc()}")
        _push_weixin(f"❌ 投资顾问失败: {e}")
        return 1


def _run_scenario_background(text: str) -> str:
    """异步后台跑 scenario."""
    cmd = [sys.executable, str(_HERE), "scenario", "--async-worker", "--text", text]
    _spawn_background(cmd, "gold_scenario_async.log")
    logger.info("scenario 后台任务已启动")
    return "⏳ 情景推演已启动（走 LLM），约 30-90 秒后推送微信。"


def _scenario_worker(text: str) -> int:
    try:
        from gold_miner.scenarios.analyzer import ScenarioAnalyzer
        from gold_miner.data.spot_gold import SpotGoldFetcher

        context = {}
        try:
            fetcher = SpotGoldFetcher()
            df = fetcher.fetch(days=5)
            if not df.empty:
                context["spot_gold"] = float(df["close"].iloc[-1])
        except Exception:
            pass

        report = ScenarioAnalyzer().analyze(text, time_horizon_months=12, context=context)
        pi = report.price_impact
        lines = [f"## 🌪️ 情景推演 · {_now_cn()}"]
        lines.append(f"**情景**: {report.scenario_description[:80]}")
        lines.append(f"\n**价格影响**: {pi.direction} | 基准 {pi.base_case_change_pct:+.1f}% | "
                     f"乐观 {pi.bullish_case_change_pct:+.1f}% | 悲观 {pi.bearish_case_change_pct:+.1f}% | "
                     f"置信 {pi.confidence:.0%}")
        if pi.reasoning:
            lines.append(f"\n**核心推理**: {pi.reasoning[:200]}")
        if report.transmission_channels:
            lines.append("\n**传导链**:")
            for c in report.transmission_channels[:4]:
                lines.append(f"- {c.channel} ({c.direction}, {c.timeframe}): {c.description[:60]}")
        if report.historical_analogs:
            lines.append("\n**历史类比**:")
            for h in report.historical_analogs[:2]:
                lines.append(f"- {h.event_name} ({h.period}): 金价 {h.gold_price_change_pct:+.1f}%")
        strat = report.strategy
        lines.append(f"\n**策略**: {strat.overall_position} | 积存金 {strat.accumulation_gold_action}")
        if strat.suggested_entry_zones:
            lines.append(f"入场区: {strat.suggested_entry_zones}")
        md = "\n".join(lines)
        ok = _push_weixin(md[:1800])
        logger.info(f"scenario 完成, 推送 {'成功' if ok else '失败'}")
        return 0
    except Exception as e:
        import traceback
        logger.error(f"scenario worker 失败: {e}\n{traceback.format_exc()}")
        _push_weixin(f"❌ 情景推演失败: {e}")
        return 1


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="微信端金价命令封装器")
    parser.add_argument("subcommand",
                        choices=["quote", "watch", "doctrine", "orders", "position",
                                 "sync", "sim", "track", "analyze", "scan", "advisor", "scenario"])
    parser.add_argument("--url", default=None, help="文章 URL (analyze)")
    parser.add_argument("--text", default=None, help="情景描述 (scenario)")
    parser.add_argument("--question", default=None, help="咨询问题 (advisor)")
    parser.add_argument("--quick", action="store_true", help="快速模式 (scan, 关新闻/情绪)")
    parser.add_argument("--async-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    # 后台 worker 入口 (异步子进程)
    if args.async_worker:
        if args.subcommand == "scan":
            return _scan_worker(args.quick)
        if args.subcommand == "advisor":
            return _advisor_worker(args.question or "")
        if args.subcommand == "scenario":
            return _scenario_worker(args.text or "黄金价格大幅下跌")

    # 同步命令
    if args.subcommand == "quote":
        print(cmd_quote())
    elif args.subcommand == "watch":
        print(cmd_watch())
    elif args.subcommand == "doctrine":
        print(cmd_doctrine())
    elif args.subcommand == "orders":
        print(cmd_orders())
    elif args.subcommand == "position":
        print(cmd_position())
    elif args.subcommand == "sync":
        print(cmd_sync())
    elif args.subcommand == "sim":
        print(cmd_sim())
    elif args.subcommand == "track":
        print(cmd_track())
    elif args.subcommand == "analyze":
        if not args.url:
            print("⚠️ analyze 需要 --url 参数")
            return 1
        print(cmd_analyze(args.url))

    # 异步命令: 前台触发后台任务
    elif args.subcommand == "scan":
        print(_run_scan_background(args.quick))
    elif args.subcommand == "advisor":
        print(_run_advisor_background(args.question or ""))
    elif args.subcommand == "scenario":
        print(_run_scenario_background(args.text or "黄金价格大幅下跌"))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
