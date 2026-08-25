"""多维度详细输出 — 技术面/基本面/消息面/情绪面/资金流/经济日历."""
from __future__ import annotations

import re

import pandas as pd

from gold_miner.signals.base import Signal, SignalBundle

# ── 通用表格渲染工具 (2026-08-25 各维度板块排版统一表格化) ──

_DIR_ZH = {"bullish": "🟢 看多", "bearish": "🔴 看空", "neutral": "⚪ 中性"}
_STRENGTH_ZH = {"weak": "弱", "moderate": "中", "strong": "强"}
_IMPACT_ZH = {"high": "高", "medium": "中", "low": "低"}


def _cell(text: object, limit: int) -> str:
    """表格单元格清洗: 压缩空白/去换行与竖线 + 截断."""
    cleaned = re.sub(r"\s+", " ", str(text or "")).replace("|", "/").strip()
    return cleaned[:limit] + "…" if len(cleaned) > limit else cleaned


def _sig_name(sig: Signal) -> str:
    """信号名清洗: 去尾部强度标记 [weak/moderate/strong] (强度由列承载)."""
    return re.sub(r"\s*\[(weak|moderate|strong)\]$", "", sig.name)


def _signal_table(sigs: list[Signal], name_limit: int = 30, desc_limit: int = 45) -> None:
    """通用信号表格: | 信号 | 方向 | 评分 | 说明 |"""
    print("  | 信号 | 方向 | 评分 | 说明 |")
    print("  |---|---|---|---|")
    for sig in sigs:
        d = _DIR_ZH.get(sig.direction.value, sig.direction.value)
        print(
            f"  | {_cell(_sig_name(sig), name_limit)} | {d} "
            f"| {sig.score:+.2f} | {_cell(sig.description, desc_limit)} |"
        )


def _print_avg_header(sigs: list[Signal]) -> None:
    """信号小节头: 数量 + 均分."""
    avg = sum(s.score for s in sigs) / len(sigs)
    print(f"  信号 ({len(sigs)}个, 均分 {avg:+.2f}):")

# 聪明钱资金流信号 source 分类
_SMART_MONEY_SOURCES = frozenset({
    "cot_report",          # CFTC COT 持仓报告
    "gld_holdings_tonnes", # GLD 官方持仓
    "gold_etf_price_proxy",
    "gold_etf_volume_proxy",
    "intl_gold_etf_volume_proxy",
    "domestic_intl_divergence",
    "cross_etf",
    "bank_targets",        # 投行目标价共识
    "comex_large_traders", # COMEX 大户集中度
    "13f_institutional",   # 13F 机构持仓
    "smart_money_composite",  # 聪明钱综合信号
    "jd_fund_bomb",        # jdgold 资金炸弹/大单资金流 (分钟级, P3 2026-08-13)
    "jd_blogger_rank",     # jdgold 大V加仓榜 (散户情绪, P3 2026-08-13)
})


def _calc_rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1:
        return 50.0
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(period).mean().iloc[-1]
    avg_loss = loss.rolling(period).mean().iloc[-1]
    if avg_loss == 0 or pd.isna(avg_loss):
        return 100.0 if avg_gain > 0 else 50.0
    return float(100.0 - (100.0 / (1.0 + avg_gain / avg_loss)))


def print_technical(
    gold_df: pd.DataFrame, bundle: SignalBundle, trend_gate: dict | None = None
) -> None:
    if gold_df.empty:
        return
    close = gold_df["close"]
    latest = close.iloc[-1]
    rsi_val = _calc_rsi(close)
    rsi_label = "超卖" if rsi_val < 20 else "超买" if rsi_val > 80 else "中性"
    ema12 = close.ewm(span=12).mean().iloc[-1]
    ema26 = close.ewm(span=26).mean().iloc[-1]
    macd = ema12 - ema26
    macd_label = "金叉" if macd > 0 else "死叉"
    sma20 = close.rolling(20).mean().iloc[-1]
    std20 = close.rolling(20).std().iloc[-1]
    upper = sma20 + 2 * std20
    lower = sma20 - 2 * std20
    bb_pos = (latest - lower) / (upper - lower) * 100 if upper != lower else 50
    bb_label = "下轨附近" if bb_pos < 20 else "上轨附近" if bb_pos > 80 else "中轨"
    high_20 = gold_df["high"].tail(20).max()
    low_20 = gold_df["low"].tail(20).min()

    dim_name = "\U0001f4ca 技术面"
    print(f"\n{'='*60}")
    print(f"  {dim_name}")
    print(f"{'='*60}")
    # 核心指标表格 (2026-08-25 排版统一)
    print("  | 指标 | 数值 | 状态 |")
    print("  |---|---|---|")
    print(f"  | RSI(14) | {rsi_val:.0f} | {rsi_label} |")
    print(f"  | MACD | {macd:+.2f} | {macd_label} |")
    print(f"  | 布林带位置 | {bb_pos:.0f}% | {bb_label} (上{upper:.0f}/中{sma20:.0f}/下{lower:.0f}) |")
    print(
        f"  | 20日区间 | {low_20:.0f} ~ {high_20:.0f} "
        f"| 距支撑{((latest-low_20)/low_20*100):+.1f}% 距阻力{((high_20-latest)/high_20*100):+.1f}% |"
    )

    # 长期趋势闸门 (MA50/100/200) - 军规 r026 可视化
    if trend_gate and trend_gate.get("state") != "insufficient_data":
        gate_icon = {
            "bull": "\U0001f7e2 开启(多头排列)",
            "bear": "\U0001f534 关闭",
            "mixed": "\U0001f7e1 中性(排列未确认)",
        }.get(trend_gate["state"], trend_gate["state"])
        _pam = trend_gate.get("price_above_ma200")
        pos_label = "站上" if _pam else "跌破" if _pam is not None else "未知"
        print(
            f"  | 长期趋势闸门 | {gate_icon} "
            f"| 现价{pos_label}MA200, 乖离 {trend_gate.get('vs_ma200_pct', 0):+.1f}% "
            f"(MA50 {trend_gate.get('ma50')}/MA100 {trend_gate.get('ma100')}/MA200 {trend_gate.get('ma200')}) |"
        )

    sigs = bundle.by_dimension("technical")
    if sigs:
        _print_avg_header(sigs)
        _signal_table(sigs)
    else:
        print("  信号: 无 (技术指标未触发极端值)")


def print_fundamental(
    dxy_df: pd.DataFrame, rate_df: pd.DataFrame, breakeven_df: pd.DataFrame,
    gold_df: pd.DataFrame, silver_df: pd.DataFrame, bundle: SignalBundle,
) -> None:
    dim_name = "\U0001f3db️ 基本面"
    print(f"\n{'='*60}")
    print(f"  {dim_name}")
    print(f"{'='*60}")

    # 核心指标表格 (2026-08-25 排版统一)
    print("  | 指标 | 现值 | 对比20日均 |")
    print("  |---|---|---|")
    if not dxy_df.empty:
        dxy_now = dxy_df["value"].iloc[-1]
        dxy_20 = dxy_df["value"].tail(20).mean()
        dxy_dir = "走弱 ↓" if dxy_now < dxy_20 else "走强 ↑"
        print(f"  | 美元指数 DXY | {dxy_now:.2f} | {dxy_dir} (20日均 {dxy_20:.2f}) |")
    if not rate_df.empty:
        rate_now = rate_df["value"].iloc[-1]
        rate_20 = rate_df["value"].tail(20).mean()
        rate_dir = "↓" if rate_now < rate_20 else "↑"
        print(f"  | 10Y 实际利率 | {rate_now:.2f}% | {rate_dir} (20日均 {rate_20:.2f}%) |")
    if not breakeven_df.empty:
        be_now = breakeven_df["value"].iloc[-1]
        be_20 = breakeven_df["value"].tail(20).mean()
        be_dir = "↓" if be_now < be_20 else "↑"
        print(f"  | 盈亏平衡通胀率 | {be_now:.2f}% | {be_dir} (20日均 {be_20:.2f}%) |")
    if not gold_df.empty and not silver_df.empty:
        gold_s = gold_df["close"].iloc[-1]
        silver_s = silver_df["value"].iloc[-1]
        ratio = gold_s / silver_s if silver_s > 0 else 0
        ratio_label = "极高位(避险极端)" if ratio > 85 else "低位(风险偏好高)" if ratio < 60 else "正常"
        print(f"  | 金银比 | {ratio:.1f} | {ratio_label} |")

    sigs = bundle.by_dimension("fundamental")
    if sigs:
        _print_avg_header(sigs)
        _signal_table(sigs)
    else:
        print("  信号: 无")


def print_news(news_items: list, bundle: SignalBundle) -> None:
    dim_name = "\U0001f4f0 消息面"
    print(f"\n{'='*60}")
    print(f"  {dim_name}")
    print(f"{'='*60}")

    sigs = bundle.by_dimension("news")
    if sigs:
        _print_avg_header(sigs)
        _signal_table(sigs, name_limit=34, desc_limit=45)
    else:
        print("  信号: 无 (新闻情感未达阈值)")

    if news_items:
        print()  # 信号表与最近新闻表之间留空行 (2026-08-25 排版)
        # 来源标签动态化 (2026-08-25): 旧版硬编码 "NewsAPI" 但 fallback 路径实际是
        # anysearch/搜索引擎, 误导信源判断
        from collections import Counter as _Counter
        src_counts = _Counter((i.source or "unknown").split(".")[0].lower() for i in news_items)
        src_label = "+".join(f"{s}×{c}" for s, c in src_counts.most_common(3))
        print(f"  最近新闻 ({src_label}, {len(news_items)}条):")
        print("  | 标题 | 来源 | 层级 |")
        print("  |---|---|---|")
        for item in news_items[:6]:
            tier = item.metadata.get("source_tier", "")
            print(
                f"  | {_cell(item.title, 50)} | {_cell(item.source, 14)} "
                f"| {tier or '-'} |"
            )


def print_sentiment(au_df: pd.DataFrame | None, bundle: SignalBundle) -> None:
    dim_name = "\U0001f4ad 情绪面"
    print(f"\n{'='*60}")
    print(f"  {dim_name}")
    print(f"{'='*60}")

    if au_df is not None and not au_df.empty:
        latest_au = au_df.iloc[-1]
        oi = latest_au.get("open_interest", 0)
        oi_5d = latest_au.get("oi_change_5d", 0)
        vol = latest_au.get("volume", 0)
        vol_ratio = latest_au.get("volume_ratio", 1.0)
        oi_dir = "增仓" if oi_5d > 0 else "减仓"
        vol_label = "放量" if vol_ratio > 1.2 else "缩量" if vol_ratio < 0.8 else "正常"
        print("  | 指标 | 数值 | 状态 |")
        print("  |---|---|---|")
        print(f"  | AU期货持仓 | {oi:.0f}手 | {oi_dir} {oi_5d:+.0f} |")
        print(f"  | 成交量 | {vol:.0f}手 | {vol_label} |")

    sigs = bundle.by_dimension("sentiment")
    if sigs:
        _print_avg_header(sigs)
        _signal_table(sigs)
    else:
        print("  信号: 无")


def print_economic_calendar(bundle: SignalBundle) -> None:
    dim_name = "\U0001f4c5 经济日历"
    sigs = bundle.by_dimension("event")
    if not sigs:
        return

    print(f"\n{'='*60}")
    print(f"  {dim_name}")
    print(f"{'='*60}")

    # 事件维度信号四分类 (2026-08-25 表格化重排):
    # 未来事件 / 复核警告(方向冲突+待查结果) / 近期结果注入 / 军规提醒
    events = [s for s in sigs if s.metadata.get("event_type")]
    warnings = [s for s in sigs if s.metadata.get("rule_id")]

    results = [s for s in events if s.name.startswith(("事件结果", "近期事件"))]
    upcoming = [
        s for s in events
        if not s.name.startswith(("事件结果", "近期事件", "⚠️"))
        and s.metadata.get("event_type") not in ("gold_bias_conflict", "pending_result_sync")
    ]
    review_warnings = [
        s for s in events
        if s.metadata.get("event_type") in ("gold_bias_conflict", "pending_result_sync")
        or s.name.startswith("⚠️")
    ]

    if upcoming:
        print(f"  未来高影响事件 ({len(upcoming)}个):")
        print("  | 事件 | 影响 | ET时间 | 北京时间 | 距今 |")
        print("  |---|---|---|---|---|")
        for sig in upcoming:
            md = sig.metadata
            name = _sig_name(sig).removeprefix("未来事件: ").removeprefix("观测: ")
            impact = _IMPACT_ZH.get(md.get("impact", ""), md.get("impact", "-"))
            et = _fmt_iso(md.get("scheduled_at"))
            bj = _fmt_iso(md.get("scheduled_at_beijing"))
            hours = md.get("hours_until")
            until = f"{hours:.0f}h" if hours is not None and hours < 48 else (
                f"{md.get('days_until', '-')}天" if md.get("days_until") is not None else "-"
            )
            print(f"  | {_cell(name, 44)} | {impact} | {et} | {bj} | {until} |")

    if review_warnings:
        print(f"  ⚠️ 复核/待查 ({len(review_warnings)}个):")
        for sig in review_warnings:
            name = _cell(_sig_name(sig).removeprefix("⚠️ "), 52)
            desc = _cell(_compact_conflict_desc(sig.description or ""), 62)
            print(f"    - {name}: {desc}")

    if results:
        merged = _dedupe_event_results(results)
        print(f"  近期事件结果 ({len(merged)}个):")
        print("  | 事件 | 方向 | 评分 | 距今 | 实际 vs 预期 |")
        print("  |---|---|---|---|---|")
        for sig in merged:
            md = sig.metadata
            name = _sig_name(sig)
            name = name.removeprefix("事件结果: ").removeprefix("近期事件: ")
            d = _DIR_ZH.get(sig.direction.value, sig.direction.value)
            hours_ago = md.get("hours_ago")
            ago = (
                f"{hours_ago/24:.0f}天前" if hours_ago is not None and hours_ago >= 72
                else f"{hours_ago:.0f}h前" if hours_ago is not None else "-"
            )
            actual = md.get("actual") or "-"
            forecast = md.get("forecast")
            av = _cell(actual, 40)
            if forecast:
                av += f" (预期 {_cell(forecast, 15)})"
            print(f"  | {_cell(name, 32)} | {d} | {sig.score:+.2f} | {ago} | {_cell(av, 62)} |")

    if warnings:
        print("  军规提醒:")
        for sig in warnings:
            print(f"    - {_cell(sig.name, 44)}: {_cell(sig.description or '', 62)}")


def _dedupe_event_results(results: list[Signal]) -> list[Signal]:
    """事件结果/近期事件 双版本按事件名去重, 优先保留带 scheduled_at 的更完整版本.

    事件维度同一事件会产生「事件结果: X」(结果注入, 带发生时间/actual) 与
    「近期事件: X」(时效性加权) 两条信号, 表格若都显示会造成 14 行重复观感
    (2026-08-25 排版重排).
    """
    best: dict[str, Signal] = {}
    for sig in results:
        key = _sig_name(sig).removeprefix("事件结果: ").removeprefix("近期事件: ")
        cur = best.get(key)
        if cur is None:
            best[key] = sig
        elif sig.metadata.get("scheduled_at") and not cur.metadata.get("scheduled_at"):
            best[key] = sig
    return list(best.values())


def _compact_conflict_desc(desc: str) -> str:
    """'写入判定=X 与关键词推断=Y 冲突, 以写入判定为准...' -> 紧凑单行 '写入 X vs 推断 Y 冲突'."""
    m = re.search(r"写入判定=(\S+)\s+与关键词推断=(\S+)\s+冲突", desc)
    if not m:
        return desc
    tail = ""
    tm = re.search(r"需人工复核(.*)$", desc)
    if tm:
        tail = _cell(tm.group(1), 24)
    return f"写入 {m.group(1)} vs 推断 {m.group(2)} 冲突, 需人工复核 {tail}"


def _fmt_iso(iso: str | None) -> str:
    """ISO 时间 -> 'MM-DD HH:MM' 短格式 (表格用)."""
    if not iso:
        return "-"
    from datetime import datetime as _dt
    try:
        return _dt.fromisoformat(iso).strftime("%m-%d %H:%M")
    except ValueError:
        return _cell(iso, 11)


def print_smart_money(bundle: SignalBundle) -> None:
    """聪明钱资金流维度 - 聚合 CFTC COT + ETF 资金流 + 机构持仓.

    «这个市场谁在买、谁在卖、谁在套保--比新闻头条更诚实。»
    """
    # 从 bundle 中筛选所有聪明钱相关信号（2026-08-22 起 smart_money 已是独立维度）
    all_smart: list[Signal] = [
        sig for sig in bundle.by_dimension("smart_money")
        if sig.metadata.get("source", "") in _SMART_MONEY_SOURCES
    ]

    if not all_smart:
        return

    dim_name = "\U0001f468‍\U0001f4bc 聪明钱资金流"
    print(f"\n{'='*60}")
    print(f"  {dim_name}")
    print(f"{'='*60}")

    # 按来源分组
    groups: dict[str, list[Signal]] = {}
    for sig in all_smart:
        src = sig.metadata.get("source", "other")
        groups.setdefault(src, []).append(sig)

    # 展示顺序: COT -> ETF -> 投行 -> 大户 -> jdgold -> 13F -> 综合
    _order = [
        "cot_report", "gld_holdings_tonnes",
        "gold_etf_price_proxy", "gold_etf_volume_proxy",
        "intl_gold_etf_volume_proxy", "domestic_intl_divergence",
        "cross_etf",
        "bank_targets", "comex_large_traders", "jd_fund_bomb",
        "jd_blogger_rank", "13f_institutional",
        "smart_money_composite",
    ]

    # 子项短标签 (2026-08-25 表格化, 共享 _cell/_DIR_ZH/_STRENGTH_ZH 工具)
    _short_labels: dict[str, str] = {
        "cot_report": "📊 COT持仓",
        "gld_holdings_tonnes": "📦 GLD持仓",
        "gold_etf_price_proxy": "📈 ETF价格代理",
        "gold_etf_volume_proxy": "📈 ETF成交量代理",
        "intl_gold_etf_volume_proxy": "🌍 国际ETF资金流",
        "domestic_intl_divergence": "↔️ 国内外ETF背离",
        "cross_etf": "🔄 金vs比特币ETF",
        "bank_targets": "🏦 投行目标价",
        "comex_large_traders": "🎯 COMEX大户",
        "jd_fund_bomb": "💣 资金炸弹",
        "jd_blogger_rank": "👑 大V加仓榜",
        "13f_institutional": "📋 13F持仓",
        "smart_money_composite": "🧠 综合评分",
    }

    rows: list[Signal] = []
    for src_key in _order:
        rows.extend(groups.get(src_key, []))
    # 未知来源兜底追加 (新信号源未登记 _order 时不丢)
    known = set(_order)
    rows.extend(s for s in all_smart if s.metadata.get("source", "other") not in known)

    if rows:
        print("  | 子项 | 信号 | 方向 | 强度 | 评分 | 说明 |")
        print("  |---|---|---|---|---|---|")
        for sig in rows:
            src = sig.metadata.get("source", "other")
            sub = _short_labels.get(src, src)
            st = _STRENGTH_ZH.get(sig.strength.value, sig.strength.value)
            print(
                f"  | {sub} | {_cell(_sig_name(sig), 40)} "
                f"| {_DIR_ZH.get(sig.direction.value, sig.direction.value)} "
                f"| {st} | {sig.score:+.2f} | {_cell(sig.description, 60)} |"
            )


def _strip_obs(name: str) -> str:
    """去掉 monitor 名称冗余 '观测: ' 前缀 (板块头已标注观测/触发, 2026-08-25)."""
    return re.sub(r"^\s*观测:\s*", "", name)


def print_monitor(bundle: SignalBundle) -> None:
    """监控触发维度 (2026-08-25 新增渲染: 此前无框线板块, 报告恒为空态, 27个活跃 monitor 全被吞)."""
    sigs = bundle.by_dimension("monitor")
    if not sigs:
        return

    dim_name = "\U0001f4e1 监控触发"
    print(f"\n{'='*60}")
    print(f"  {dim_name}")
    print(f"{'='*60}")

    triggered = [s for s in sigs if s.name.startswith("Monitor触发:")]
    watching = [s for s in sigs if s.name.startswith("Monitor观测:")]

    if triggered:
        print(f"  已触发 ({len(triggered)}个):")
        print("  | Monitor | 方向 | 距今 | 触发结果 |")
        print("  |---|---|---|---|")
        for sig in triggered:
            md = sig.metadata
            hours_ago = md.get("hours_ago")
            ago = (
                f"{hours_ago/24:.0f}天前" if hours_ago is not None and hours_ago >= 72
                else f"{hours_ago:.0f}h前" if hours_ago is not None else "-"
            )
            print(
                f"  | {_cell(_strip_obs(str(md.get('monitor_name', _sig_name(sig)))), 34)} "
                f"| {_DIR_ZH.get(sig.direction.value, sig.direction.value)} "
                f"| {ago} | {_cell(md.get('trigger_result', ''), 42)} |"
            )

    if watching:
        print(f"  观测中 ({len(watching)}个):")
        # 前 5 个保留触发条件明细, 其余折成多行名称分组 (2026-08-25 排版: 旧版
        # 130 字符单行硬塞 15 个 monitor, 与后续板块视觉上黏成一团)
        show, rest = watching[:5], watching[5:]
        print("  | Monitor | 触发条件 |")
        print("  |---|---|")
        for sig in show:
            md = sig.metadata
            name = _strip_obs(str(md.get("monitor_name", _sig_name(sig))))
            print(
                f"  | {_cell(name, 34)} "
                f"| {_cell(md.get('trigger_condition', ''), 60)} |"
            )
        if rest:
            rest_names = [
                _strip_obs(str(s.metadata.get("monitor_name", _sig_name(s))))
                for s in rest
            ]
            print(f"  其余 {len(rest)} 个(仅名称):")
            for i in range(0, len(rest_names), 3):
                print(f"    - " + "、".join(rest_names[i:i + 3]))
        print()


def print_all_dimensions(
    gold_df, dxy_df, rate_df, breakeven_df, silver_df,
    news_items, au_df, bundle, trend_gate: dict | None = None,
) -> None:
    print_technical(gold_df, bundle, trend_gate)
    print_fundamental(dxy_df, rate_df, breakeven_df, gold_df, silver_df, bundle)
    print_monitor(bundle)
    print_smart_money(bundle)
    print_news(news_items, bundle)
    print_sentiment(au_df, bundle)
    print_economic_calendar(bundle)
