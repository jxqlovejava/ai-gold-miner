"""测试文章分析日志."""

import tempfile
from pathlib import Path

from gold_miner.intelligence.journal import ArticleJournal, ArticleRecord


def _make_record(id: str = "abc123", source_url: str = "https://example.com/gold") -> ArticleRecord:
    return ArticleRecord(
        id=id,
        source_url=source_url,
        title="测试文章",
        text_preview="这是一篇测试文章...",
        word_count=500,
        sentiment_score=0.35,
        sentiment_direction="bullish",
        manipulation_score=2,
        manipulation_flags=["单一方向"],
        is_suspicious=False,
        claims=[{"category": "货币政策", "claim": "美联储降息", "pattern": "test"}],
    )


class TestArticleJournal:
    def test_save_and_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal = ArticleJournal(data_dir=Path(tmpdir))
            record = _make_record()
            journal.save(record)

            all_records = journal.list_all()
            assert len(all_records) == 1
            assert all_records[0].id == "abc123"

    def test_get_by_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal = ArticleJournal(data_dir=Path(tmpdir))
            journal.save(_make_record(id="r1"))
            journal.save(_make_record(id="r2"))

            r = journal.get("r1")
            assert r is not None
            assert r.id == "r1"

            r = journal.get("nonexistent")
            assert r is None

    def test_update(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal = ArticleJournal(data_dir=Path(tmpdir))
            journal.save(_make_record(id="r1"))

            updated = journal.update(
                "r1",
                llm_analysis={"sentiment": "bearish", "confidence": 0.8},
                status="cross_referenced",
            )
            assert updated is not None
            assert updated.llm_analysis == {"sentiment": "bearish", "confidence": 0.8}
            assert updated.status == "cross_referenced"

            # Verify persistence
            journal2 = ArticleJournal(data_dir=Path(tmpdir))
            r = journal2.get("r1")
            assert r is not None
            assert r.llm_analysis == {"sentiment": "bearish", "confidence": 0.8}

    def test_update_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal = ArticleJournal(data_dir=Path(tmpdir))
            result = journal.update("nonexistent", status="forecasted")
            assert result is None

    def test_list_forecasted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal = ArticleJournal(data_dir=Path(tmpdir))
            journal.save(_make_record(id="r1"))
            journal.save(_make_record(id="r2"))
            journal.update("r1", forecast_direction="bullish", forecast_confidence=0.7, status="forecasted")

            forecasted = journal.list_forecasted()
            assert len(forecasted) == 1
            assert forecasted[0].id == "r1"

    def test_list_unverified(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal = ArticleJournal(data_dir=Path(tmpdir))
            journal.save(_make_record(id="r1"))
            journal.update("r1", forecast_direction="bullish", forecast_confidence=0.7, status="forecasted")

            unverified = journal.list_unverified()
            assert len(unverified) == 1

    def test_empty_journal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal = ArticleJournal(data_dir=Path(tmpdir))
            assert journal.list_all() == []
            assert journal.list_forecasted() == []
