"""Polymarket 数据采集与信号生成测试."""

from datetime import datetime

import pytest

from gold_miner.data.polymarket import (
    NOISE_KEYWORDS,
    PredictionMarket,
    PolymarketFetcher,
)
from gold_miner.signals.base import SignalDirection
from gold_miner.signals.polymarket_signal import (
    PolymarketSignalConfig,
    PolymarketSignalGenerator,
)


class TestPredictionMarket:
    """PredictionMarket dataclass 测试."""

    def test_creation(self) -> None:
        m = PredictionMarket(
            market_id="abc123",
            question="Will Fed cut rates?",
            description="Test",
            outcome_yes_price=0.72,
            outcome_no_price=0.28,
            outcomes=["Yes", "No"],
            volume_24h=5000.0,
            volume_total=100000.0,
            liquidity=2000.0,
            end_date=None,
            slug="test",
            condition_id="abc123",
            updated_at=datetime.now(),
            created_at=datetime.now(),
        )
        assert m.outcome_yes_price == 0.72
        assert m.matched_category == ""


class TestPolymarketFetcher:
    """PolymarketFetcher 单元测试."""

    @pytest.fixture
    def fetcher(self) -> PolymarketFetcher:
        return PolymarketFetcher()

    def test_filter_noise_excludes_entertainment(self, fetcher: PolymarketFetcher) -> None:
        markets = [
            _make_market("Will Rihanna release an album before GTA VI?"),
            _make_market("Will Fed cut rates in June?"),
            _make_market("New Playboi Carti Album before GTA 6?"),
            _make_market("Will inflation exceed 3%?"),
        ]
        result = fetcher._filter_noise(markets)
        questions = [m.question for m in result]
        assert "Will Fed cut rates in June?" in questions
        assert "Will inflation exceed 3%?" in questions
        assert "Rihanna" not in " ".join(questions)
        assert "Playboi Carti" not in " ".join(questions)

    def test_filter_gold_related_macro(self, fetcher: PolymarketFetcher) -> None:
        markets = [
            _make_market("Will Fed cut rates in June?"),
            _make_market("Will inflation exceed 3% this year?"),
            _make_market("Will the Lakers win the NBA championship?"),
            _make_market("Will gold price exceed $3000 by year end?"),
        ]
        filtered = fetcher._filter_noise(markets)
        related = fetcher._filter_gold_related(filtered)
        questions = [m.question for m in related]

        assert "Will Fed cut rates in June?" in questions
        assert "Will inflation exceed 3% this year?" in questions
        assert "Will gold price exceed $3000 by year end?" in questions
        assert "Lakers" not in " ".join(questions)

    def test_filter_gold_related_geopolitical(self, fetcher: PolymarketFetcher) -> None:
        markets = [
            _make_market("Will Israel launch a strike on Iran?"),
            _make_market("Will there be a ceasefire in Gaza by July?"),
            _make_market("Will the new iPhone feature AI?"),
        ]
        related = fetcher._filter_gold_related(markets)
        questions = [m.question for m in related]
        assert "Israel" in " ".join(questions)
        assert "ceasefire" in " ".join(questions)
        assert "iPhone" not in " ".join(questions)

    def test_filter_gold_related_currency(self, fetcher: PolymarketFetcher) -> None:
        markets = [
            _make_market("Will DXY index exceed 110 by year end?"),
            _make_market("Will USD weaken against EUR?"),
        ]
        related = fetcher._filter_gold_related(markets)
        assert len(related) == 2
        assert related[0].matched_category == "currency"

    def test_matches_keywords(self, fetcher: PolymarketFetcher) -> None:
        m = _make_market("Will the Federal Reserve cut interest rates?")
        assert fetcher._matches_keywords(m, ["fed", "rate"])
        assert not fetcher._matches_keywords(m, ["bitcoin", "crypto"])


class TestPolymarketSignalGenerator:
    """PolymarketSignalGenerator 测试."""

    @pytest.fixture
    def generator(self) -> PolymarketSignalGenerator:
        return PolymarketSignalGenerator(PolymarketSignalConfig(
            prob_threshold_strong=0.70,
            prob_threshold_moderate=0.55,
        ))

    def test_infer_direction_rate_cut(self, generator: PolymarketSignalGenerator) -> None:
        assert generator._infer_direction("Will Fed cut rates?") == SignalDirection.BULLISH
        assert generator._infer_direction("Will there be a rate cut?") == SignalDirection.BULLISH

    def test_infer_direction_rate_hike(self, generator: PolymarketSignalGenerator) -> None:
        assert generator._infer_direction("Will Fed hike rates?") == SignalDirection.BEARISH
        assert generator._infer_direction("Will there be a rate hike?") == SignalDirection.BEARISH

    def test_infer_direction_inflation(self, generator: PolymarketSignalGenerator) -> None:
        assert generator._infer_direction("Will CPI be above 3%?") == SignalDirection.BULLISH
        assert generator._infer_direction("Will inflation fall below 2%?") == SignalDirection.BEARISH

    def test_infer_direction_war(self, generator: PolymarketSignalGenerator) -> None:
        assert generator._infer_direction("Will war break out?") == SignalDirection.BULLISH
        assert generator._infer_direction("Will peace deal be signed?") == SignalDirection.BEARISH

    def test_infer_direction_dollar(self, generator: PolymarketSignalGenerator) -> None:
        assert generator._infer_direction("Will dollar strengthen?") == SignalDirection.BEARISH
        assert generator._infer_direction("Will USD weaken?") == SignalDirection.BULLISH

    def test_infer_direction_gold(self, generator: PolymarketSignalGenerator) -> None:
        assert generator._infer_direction("Will gold exceed $3000?") == SignalDirection.BULLISH
        assert generator._infer_direction("Will gold fall below $2000?") == SignalDirection.BEARISH

    def test_infer_direction_unknown(self, generator: PolymarketSignalGenerator) -> None:
        assert generator._infer_direction("Will aliens visit Earth?") is None

    def test_market_to_signal_prob_too_low(self, generator: PolymarketSignalGenerator) -> None:
        m = _make_market("Will Fed cut rates?", yes_price=0.40)
        m.matched_category = "macro"
        assert generator._market_to_signal(m) is None

    def test_market_to_signal_bullish(self, generator: PolymarketSignalGenerator) -> None:
        m = _make_market("Will Fed cut rates?", yes_price=0.75, volume_24h=15000)
        m.matched_category = "macro"
        sig = generator._market_to_signal(m)
        assert sig is not None
        assert sig.direction == SignalDirection.BULLISH
        assert sig.score > 0

    def test_market_to_signal_bearish(self, generator: PolymarketSignalGenerator) -> None:
        m = _make_market("Will dollar strengthen?", yes_price=0.65, volume_24h=8000)
        m.matched_category = "currency"
        sig = generator._market_to_signal(m)
        assert sig is not None
        assert sig.direction == SignalDirection.BEARISH
        assert sig.score < 0

    def test_aggregate_by_category(self, generator: PolymarketSignalGenerator) -> None:
        markets = [
            _make_market("Will Fed cut rates?", yes_price=0.72, volume_24h=5000),
            _make_market("Will inflation exceed 3%?", yes_price=0.68, volume_24h=3000),
            _make_market("Will NFP be strong?", yes_price=0.55, volume_24h=2000),
        ]
        for m in markets:
            m.matched_category = "macro"
        signals = generator._aggregate_by_category(markets)
        assert len(signals) >= 1


# ------------------------------------------------------------------
# 集成测试：真实 API 调用（标记为 slow）
# ------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.integration
class TestPolymarketIntegration:
    """真实 API 集成测试."""

    def test_fetch_active_markets(self) -> None:
        fetcher = PolymarketFetcher()
        markets = fetcher._fetch_active_markets(limit=20)
        assert isinstance(markets, list)
        assert len(markets) > 0
        for m in markets:
            assert m.market_id
            assert 0.0 <= m.outcome_yes_price <= 1.0

    def test_fetch_gold_related(self) -> None:
        fetcher = PolymarketFetcher()
        markets = fetcher.fetch_gold_related(limit=100, max_results=20)
        assert isinstance(markets, list)
        # 当前可能没有黄金相关市场，但至少不报错
        for m in markets:
            assert m.matched_category
            assert m.matched_keywords


# ------------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------------

def _make_market(
    question: str,
    yes_price: float = 0.6,
    volume_24h: float = 1000.0,
) -> PredictionMarket:
    return PredictionMarket(
        market_id="test-id",
        question=question,
        description="Test description",
        outcome_yes_price=yes_price,
        outcome_no_price=1.0 - yes_price,
        outcomes=["Yes", "No"],
        volume_24h=volume_24h,
        volume_total=volume_24h * 10,
        liquidity=volume_24h * 2,
        end_date=None,
        slug="test-slug",
        condition_id="test-condition",
        updated_at=datetime.now(),
        created_at=datetime.now(),
    )
