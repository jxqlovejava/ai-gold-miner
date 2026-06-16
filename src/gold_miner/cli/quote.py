"""Quote command handler."""

from __future__ import annotations

from loguru import logger

from gold_miner.data.accumulation_gold import AccumulationGoldFetcher
from gold_miner.data.spot_gold import SpotGoldFetcher


def run_quote() -> None:
    """Fetch and display gold quotes."""
    fetcher = SpotGoldFetcher()
    quote = fetcher.fetch_realtime_quote()
    print("现货黄金报价:")
    for k, v in quote.items():
        print(f"  {k}: {v}")

    try:
        acc_fetcher = AccumulationGoldFetcher()
        acc_latest = acc_fetcher.fetch_latest()
        if not acc_latest.empty:
            acc_row = acc_latest.iloc[-1]
            print("\n积存金 (Au99.99 人民币/克):")
            print(f"  最新价: {acc_row['close']:.2f}")
            acc_premium = acc_fetcher.fetch_premium()
            if acc_premium.get("premium_pct"):
                print(f"  相对现货溢价: {acc_premium['premium_pct']:+.2%}")
    except Exception as e:
        logger.warning(f"积存金数据获取失败: {e}")
