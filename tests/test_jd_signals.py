"""jdgold P3 信号生成器测试 (mock jdgold_client fetch)."""
from __future__ import annotations

from gold_miner.signals.base import SignalDirection, SignalStrength
from gold_miner.signals.jd_blogger_sentiment_signal import JdBloggerSentimentSignalGenerator
from gold_miner.signals.jd_fund_bomb_signal import JdFundBombSignalGenerator


# ── 资金炸弹信号 ────────────────────────────────────────────────

def _bomb(long_r: float, short_r: float, vol: float = 5.18e8) -> dict:
    return {
        "route": "bomb_latest",
        "items": [{
            "longPositionRatio": long_r, "shortPositionRatio": short_r,
            "tradingVolume": vol, "title": "触发资金炸弹",
        }],
    }


def test_bomb_bullish_when_long_dominant(monkeypatch):
    monkeypatch.setattr(
        "gold_miner.signals.jd_fund_bomb_signal.fetch_bomb",
        lambda *a, **k: _bomb(60.0, 40.0),
    )
    sigs = JdFundBombSignalGenerator().generate_signals()
    assert len(sigs) == 1
    assert sigs[0].direction == SignalDirection.BULLISH
    assert sigs[0].score > 0
    assert sigs[0].metadata["source"] == "jd_fund_bomb"
    assert sigs[0].metadata["source_tier"] == "T1"


def test_bomb_bearish_when_short_dominant(monkeypatch):
    monkeypatch.setattr(
        "gold_miner.signals.jd_fund_bomb_signal.fetch_bomb",
        lambda *a, **k: _bomb(35.0, 65.0),
    )
    sigs = JdFundBombSignalGenerator().generate_signals()
    assert sigs[0].direction == SignalDirection.BEARISH
    assert sigs[0].score < 0


def test_bomb_neutral_near_50_50(monkeypatch):
    monkeypatch.setattr(
        "gold_miner.signals.jd_fund_bomb_signal.fetch_bomb",
        lambda *a, **k: _bomb(49.2, 50.8),
    )
    sigs = JdFundBombSignalGenerator().generate_signals()
    assert sigs[0].direction == SignalDirection.NEUTRAL


def test_bomb_no_data_returns_empty(monkeypatch):
    monkeypatch.setattr(
        "gold_miner.signals.jd_fund_bomb_signal.fetch_bomb", lambda *a, **k: None
    )
    assert JdFundBombSignalGenerator().generate_signals() == []


# ── 大V加仓榜信号 ───────────────────────────────────────────────

def _blogger(items: list[dict]) -> dict:
    return {"rankMode": "buy", "rankings": [
        {"rankMode": "buy", "items": items},
    ]}


def test_blogger_bullish_when_all_buying(monkeypatch):
    monkeypatch.setattr(
        "gold_miner.signals.jd_blogger_sentiment_signal.fetch_blogger_trend",
        lambda *a, **k: _blogger([
            {"latestTrade": "5分钟前买入30g(957元/g)"},
            {"latestTrade": "1小时前加仓10g(955元/g)"},
        ]),
    )
    sigs = JdBloggerSentimentSignalGenerator().generate_signals()
    assert len(sigs) == 1
    assert sigs[0].direction == SignalDirection.BULLISH
    assert sigs[0].score > 0
    assert sigs[0].metadata["source"] == "jd_blogger_rank"


def test_blogger_bearish_when_selling(monkeypatch):
    monkeypatch.setattr(
        "gold_miner.signals.jd_blogger_sentiment_signal.fetch_blogger_trend",
        lambda *a, **k: _blogger([
            {"latestTrade": "10分钟前卖出50g(955元/g)"},
            {"latestTrade": "1小时前减仓20g(952元/g)"},
        ]),
    )
    sigs = JdBloggerSentimentSignalGenerator().generate_signals()
    assert sigs[0].direction == SignalDirection.BEARISH


def test_blogger_no_data_returns_empty(monkeypatch):
    monkeypatch.setattr(
        "gold_miner.signals.jd_blogger_sentiment_signal.fetch_blogger_trend", lambda *a, **k: None
    )
    assert JdBloggerSentimentSignalGenerator().generate_signals() == []
