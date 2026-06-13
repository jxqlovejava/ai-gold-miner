"""聪明钱补充模块测试 — 13F / 投行目标价 / COMEX大户."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

from gold_miner.data.comex_large_traders import ComexLargeTraderFetcher
from gold_miner.data.institutional_13f import (
    Institutional13FFetcher,
    InstitutionPosition,
    InstitutionalSummary,
)
from gold_miner.data.investment_bank_targets import (
    InvestmentBankTargetFetcher,
    PriceTarget,
)
from gold_miner.signals.institutional_signal import InstitutionalSignalGenerator
from gold_miner.signals.base import SignalDirection, SignalStrength


# =============================================================================
# 13F Institutional Tests
# =============================================================================

class TestInstitutional13FFetcher:
    def test_fallback_summary(self):
        fetcher = Institutional13FFetcher()
        summary = fetcher._fallback_summary()
        assert summary is not None
        assert summary.total_institutions == 7
        assert summary.net_gold_bullish >= 0
        assert summary.net_gold_bearish >= 0
        assert summary.gold_etf_total_shares > 0

    def test_fetch_latest_quarter_fallback(self):
        fetcher = Institutional13FFetcher()
        summary = fetcher.fetch_latest_quarter()
        assert summary is not None
        assert summary.quarter.startswith("Q")

    def test_get_bullish_score(self):
        fetcher = Institutional13FFetcher()
        score = fetcher.get_bullish_score()
        assert -1.0 <= score <= 1.0

    def test_current_quarter(self):
        q = Institutional13FFetcher._current_quarter()
        assert q.startswith("Q")
        assert len(q.split()) == 2


class TestInstitutionPosition:
    def test_properties(self):
        pos = InstitutionPosition(
            institution="Bridgewater",
            ticker="GLD",
            shares=1000000,
            value_usd=190000000,
            quarter="Q1 2026",
            position_change_pct=0.15,
        )
        assert pos.is_new is False
        assert pos.is_closed is False

    def test_closed_position(self):
        pos = InstitutionPosition(
            institution="Soros",
            ticker="GLD",
            shares=0,
            value_usd=0,
            quarter="Q1 2026",
            position_change_pct=-1.0,
            is_closed=True,
        )
        assert pos.is_closed is True


# =============================================================================
# Investment Bank Target Tests
# =============================================================================

class TestPriceTarget:
    def test_upside_pct(self):
        target = PriceTarget("Goldman", 3600, 3300, "Buy")
        assert target.upside_pct == pytest.approx(9.09, abs=0.1)
        assert target.is_bullish is True
        assert target.is_bearish is False

    def test_bearish_target(self):
        target = PriceTarget("Bank", 3000, 3300, "Sell")
        assert target.is_bullish is False
        assert target.is_bearish is True

    def test_neutral_target(self):
        target = PriceTarget("Bank", 3400, 3300, "Hold")
        assert target.is_bullish is False
        assert target.is_bearish is False


class TestInvestmentBankTargetFetcher:
    def test_fetch_consensus(self):
        fetcher = InvestmentBankTargetFetcher()
        consensus = fetcher.fetch_consensus(current_spot=3300)
        assert consensus["status"] == "ok"
        assert consensus["total_banks"] >= 5
        assert consensus["avg_target"] > 0
        assert consensus["median_target"] > 0
        assert "highest" in consensus
        assert "lowest" in consensus

    def test_fetch_consensus_empty(self):
        fetcher = InvestmentBankTargetFetcher()
        with patch.object(fetcher, "fetch_all_targets", return_value=[]):
            result = fetcher.fetch_consensus()
            assert result["status"] == "no_data"

    def test_get_bullish_score(self):
        fetcher = InvestmentBankTargetFetcher()
        score = fetcher.get_bullish_score(3300)
        assert -1.0 <= score <= 1.0


# =============================================================================
# COMEX Large Trader Tests
# =============================================================================

class TestComexLargeTraderFetcher:
    def test_fetch_concentration_summary(self):
        fetcher = ComexLargeTraderFetcher()
        result = fetcher.fetch_concentration_summary()
        assert result["status"] == "ok"
        assert "long4_concentration_pct" in result
        assert "short4_concentration_pct" in result
        assert "long_dominance" in result
        assert "crowded_short" in result
        assert "squeeze_risk" in result

    def test_fallback_data(self):
        fetcher = ComexLargeTraderFetcher()
        df = fetcher._fallback_data()
        assert not df.empty
        assert len(df) == 12
        assert "timestamp" in df.columns


class TestLargeTraderData:
    def test_properties(self):
        from gold_miner.data.comex_large_traders import LargeTraderData
        data = LargeTraderData(
            report_date=datetime.now(),
            long4_concentration=40.0,
            long8_concentration=55.0,
            short4_concentration=35.0,
            short8_concentration=50.0,
            net_long4_pct=5.0,
            net_long8_pct=5.0,
        )
        assert data.long_dominance == 5.0
        assert data.is_crowded_short is False
        assert data.is_crowded_long is False

    def test_crowded_short(self):
        from gold_miner.data.comex_large_traders import LargeTraderData
        data = LargeTraderData(
            report_date=datetime.now(),
            long4_concentration=35.0,
            long8_concentration=50.0,
            short4_concentration=48.0,
            short8_concentration=60.0,
            net_long4_pct=-13.0,
            net_long8_pct=-10.0,
        )
        assert data.is_crowded_short is True
        assert data.is_crowded_long is False


# =============================================================================
# Institutional Signal Generator Tests
# =============================================================================

class TestInstitutionalSignalGenerator:
    def test_bank_target_bullish_signal(self):
        gen = InstitutionalSignalGenerator(current_spot=3300)
        consensus = {
            "status": "ok",
            "upside_pct": 10.0,
            "bullish_count": 6,
            "bearish_count": 1,
            "neutral_count": 0,
            "total_banks": 7,
            "avg_target": 3650,
            "highest_target": 4000,
            "lowest_target": 3400,
        }
        with patch.object(gen.bank_fetcher, "fetch_consensus", return_value=consensus):
            signals = gen._bank_target_signals()
            assert len(signals) >= 1
            assert any("投行共识强烈看涨" in s.name for s in signals)
            assert any(s.direction == SignalDirection.BULLISH for s in signals)

    def test_bank_target_dispersion_warning(self):
        gen = InstitutionalSignalGenerator(current_spot=3300)
        consensus = {
            "status": "ok",
            "upside_pct": 5.0,
            "bullish_count": 4,
            "bearish_count": 0,
            "neutral_count": 3,
            "total_banks": 7,
            "avg_target": 3500,
            "highest_target": 5000,
            "lowest_target": 3000,
        }
        with patch.object(gen.bank_fetcher, "fetch_consensus", return_value=consensus):
            signals = gen._bank_target_signals()
            assert any("分歧大" in s.name for s in signals)

    def test_large_trader_squeeze_risk(self):
        gen = InstitutionalSignalGenerator(current_spot=3300)
        summary = {
            "status": "ok",
            "long4_concentration_pct": 35.0,
            "short4_concentration_pct": 48.0,
            "long_dominance": -13.0,
            "crowded_short": True,
            "crowded_long": False,
            "squeeze_risk": True,
        }
        with patch.object(gen.trader_fetcher, "fetch_concentration_summary", return_value=summary):
            signals = gen._large_trader_signals()
            assert any("逼空" in s.name for s in signals)
            squeeze = [s for s in signals if "逼空" in s.name][0]
            assert squeeze.direction == SignalDirection.BULLISH
            assert squeeze.strength == SignalStrength.STRONG

    def test_large_trader_crowded_long_warning(self):
        gen = InstitutionalSignalGenerator(current_spot=3300)
        summary = {
            "status": "ok",
            "long4_concentration_pct": 48.0,
            "short4_concentration_pct": 30.0,
            "long_dominance": 18.0,
            "crowded_short": False,
            "crowded_long": True,
            "squeeze_risk": False,
        }
        with patch.object(gen.trader_fetcher, "fetch_concentration_summary", return_value=summary):
            signals = gen._large_trader_signals()
            assert any("多头拥挤" in s.name for s in signals)
            warning = [s for s in signals if "多头拥挤" in s.name][0]
            assert warning.direction == SignalDirection.BEARISH

    def test_13f_bullish_signal(self):
        gen = InstitutionalSignalGenerator(current_spot=3300)
        inst_summary = InstitutionalSummary(
            quarter="Q2 2026",
            total_institutions=7,
            net_gold_bullish=5,
            net_gold_bearish=2,
            top_buyers=[InstitutionPosition("Bridgewater Associates", "GLD", 5000000, 950000000, "Q2 2026", 0.15)],
        )
        with patch.object(gen.inst_13f_fetcher, "fetch_latest_quarter", return_value=inst_summary):
            signals = gen._institutional_13f_signals()
            assert any("大举增持" in s.name for s in signals)
            assert any("Bridgewater" in s.name for s in signals)

    def test_composite_signal(self):
        gen = InstitutionalSignalGenerator(current_spot=3300)
        signals = gen._composite_smart_money_signal()
        # 回退数据应产生某种综合信号
        assert isinstance(signals, list)

    def test_generate_signals_combined(self):
        gen = InstitutionalSignalGenerator(current_spot=3300)
        signals = gen.generate_signals()
        assert isinstance(signals, list)
        # 至少应有某些信号产生
        names = [s.name for s in signals]
        assert len(names) > 0
