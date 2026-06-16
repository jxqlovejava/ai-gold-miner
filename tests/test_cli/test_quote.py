"""Quote command tests."""

from __future__ import annotations

from unittest.mock import patch

from gold_miner.cli.quote import run_quote


def test_run_quote_prints_price(capsys):
    """quote 命令应输出价格信息."""
    with patch("gold_miner.cli.quote.SpotGoldFetcher") as mock_fetcher:
        instance = mock_fetcher.return_value
        instance.fetch_realtime_quote.return_value = {
            "domestic_price": 937.51,
            "domestic_change_pct": 0.001,
        }
        run_quote()

    captured = capsys.readouterr()
    assert "现货黄金报价" in captured.out
    assert "937.51" in captured.out
