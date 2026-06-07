"""测试价格预警."""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from gold_miner.execution.alert import Alert, PriceAlert


def _make_gold_df(prices: list[float]) -> pd.DataFrame:
    dates = [datetime.now() - timedelta(days=i) for i in range(len(prices), 0, -1)]
    return pd.DataFrame({
        "timestamp": dates,
        "open": prices,
        "high": [p * 1.01 for p in prices],
        "low": [p * 0.99 for p in prices],
        "close": prices,
        "volume": [1000] * len(prices),
    })


def _make_dxy_df(values: list[float]) -> pd.DataFrame:
    dates = [datetime.now() - timedelta(days=i) for i in range(len(values), 0, -1)]
    return pd.DataFrame({
        "timestamp": dates,
        "value": values,
    })


class TestPriceAlert:
    def test_no_alerts_on_stable_price(self):
        alert = PriceAlert()
        gold_df = _make_gold_df([2000.0, 2005.0, 2002.0, 2003.0, 2004.0])
        alerts = alert.check_all(gold_df=gold_df)
        assert len(alerts) == 0

    def test_big_move_alert(self, monkeypatch):
        monkeypatch.setattr("gold_miner.execution.alert.settings.alert_big_move_pct", 1.0)
        alert = PriceAlert()
        gold_df = _make_gold_df([2000.0, 2050.0])  # +2.5%
        alerts = alert.check_all(gold_df=gold_df)
        assert any(a.name == "大波动" for a in alerts)

    def test_key_level_break_high(self, monkeypatch):
        monkeypatch.setattr("gold_miner.execution.alert.settings.alert_key_level_lookback", 3)
        alert = PriceAlert()
        gold_df = _make_gold_df([100, 105, 110, 120])  # last > all previous highs
        alerts = alert.check_all(gold_df=gold_df)
        assert any(a.name == "关键位突破" for a in alerts)

    def test_dxy_move_alert(self, monkeypatch):
        monkeypatch.setattr("gold_miner.execution.alert.settings.alert_dxy_move_pct", 0.5)
        alert = PriceAlert()
        dxy_df = _make_dxy_df([100.0, 101.0])
        gold_df = _make_gold_df([2000.0, 2005.0])
        alerts = alert.check_all(gold_df=gold_df, dxy_df=dxy_df)
        assert any(a.name == "DXY异动" for a in alerts)

    def test_gold_silver_ratio_high(self, monkeypatch):
        monkeypatch.setattr("gold_miner.execution.alert.settings.alert_gold_silver_ratio_high", 85)
        alert = PriceAlert()
        gold_df = _make_gold_df([2000.0, 2000.0])
        alerts = alert.check_all(gold_df=gold_df, silver_price=20.0)  # ratio = 100
        assert any(a.name == "金银比极值" for a in alerts)

    def test_empty_dataframe(self):
        alert = PriceAlert()
        gold_df = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close"])
        alerts = alert.check_all(gold_df=gold_df)
        assert alerts == []
