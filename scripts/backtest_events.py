"""事件驱动回测 — 用已有经济数据验证关键信号对金价方向的预测能力.

三个回测:
  A. COT Managed Money 净多头变化 → 1w 金价方向
  B. 央行季度净购金 > 250t → 1m 金价方向
  C. GLD 持仓周度变化 → 1d 金价方向
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd
from loguru import logger

from gold_miner.data.cot_data import CotDataFetcher
from gold_miner.data.gld_holdings import GldHoldingsFetcher
from gold_miner.data.spot_gold import SpotGoldFetcher


def load_data() -> dict[str, pd.DataFrame]:
    print("加载数据...")
    gold = SpotGoldFetcher()
    gold_df = gold.fetch(days=365 * 3)  # 3 years

    cot = CotDataFetcher()
    cot_df = cot.fetch(start=datetime(2023, 1, 1), contract="088691")

    gld = GldHoldingsFetcher()
    gld_df = gld.fetch(start=datetime(2023, 1, 1))

    # 对齐 gold 价格为日内 close
    gold_daily = gold_df[["timestamp", "close"]].copy()
    gold_daily["timestamp"] = pd.to_datetime(gold_daily["timestamp"])

    logger.info(f"Gold: {len(gold_daily)}d, COT: {len(cot_df)}w, GLD: {len(gld_df)}d")
    return {"gold": gold_daily, "cot": cot_df, "gld": gld_df}


def forward_return(
    gold: pd.DataFrame,
    event_date: pd.Timestamp,
    forward_days: int,
) -> float | None:
    """计算事件后 forward_days 的金价变化百分比."""
    target = event_date + pd.Timedelta(days=forward_days)
    gold_sorted = gold.sort_values("timestamp")
    try:
        start_mask = gold_sorted["timestamp"] >= event_date
        start_idx = gold_sorted[start_mask].index[0]
        start_price = gold_sorted.loc[start_idx, "close"]

        end_mask = gold_sorted["timestamp"] >= target
        if not end_mask.any():
            return None
        end_idx = gold_sorted[end_mask].index[0]
        end_price = gold_sorted.loc[end_idx, "close"]

        return (end_price - start_price) / start_price
    except (IndexError, KeyError):
        return None


def describe(results: list[dict], label: str) -> dict[str, Any]:
    returns = [r["forward_return"] for r in results if r.get("forward_return") is not None]
    if not returns:
        return {"label": label, "count": 0}
    wins = sum(1 for r in returns if r > 0)
    return {
        "label": label,
        "count": len(results),
        "valid_n": len(returns),
        "win_rate": wins / len(returns),
        "avg_return": sum(returns) / len(returns),
        "max_return": max(returns),
        "min_return": min(returns),
    }


# ---- Test A: COT Managed Money 净多头变化 → 1w 金价 ----

def test_a_cot_managed_money(gold: pd.DataFrame, cot: pd.DataFrame) -> pd.DataFrame:
    print("\n=== 回测 A: COT Managed Money 净多变化 → 1周金价 ===")
    results: list[dict] = []
    cot_sorted = cot.sort_values("timestamp").reset_index(drop=True)

    for i in range(1, len(cot_sorted)):
        prev = cot_sorted.iloc[i - 1]
        curr = cot_sorted.iloc[i]
        mm_net_prev = prev.get("managed_money_long", 0) - prev.get("managed_money_short", 0)
        mm_net_curr = curr.get("managed_money_long", 0) - curr.get("managed_money_short", 0)
        mm_change = mm_net_curr - mm_net_prev
        mm_change_pct = mm_change / max(abs(mm_net_prev), 1)

        fwd = forward_return(gold, curr["timestamp"], 5)  # 5 trading days ≈ 1 week
        event_type = "mm_increase" if mm_change > 0 else "mm_decrease"

        results.append({
            "date": curr["timestamp"],
            "event_type": event_type,
            "mm_change": mm_change,
            "mm_change_pct": mm_change_pct,
            "forward_return": fwd,
        })

    df = pd.DataFrame(results)
    if df.empty:
        return df

    df["signal"] = df["mm_change"].apply(lambda x: "bullish" if x > 0 else "bearish")

    for label, subset in [
        ("全部信号", df),
        ("MM净多增加 (bullish)", df[df["signal"] == "bullish"]),
        ("MM净多减少 (bearish)", df[df["signal"] == "bearish"]),
    ]:
        stats = describe(subset.to_dict("records"), label)
        print(f"  {label}: N={stats['valid_n']}, win={stats.get('win_rate',0):.1%}, avg={stats.get('avg_return',0):.2%}")

    return df


# ---- Test B: 央行季度购金 >250t → 1月金价 ----

CENTRAL_BANK_QUARTERLY: list[dict] = [
    {"quarter": "Q1 2023", "tonnes": 228, "date": "2023-03-31"},
    {"quarter": "Q2 2023", "tonnes": 175, "date": "2023-06-30"},
    {"quarter": "Q3 2023", "tonnes": 337, "date": "2023-09-30"},
    {"quarter": "Q4 2023", "tonnes": 229, "date": "2023-12-31"},
    {"quarter": "Q1 2024", "tonnes": 290, "date": "2024-03-31"},
    {"quarter": "Q2 2024", "tonnes": 184, "date": "2024-06-30"},
    {"quarter": "Q3 2024", "tonnes": 186, "date": "2024-09-30"},
    {"quarter": "Q4 2024", "tonnes": 333, "date": "2024-12-31"},
    {"quarter": "Q1 2025", "tonnes": 292, "date": "2025-03-31"},
    {"quarter": "Q2 2025", "tonnes": 198, "date": "2025-06-30"},
    {"quarter": "Q3 2025", "tonnes": 220, "date": "2025-09-30"},
    {"quarter": "Q4 2025", "tonnes": 345, "date": "2025-12-31"},
    # Q1 2026 已被 WGC 于 2026-07 下修（244t → 57t，187t 重分类至 OTC）
    {"quarter": "Q1 2026", "tonnes": 57, "date": "2026-03-31"},
    {"quarter": "Q2 2026", "tonnes": 289, "date": "2026-06-30"},
]


def test_b_central_bank(gold: pd.DataFrame) -> pd.DataFrame:
    print("\n=== 回测 B: 央行季度净购金 → 1月金价 ===")
    results: list[dict] = []

    for cb in CENTRAL_BANK_QUARTERLY:
        event_date = pd.Timestamp(cb["date"])
        fwd_1m = forward_return(gold, event_date, 30)
        fwd_3m = forward_return(gold, event_date, 90)
        label = "heavy_buying" if cb["tonnes"] > 250 else "moderate"

        results.append({
            "quarter": cb["quarter"],
            "tonnes": cb["tonnes"],
            "event_date": event_date,
            "label": label,
            "forward_1m": fwd_1m,
            "forward_3m": fwd_3m,
        })

    df = pd.DataFrame(results)
    if df.empty:
        return df

    for lbl, fd in [(">250t 重购金", df[df["label"] == "heavy_buying"]), ("≤250t 温和", df[df["label"] == "moderate"])]:
        r1 = [r for r in fd["forward_1m"] if not pd.isna(r)]
        r3 = [r for r in fd["forward_3m"] if not pd.isna(r)]
        w1 = sum(1 for r in r1 if r > 0) / len(r1) if r1 else 0
        w3 = sum(1 for r in r3 if r > 0) / len(r3) if r3 else 0
        a1 = sum(r1) / len(r1) if r1 else 0
        a3 = sum(r3) / len(r3) if r3 else 0
        print(f"  {lbl}: N={len(fd)}, 1m_win={w1:.0%} 1m_avg={a1:.2%}, 3m_win={w3:.0%} 3m_avg={a3:.2%}")

    return df


# ---- Test C: GLD 持仓周度变化 → 1d 金价 ----

def test_c_gld_flow(gold: pd.DataFrame, gld: pd.DataFrame) -> pd.DataFrame:
    print("\n=== 回测 C: GLD 持仓周度变化 → 1日金价 ===")
    gld_daily = gld.sort_values("timestamp").reset_index(drop=True)
    gld_daily["gld_weekly_change"] = gld_daily["value"].diff(5)  # 5日差分 ≈ 周度变化

    results: list[dict] = []
    for i in range(5, len(gld_daily)):
        row = gld_daily.iloc[i]
        change = row["gld_weekly_change"]
        if pd.isna(change):
            continue
        fwd_1d = forward_return(gold, row["timestamp"], 1)
        signal = "inflow" if change > 0 else "outflow"
        results.append({
            "date": row["timestamp"],
            "gld_weekly_change": change,
            "signal": signal,
            "forward_1d": fwd_1d,
        })

    df = pd.DataFrame(results)
    if df.empty:
        return df

    for label, subset in [
        ("全部", df),
        ("GLD流入周", df[df["signal"] == "inflow"]),
        ("GLD流出周", df[df["signal"] == "outflow"]),
    ]:
        returns = [r for r in subset["forward_1d"] if not pd.isna(r)]
        if not returns:
            print(f"  {label}: 无数据")
            continue
        wins = sum(1 for r in returns if r > 0)
        print(f"  {label}: N={len(returns)}, win={wins/len(returns):.1%}, avg={sum(returns)/len(returns):.2%}")

    return df


def main():
    data = load_data()
    gold = data["gold"]
    cot = data["cot"]
    gld = data["gld"]

    # A
    result_a = test_a_cot_managed_money(gold, cot)

    # B
    result_b = test_b_central_bank(gold)

    # C
    result_c = test_c_gld_flow(gold, gld)

    print("\n" + "=" * 60)
    print("  回测总结")
    print("=" * 60)

    # COT signal effectiveness
    if not result_a.empty:
        correct = ((result_a["signal"] == "bullish") & (result_a["forward_return"] > 0) |
                   (result_a["signal"] == "bearish") & (result_a["forward_return"] < 0))
        acc = correct.sum() / len(result_a) if len(result_a) > 0 else 0
        print(f"  COT Managed Money 方向准确率: {acc:.1%} (N={len(result_a)})")

    # Central bank heavy buying
    if not result_b.empty:
        heavy = result_b[result_b["label"] == "heavy_buying"]
        h1 = [r for r in heavy["forward_1m"] if not pd.isna(r)]
        win_rate = sum(1 for r in h1 if r > 0) / len(h1) if h1 else 0
        print(f"  央行重购金 (>250t) 1月胜率: {win_rate:.0%} ({len(h1)}次)")

    # GLD flow signal
    if not result_c.empty:
        correct = ((result_c["signal"] == "inflow") & (result_c["forward_1d"] > 0) |
                   (result_c["signal"] == "outflow") & (result_c["forward_1d"] < 0))
        acc = correct.sum() / len(result_c) if len(result_c) > 0 else 0
        print(f"  GLD 周度流向 1日方向准确率: {acc:.1%} (N={len(result_c)})")


if __name__ == "__main__":
    main()
