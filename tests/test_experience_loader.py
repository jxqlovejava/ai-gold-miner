"""Experience loader tests."""

from __future__ import annotations

from pathlib import Path

from gold_miner.experience.loader import ExperienceLoader


def test_load_relevant_with_no_learnings(tmp_path: Path):
    """没有 learning 文件时返回空列表."""
    loader = ExperienceLoader(tmp_path)
    reminders = loader.load_relevant({"active_dimensions": ["technical"]})
    assert reminders == []


def test_load_relevant_matches_keywords(tmp_path: Path):
    """根据关键词匹配相关 learning."""
    learning = tmp_path / "2026-06-16-test.md"
    learning.write_text(
        "# Test\n\n## 学到的规则\n- 遇到 FOMC 前不要重仓\n- 新闻 API 失败时要披露\n",
        encoding="utf-8",
    )
    loader = ExperienceLoader(tmp_path)
    reminders = loader.load_relevant(
        {"active_dimensions": ["新闻"], "direction": "neutral"},
        max_items=3,
    )
    assert len(reminders) >= 1
    assert "新闻 API 失败时要披露" in reminders[0]
