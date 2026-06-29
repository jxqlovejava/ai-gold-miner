"""Report command tests."""

from __future__ import annotations

import re

from gold_miner.cli.report import _FALLBACK_NEWS_ITEMS


def test_fallback_news_items_do_not_contain_specific_prices():
    """兜底新闻的标题和摘要都不应包含具体金价，避免被误认为真实行情."""
    price_pattern = re.compile(
        r"\d+(?:\.\d+)?\s*[元¥]\s*/\s*克|"
        r"\d+(?:\.\d+)?\s*元\s*每\s*克|"
        r"每\s*克\s*\d+(?:\.\d+)?\s*[元¥]?|"
        r"¥\s*\d+(?:\.\d+)?\s*/\s*克|"
        r"\d+(?:\.\d+)?\s*元/克"
    )
    for item in _FALLBACK_NEWS_ITEMS:
        for field, text in (("title", item.title), ("summary", item.summary)):
            assert not price_pattern.search(text), (
                f"fallback {field} contains specific price: {text!r}"
            )
