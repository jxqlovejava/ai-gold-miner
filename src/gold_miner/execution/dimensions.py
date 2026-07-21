"""多维度详细输出 — 技术面/基本面/消息面/情绪面/资金流/经济日历."""
from __future__ import annotations

import pandas as pd

from gold_miner.signals.base import Signal, SignalBundle

# 聪明钱资金流信号 source 分类
_SMART_MONEY_SOURCES = frozenset({
    "cot_report",          # CFTC COT 持仓报告
    "gld_holdings_tonnes", # GLD 官方持仓
    "gold_etf_price_proxy",
    "gold_etf_volume_proxy",
    "intl_gold_etf_volume_proxy",
    "domestic_intl_divergence",
    "btc_etf",
    "cross_etf",
    "bank_targets",        # 投行目标价共识
    "comex_large_traders", # COMEX 大户集中度
    "13f_institutional",   # 13F 机构持仓
    "smart_money_composite",  # 聪明钱综合信号
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


def print_technical(gold_df: pd.DataFrame, bundle: SignalBundle) -> None:
    if gold_df.empty:
        return
    close = gold_df["close"]
    latest = close.iloc[-1]
    rsi_val = _calc_rsi(close)
    rsi_label = "超卖" if rsi_val < 30 else "超买" if rsi_val > 70 else "中性"
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
    print(f"  RSI(14): {rsi_val:.0f} ({rsi_label})")
    print(f"  MACD: {macd:+.2f} ({macd_label})")
    print(f"  布林带: {bb_pos:.0f}% ({bb_label})  上{upper:.0f}  中{sma20:.0f}  下{lower:.0f}")
    print(f"  20日区间: {low_20:.0f} ~ {high_20:.0f}  距支撑{((latest-low_20)/low_20*100):+.1f}%  距阻力{((high_20-latest)/high_20*100):+.1f}%")

    sigs = bundle.by_dimension("technical")
    print(f"  {'-'*56}")
    if sigs:
        avg = sum(s.score for s in sigs) / len(sigs)
        print(f"  信号 ({len(sigs)}个, 均分 {avg:+.2f}):")
        for sig in sigs:
            e = "+" if sig.score > 0 else "-"
            print(f"    [{e}] {sig.name}: {sig.score:+.2f}  {sig.description[:40]}")
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

    if not dxy_df.empty:
        dxy_now = dxy_df["value"].iloc[-1]
        dxy_20 = dxy_df["value"].tail(20).mean()
        dxy_dir = "走弱" if dxy_now < dxy_20 else "走强"
        print(f"  美元指数 DXY: {dxy_now:.2f} ({dxy_dir}, 20日均 {dxy_20:.2f})")
    if not rate_df.empty:
        rate_now = rate_df["value"].iloc[-1]
        rate_20 = rate_df["value"].tail(20).mean()
        rate_dir = "v" if rate_now < rate_20 else "^"
        print(f"  10Y 实际利率: {rate_now:.2f}% ({rate_dir} 20日均 {rate_20:.2f}%)")
    if not breakeven_df.empty:
        be_now = breakeven_df["value"].iloc[-1]
        be_20 = breakeven_df["value"].tail(20).mean()
        be_dir = "v" if be_now < be_20 else "^"
        print(f"  盈亏平衡通胀率: {be_now:.2f}% ({be_dir} 20日均 {be_20:.2f}%)")
    if not gold_df.empty and not silver_df.empty:
        gold_s = gold_df["close"].iloc[-1]
        silver_s = silver_df["value"].iloc[-1]
        ratio = gold_s / silver_s if silver_s > 0 else 0
        ratio_label = "极高位(避险极端)" if ratio > 85 else "低位(风险偏好高)" if ratio < 60 else "正常"
        print(f"  金银比: {ratio:.1f} ({ratio_label})")

    sigs = bundle.by_dimension("fundamental")
    print(f"  {'-'*56}")
    if sigs:
        avg = sum(s.score for s in sigs) / len(sigs)
        print(f"  信号 ({len(sigs)}个, 均分 {avg:+.2f}):")
        for sig in sigs:
            e = "+" if sig.score > 0 else "-"
            print(f"    [{e}] {sig.name}: {sig.score:+.2f}  {sig.description[:40]}")


def print_news(news_items: list, bundle: SignalBundle) -> None:
    dim_name = "\U0001f4f0 消息面"
    print(f"\n{'='*60}")
    print(f"  {dim_name}")
    print(f"{'='*60}")

    sigs = bundle.by_dimension("news")
    if sigs:
        avg = sum(s.score for s in sigs) / len(sigs)
        print(f"  信号 ({len(sigs)}个, 均分 {avg:+.2f}):")
        for sig in sigs:
            e = "+" if sig.score > 0 else "-" if sig.score < 0 else "o"
            # 提取验证标签（优先用 metadata，不用 name 子串判断）
            v_tag = ""
            if sig.metadata.get("verification_status") == "disputed":
                v_tag = "[disputed]"
            elif sig.metadata.get("source_tier"):
                v_tag = f"[verified: {sig.metadata['source_tier']}]"
            elif sig.metadata.get("aggregate_tier"):
                v_tag = sig.metadata["aggregate_tier"]
            print(f"    [{e}] {sig.name}{v_tag}: {sig.score:+.2f}")
            if sig.description:
                print(f"        {sig.description[:50]}")
    else:
        print("  信号: 无 (新闻情感未达阈值)")

    if news_items:
        print(f"  {'-'*56}")
        print(f"  最近新闻 (NewsAPI, {len(news_items)}条):")
        for item in news_items[:6]:
            s = item.sentiment
            e = "+" if s > 0.1 else "-" if s < -0.1 else "o"
            tier = item.metadata.get("source_tier", "")
            tier_tag = f"[{tier}]" if tier else ""
            print(f"    [{e}] [{item.source[:12]}]{tier_tag} {item.title[:50]}")


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
        print(f"  AU期货持仓: {oi:.0f}手 ({oi_dir} {oi_5d:+.0f})  成交量: {vol:.0f}手 ({vol_label})")
    else:
        print("  数据: 暂不可用")

    sigs = bundle.by_dimension("sentiment")
    print(f"  {'-'*56}")
    if sigs:
        avg = sum(s.score for s in sigs) / len(sigs)
        print(f"  信号 ({len(sigs)}个, 均分 {avg:+.2f}):")
        for sig in sigs:
            e = "+" if sig.score > 0 else "-" if sig.score < 0 else "o"
            print(f"    [{e}] {sig.name}: {sig.score:+.2f}  {sig.description[:40]}")
    else:
        print("  信号: 无")


def print_economic_calendar(bundle: SignalBundle) -> None:
    dim_name = "\U0001f4c5 经济日历"
    sigs = bundle.by_dimension("event_calendar")
    if not sigs:
        return

    print(f"\n{'='*60}")
    print(f"  {dim_name}")
    print(f"{'='*60}")

    # 分离普通事件提醒与军规提醒
    events = [s for s in sigs if s.metadata.get("event_type")]
    warnings = [s for s in sigs if s.metadata.get("rule_id")]

    if events:
        print(f"  未来高影响事件 ({len(events)}个):")
        for sig in events:
            e = "!" if sig.strength == "strong" else "i"
            print(f"    [{e}] {sig.name}: {sig.description[:60]}")

    if warnings:
        print(f"  {'-'*56}")
        print("  军规提醒:")
        for sig in warnings:
            print(f"    [!] {sig.name}: {sig.description[:60]}")


def print_smart_money(bundle: SignalBundle) -> None:
    """聪明钱资金流维度 — 聚合 CFTC COT + ETF 资金流 + 机构持仓.

    «这个市场谁在买、谁在卖、谁在套保——比新闻头条更诚实。»
    """
    # 从 bundle 中筛选所有聪明钱相关信号
    all_smart: list[Signal] = []
    for dim in ("sentiment", "fundamental"):
        for sig in bundle.by_dimension(dim):
            src = sig.metadata.get("source", "")
            if src in _SMART_MONEY_SOURCES:
                all_smart.append(sig)

    if not all_smart:
        return

    dim_name = "\U0001f468‍\U0001f4bc 聪明钱资金流"
    print(f"\n{'='*60}")
    print(f"  {dim_name}")
    print(f"{'='*60}")

    # 按来源分组展示
    groups: dict[str, list[Signal]] = {}
    for sig in all_smart:
        src = sig.metadata.get("source", "other")
        groups.setdefault(src, []).append(sig)

    # 展示顺序: COT → ETF → 投行 → 大户 → 13F → 综合
    _order = [
        "cot_report", "gld_holdings_tonnes",
        "gold_etf_price_proxy", "gold_etf_volume_proxy",
        "intl_gold_etf_volume_proxy", "domestic_intl_divergence",
        "btc_etf", "cross_etf",
        "bank_targets", "comex_large_traders", "13f_institutional",
        "smart_money_composite",
    ]

    group_labels: dict[str, str] = {
        "cot_report": "\U0001f4ca CFTC COT 期货持仓",
        "gld_holdings_tonnes": "\U0001f4e6 GLD 官方持仓 (吨)",
        "gold_etf_price_proxy": "\U0001f4c8 黄金ETF价格代理",
        "gold_etf_volume_proxy": "\U0001f4ca 黄金ETF成交量代理",
        "intl_gold_etf_volume_proxy": "\U0001f30d 国际黄金ETF资金流",
        "domestic_intl_divergence": "\U00002194 国内外ETF背离",
        "btc_etf": "\U000020bf 比特币ETF资金流",
        "cross_etf": "\U0001f504 黄金vs比特币ETF交叉",
        "bank_targets": "\U0001f3e6 投行目标价共识",
        "comex_large_traders": "\U0001f3af COMEX大户集中度",
        "13f_institutional": "\U0001f4cb 13F机构持仓",
        "smart_money_composite": "\U0001f9e0 聪明钱综合评分",
    }

    for src_key in _order:
        sigs = groups.get(src_key, [])
        if not sigs:
            continue
        label = group_labels.get(src_key, src_key)
        print(f"  {label}:")
        for sig in sigs:
            d = "\U0001f7e2" if sig.direction.value == "bullish" else "\U0001f534" if sig.direction.value == "bearish" else "\U000026ab"
            print(f"    {d} {sig.name} [{sig.strength.value}]: {sig.score:+.2f}")
            if sig.description:
                print(f"       {sig.description[:100]}")

    # 加权汇总
    all_scores = [s.score for s in all_smart if abs(s.score) > 0.05]
    if all_scores:
        avg = sum(all_scores) / len(all_scores)
        n_bullish = sum(1 for s in all_smart if s.direction.value == "bullish")
        n_bearish = sum(1 for s in all_smart if s.direction.value == "bearish")
        consensus = (
            "一致看多" if n_bullish >= n_bearish + 3
            else "一致看空" if n_bearish >= n_bullish + 3
            else "偏多" if n_bullish > n_bearish
            else "偏空" if n_bearish > n_bullish
            else "分歧"
        )
        print(f"  {'-'*56}")
        print(f"  聪明钱共识: {consensus}  |  均分 {avg:+.2f}  |  "
              f"看多信号{n_bullish}个 看空信号{n_bearish}个  "
              f"({len(all_smart)}个有效信号)")


def print_all_dimensions(
    gold_df, dxy_df, rate_df, breakeven_df, silver_df,
    news_items, au_df, bundle,
) -> None:
    print_technical(gold_df, bundle)
    print_fundamental(dxy_df, rate_df, breakeven_df, gold_df, silver_df, bundle)
    print_smart_money(bundle)
    print_news(news_items, bundle)
    print_sentiment(au_df, bundle)
    print_economic_calendar(bundle)
