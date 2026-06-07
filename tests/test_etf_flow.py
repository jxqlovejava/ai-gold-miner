"""ETF资金流模块单元测试."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from gold_miner.data.etf_flow import (
    BtcEtfFlowFetcher,
    EtfFlowRecord,
    GoldEtfFlowFetcher,
)
from gold_miner.signals.etf_flow_signal import turnover_fmt
from gold_miner.signals.etf_flow_signal import EtfFlowSignalGenerator
from gold_miner.signals.base import SignalDirection, SignalStrength


class TestTurnoverFmt:
    def test_billion(self) -> None:
        assert turnover_fmt(1_5000_0000) == "1.50亿"

    def test_wan(self) -> None:
        assert turnover_fmt(50_000) == "5万"

    def test_small_number(self) -> None:
        assert turnover_fmt(999) == "999"

    def test_zero(self) -> None:
        assert turnover_fmt(0) == "0"


class TestGoldEtfFlowFetcher:
    def test_fetch_latest_delegates_to_fetch(self) -> None:
        fetcher = GoldEtfFlowFetcher()
        with patch.object(fetcher, "fetch", return_value=pd.DataFrame({"a": [1]})) as mock_fetch:
            df = fetcher.fetch_latest()
            mock_fetch.assert_called_once()
            assert df.equals(pd.DataFrame({"a": [1]}))

    def test_fetch_flow_summary_empty(self) -> None:
        fetcher = GoldEtfFlowFetcher()
        with patch.object(fetcher, "fetch", return_value=pd.DataFrame()):
            result = fetcher.fetch_flow_summary()
            assert result["status"] == "no_data"

    def test_fetch_flow_summary_ok(self) -> None:
        fetcher = GoldEtfFlowFetcher()
        df = pd.DataFrame({
            "代码": ["518880"],
            "名称": ["黄金ETF华安"],
            "成交量": [10000],
            "成交额": [5000000],
        })
        with patch.object(fetcher, "fetch", return_value=df):
            result = fetcher.fetch_flow_summary()
            assert result["status"] == "ok"
            assert result["total_volume"] == 10000
            assert result["total_turnover"] == 5000000.0

    def test_fetch_daily_change_empty(self) -> None:
        fetcher = GoldEtfFlowFetcher()
        with patch.object(fetcher, "fetch", return_value=pd.DataFrame()):
            result = fetcher.fetch_daily_change()
            assert result["status"] == "no_data"

    def test_fetch_daily_change_with_growth(self) -> None:
        fetcher = GoldEtfFlowFetcher()
        df = pd.DataFrame({
            "成交量": [10000],
            "成交额": [5000000],
            "日增长率": [1.5],
        })
        with patch.object(fetcher, "fetch", return_value=df):
            result = fetcher.fetch_daily_change()
            assert result["status"] == "ok"
            assert result["flow_direction"] == "inflow"
            assert result["avg_nav_change_pct"] == 1.5


class TestBtcEtfFlowFetcher:
    def test_fetch_latest_delegates_to_fetch(self) -> None:
        fetcher = BtcEtfFlowFetcher()
        with patch.object(fetcher, "fetch", return_value=pd.DataFrame({"a": [1]})) as mock_fetch:
            df = fetcher.fetch_latest()
            mock_fetch.assert_called_once()

    def test_fetch_flow_signal_empty(self) -> None:
        fetcher = BtcEtfFlowFetcher()
        with patch.object(fetcher, "fetch", return_value=pd.DataFrame()):
            result = fetcher.fetch_flow_signal()
            assert result["status"] == "no_data"
            assert result["direction"] == "neutral"

    def test_fetch_flow_signal_strong_inflow(self) -> None:
        fetcher = BtcEtfFlowFetcher()
        df = pd.DataFrame({
            "symbol": ["IBIT", "FBTC", "GBTC", "ARKB", "BITB"],
            "change_pct": [2.0, 1.5, 1.2, 1.8, 1.1],
            "volume_ratio": [1.5, 1.4, 1.3, 1.5, 1.4],
            "volume": [10000, 20000, 15000, 12000, 8000],
        })
        with patch.object(fetcher, "fetch", return_value=df):
            result = fetcher.fetch_flow_signal()
            assert result["status"] == "ok"
            assert result["direction"] == "strong_inflow"
            assert result["score"] > 0

    def test_fetch_flow_signal_strong_outflow(self) -> None:
        fetcher = BtcEtfFlowFetcher()
        df = pd.DataFrame({
            "symbol": ["IBIT", "FBTC", "GBTC", "ARKB", "BITB"],
            "change_pct": [-2.0, -1.5, -1.2, -1.8, -1.1],
            "volume_ratio": [1.5, 1.4, 1.3, 1.5, 1.4],
            "volume": [10000, 20000, 15000, 12000, 8000],
        })
        with patch.object(fetcher, "fetch", return_value=df):
            result = fetcher.fetch_flow_signal()
            assert result["status"] == "ok"
            assert result["direction"] == "strong_outflow"
            assert result["score"] < 0


class TestEtfFlowSignalGenerator:
    def test_gold_inflow_signal(self) -> None:
        gen = EtfFlowSignalGenerator()
        gold_summary = {
            "status": "ok",
            "flow_direction": "inflow",
            "avg_nav_change_pct": 2.0,
            "total_volume": 1_000_000,
            "total_turnover": 5_000_000,
        }
        with patch.object(gen.gold_fetcher, "fetch_daily_change", return_value=gold_summary):
            with patch.object(gen.btc_fetcher, "fetch_flow_signal", return_value={"status": "no_data"}):
                signals = gen._gold_etf_signals()
                assert len(signals) == 1
                assert signals[0].name == "黄金ETF资金流入"
                assert signals[0].direction == SignalDirection.BULLISH
                assert signals[0].strength == SignalStrength.STRONG

    def test_gold_outflow_signal(self) -> None:
        gen = EtfFlowSignalGenerator()
        gold_summary = {
            "status": "ok",
            "flow_direction": "outflow",
            "avg_nav_change_pct": -2.0,
            "total_volume": 1_000_000,
            "total_turnover": 5_000_000,
        }
        with patch.object(gen.gold_fetcher, "fetch_daily_change", return_value=gold_summary):
            with patch.object(gen.btc_fetcher, "fetch_flow_signal", return_value={"status": "no_data"}):
                signals = gen._gold_etf_signals()
                assert len(signals) == 1
                assert signals[0].name == "黄金ETF资金流出"
                assert signals[0].direction == SignalDirection.BEARISH

    def test_gold_volume_surge_bullish(self) -> None:
        gen = EtfFlowSignalGenerator()
        gold_summary = {
            "status": "ok",
            "flow_direction": "neutral",
            "avg_nav_change_pct": 0.1,
            "total_volume": 10_000_000,
            "total_turnover": 50_000_000,
        }
        with patch.object(gen.gold_fetcher, "fetch_daily_change", return_value=gold_summary):
            with patch.object(gen.btc_fetcher, "fetch_flow_signal", return_value={"status": "no_data"}):
                signals = gen._gold_etf_signals()
                vol_signals = [s for s in signals if "成交放量" in s.name]
                assert len(vol_signals) == 1
                assert vol_signals[0].direction == SignalDirection.BULLISH

    def test_btc_strong_inflow_signal(self) -> None:
        gen = EtfFlowSignalGenerator()
        btc_flow = {
            "status": "ok",
            "direction": "strong_inflow",
            "score": 0.8,
            "avg_change_pct": 2.5,
            "volume_surge_etfs": 4,
        }
        with patch.object(gen.btc_fetcher, "fetch_flow_signal", return_value=btc_flow):
            signals = gen._btc_etf_signals()
            assert len(signals) == 1
            assert signals[0].name == "BTC ETF大幅流入(风险偏好↑)"
            assert signals[0].direction == SignalDirection.BEARISH

    def test_btc_strong_outflow_signal(self) -> None:
        gen = EtfFlowSignalGenerator()
        btc_flow = {
            "status": "ok",
            "direction": "strong_outflow",
            "score": -0.8,
            "avg_change_pct": -2.5,
            "volume_surge_etfs": 4,
        }
        with patch.object(gen.btc_fetcher, "fetch_flow_signal", return_value=btc_flow):
            signals = gen._btc_etf_signals()
            assert len(signals) == 1
            assert signals[0].name == "BTC ETF大幅流出(避险↑)"
            assert signals[0].direction == SignalDirection.BULLISH

    def test_cross_asset_divergence_risk_off(self) -> None:
        gen = EtfFlowSignalGenerator()
        gold = {"status": "ok", "flow_direction": "inflow"}
        btc = {"status": "ok", "direction": "strong_outflow"}
        with patch.object(gen.gold_fetcher, "fetch_daily_change", return_value=gold):
            with patch.object(gen.btc_fetcher, "fetch_flow_signal", return_value=btc):
                signals = gen._cross_asset_signals()
                assert len(signals) == 1
                assert "强烈避险" in signals[0].name
                assert signals[0].direction == SignalDirection.BULLISH

    def test_cross_asset_divergence_risk_on(self) -> None:
        gen = EtfFlowSignalGenerator()
        gold = {"status": "ok", "flow_direction": "outflow"}
        btc = {"status": "ok", "direction": "strong_inflow"}
        with patch.object(gen.gold_fetcher, "fetch_daily_change", return_value=gold):
            with patch.object(gen.btc_fetcher, "fetch_flow_signal", return_value=btc):
                signals = gen._cross_asset_signals()
                assert len(signals) == 1
                assert "风险偏好" in signals[0].name
                assert signals[0].direction == SignalDirection.BEARISH

    def test_generate_signals_combined(self) -> None:
        gen = EtfFlowSignalGenerator()
        gold = {
            "status": "ok",
            "flow_direction": "inflow",
            "avg_nav_change_pct": 2.0,
            "total_volume": 6_000_000,
            "total_turnover": 50_000_000,
        }
        btc = {
            "status": "ok",
            "direction": "strong_outflow",
            "score": -0.8,
            "avg_change_pct": -2.5,
            "volume_surge_etfs": 4,
        }
        with patch.object(gen.gold_fetcher, "fetch_daily_change", return_value=gold):
            with patch.object(gen.btc_fetcher, "fetch_flow_signal", return_value=btc):
                signals = gen.generate_signals()
                names = [s.name for s in signals]
                assert "黄金ETF资金流入" in names
                assert "黄金ETF成交放量" in names
                assert "BTC ETF大幅流出(避险↑)" in names
                assert "金银背离: 黄金↑BTC↓ (强烈避险)" in names
