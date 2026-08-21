"""ETF资金流模块单元测试."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from gold_miner.data.etf_flow import (
    BtcEtfFlowFetcher,
    EtfFlowRecord,
    GoldEtfFlowFetcher,
    IntlGoldEtfFlowFetcher,
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


class TestIntlGoldHoldingsFlow:
    """GLD 持仓(吨)是国际黄金ETF真实资金流的主信号."""

    def _holdings_df(self, prev: float, latest: float) -> pd.DataFrame:
        dates = [datetime(2026, 7, 1) + timedelta(days=i) for i in range(2)]
        return pd.DataFrame({
            "timestamp": dates,
            "value": [prev, latest],
            "nav_per_share": [370.0, 371.0],
            "shares_volume": [5_000_000.0, 5_100_000.0],
        })

    def test_holdings_outflow(self) -> None:
        fetcher = IntlGoldEtfFlowFetcher()
        # 1007.08 → 1005.08 ≈ -0.199% → outflow
        df = self._holdings_df(1007.08, 1005.08)
        result = fetcher.fetch_holdings_flow(holdings_df=df)
        assert result["status"] == "ok"
        assert result["flow_direction"] == "outflow"
        assert result["flow_score"] < 0
        assert result["tonnes_delta"] == pytest.approx(-2.0, abs=0.01)
        assert result["source_tier"] == "T0"
        assert result["source"] == "gld_holdings_tonnes"

    def test_holdings_strong_inflow(self) -> None:
        fetcher = IntlGoldEtfFlowFetcher()
        # +0.5% → strong_inflow
        prev = 1000.0
        latest = 1005.0
        df = self._holdings_df(prev, latest)
        result = fetcher.fetch_holdings_flow(holdings_df=df)
        assert result["status"] == "ok"
        assert result["flow_direction"] == "strong_inflow"
        assert result["flow_score"] > 0
        assert abs(result["flow_score"]) <= 0.8

    def test_holdings_neutral_small_change(self) -> None:
        fetcher = IntlGoldEtfFlowFetcher()
        df = self._holdings_df(1000.0, 1000.2)  # +0.02% < 0.05%
        result = fetcher.fetch_holdings_flow(holdings_df=df)
        assert result["status"] == "ok"
        assert result["flow_direction"] == "neutral"
        assert result["flow_score"] == 0.0

    def test_holdings_insufficient_data(self) -> None:
        fetcher = IntlGoldEtfFlowFetcher()
        df = pd.DataFrame({
            "timestamp": [datetime(2026, 7, 1)],
            "value": [1000.0],
        })
        result = fetcher.fetch_holdings_flow(holdings_df=df)
        assert result["status"] == "no_data"

    def test_flow_summary_uses_holdings_not_price(self) -> None:
        """fetch_flow_summary 主方向来自持仓，不把价格跌当流出."""
        fetcher = IntlGoldEtfFlowFetcher()
        holdings = {
            "status": "ok",
            "as_of": "2026-07-02",
            "holdings_tonnes": 1005.08,
            "prev_holdings_tonnes": 1007.08,
            "tonnes_delta": -2.0,
            "holdings_change_pct": -0.1986,
            "flow_direction": "outflow",
            "flow_score": -0.4,
            "source": "gld_holdings_tonnes",
            "source_tier": "T0",
        }
        with patch.object(fetcher, "fetch_holdings_flow", return_value=holdings):
            summary = fetcher.fetch_flow_summary()
        assert summary["status"] == "ok"
        assert summary["flow_direction"] == "outflow"
        assert summary["source"] == "gld_holdings_tonnes"
        assert summary["source_tier"] == "T0"
        # yfinance volume proxy 已移除 (2026-08-21), proxy 字段保持默认值
        assert summary["gld_change_pct"] == 0.0


class TestEtfFlowSignalGenerator:
    def test_gold_price_proxy_bullish(self) -> None:
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
                assert signals[0].name == "国内黄金ETF价格变动(proxy)"
                assert signals[0].direction == SignalDirection.BULLISH
                assert signals[0].strength == SignalStrength.WEAK
                assert abs(signals[0].score) <= 0.3
                assert "proxy" in signals[0].description
                assert signals[0].metadata.get("is_real_flow") is False

    def test_gold_price_proxy_bearish(self) -> None:
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
                assert signals[0].name == "国内黄金ETF价格变动(proxy)"
                assert signals[0].direction == SignalDirection.BEARISH
                assert abs(signals[0].score) <= 0.3

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
                assert "proxy" in vol_signals[0].name

    def test_intl_holdings_outflow_signal(self) -> None:
        """国际信号描述必须是持仓吨变化，不得写 'GLD跌-2%'."""
        gen = EtfFlowSignalGenerator()
        summary = {
            "status": "ok",
            "flow_direction": "outflow",
            "flow_score": -0.4,
            "tonnes_delta": -2.0,
            "holdings_change_pct": -0.1986,
            "holdings_tonnes": 1005.08,
            "gld_volume_ratio": 1.1,
            "gld_change_pct": -2.0,  # price drop must NOT drive primary text
            "volume_surge_count": 0,
            "source_tier": "T0",
        }
        with patch.object(gen.intl_fetcher, "fetch_flow_summary", return_value=summary):
            with patch.object(
                gen.gold_fetcher,
                "fetch_daily_change",
                return_value={"status": "no_data"},
            ):
                signals = gen._intl_gold_etf_signals()
        assert len(signals) == 1
        s = signals[0]
        assert s.name == "国际黄金ETF资金流出"
        assert s.direction == SignalDirection.BEARISH
        assert "GLD持仓(吨)变化" in s.description
        assert "GLD跌" not in s.description
        assert s.metadata.get("source_tier") == "T0"
        assert s.metadata.get("is_real_flow") is True

    def test_intl_holdings_strong_inflow_signal(self) -> None:
        gen = EtfFlowSignalGenerator()
        summary = {
            "status": "ok",
            "flow_direction": "strong_inflow",
            "flow_score": 0.7,
            "tonnes_delta": 5.0,
            "holdings_change_pct": 0.5,
            "holdings_tonnes": 1010.0,
            "gld_volume_ratio": 1.0,
            "gld_change_pct": 0.1,
            "volume_surge_count": 0,
            "source_tier": "T0",
        }
        with patch.object(gen.intl_fetcher, "fetch_flow_summary", return_value=summary):
            with patch.object(
                gen.gold_fetcher,
                "fetch_daily_change",
                return_value={"status": "no_data"},
            ):
                signals = gen._intl_gold_etf_signals()
        assert len(signals) == 1
        assert signals[0].name == "国际黄金ETF大幅流入"
        assert signals[0].strength == SignalStrength.STRONG
        assert "GLD持仓(吨)变化" in signals[0].description

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
        intl = {
            "status": "ok",
            "flow_direction": "inflow",
            "flow_score": 0.3,
            "tonnes_delta": 1.0,
            "holdings_change_pct": 0.1,
            "holdings_tonnes": 1006.0,
            "gld_volume_ratio": 1.0,
            "gld_change_pct": 0.5,
            "volume_surge_count": 0,
            "source_tier": "T0",
        }
        with patch.object(gen.gold_fetcher, "fetch_daily_change", return_value=gold):
            with patch.object(gen.intl_fetcher, "fetch_flow_summary", return_value=intl):
                signals = gen.generate_signals()
                names = [s.name for s in signals]
                assert "国内黄金ETF价格变动(proxy)" in names
                assert "国内黄金ETF成交放量(proxy)" in names
                assert "国际黄金ETF资金流入" in names
                # btc_etf/cross_etf 维度 2026-08-21 禁用, 不再生成 BTC/金银背离信号
