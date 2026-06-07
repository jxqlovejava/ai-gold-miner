"""四维度详细输出 — 技术面/基本面/消息面/情绪面."""

import pandas as pd

from gold_miner.signals.base import SignalBundle


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
        print(f"  信号: 无 (技术指标未触发极端值)")


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
            print(f"    [{e}] {sig.name}: {sig.score:+.2f}")
            if sig.description:
                print(f"        {sig.description[:50]}")
    else:
        print(f"  信号: 无 (新闻情感未达阈值)")

    if news_items:
        print(f"  {'-'*56}")
        print(f"  最近新闻 (NewsAPI, {len(news_items)}条):")
        for item in news_items[:6]:
            s = item.sentiment
            e = "+" if s > 0.1 else "-" if s < -0.1 else "o"
            print(f"    [{e}] [{item.source[:12]}] {item.title[:50]}")


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
        print(f"  数据: 暂不可用")

    sigs = bundle.by_dimension("sentiment")
    print(f"  {'-'*56}")
    if sigs:
        avg = sum(s.score for s in sigs) / len(sigs)
        print(f"  信号 ({len(sigs)}个, 均分 {avg:+.2f}):")
        for sig in sigs:
            e = "+" if sig.score > 0 else "-" if sig.score < 0 else "o"
            print(f"    [{e}] {sig.name}: {sig.score:+.2f}  {sig.description[:40]}")
    else:
        print(f"  信号: 无")


def print_all_dimensions(
    gold_df, dxy_df, rate_df, breakeven_df, silver_df,
    news_items, au_df, bundle,
) -> None:
    print_technical(gold_df, bundle)
    print_fundamental(dxy_df, rate_df, breakeven_df, gold_df, silver_df, bundle)
    print_news(news_items, bundle)
    print_sentiment(au_df, bundle)
