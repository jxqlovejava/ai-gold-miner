"""经验知识加载器 — 从 .learnings/ 提取与当前分析相关的提醒."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger


class ExperienceLoader:
    """加载项目学习笔记并匹配相关经验."""

    def __init__(self, learnings_dir: str | Path = ".learnings") -> None:
        self.learnings_dir = Path(learnings_dir)

    def load_relevant(self, context: dict[str, Any], max_items: int = 3) -> list[str]:
        """根据分析上下文返回最相关的经验提醒."""
        files = sorted(self.learnings_dir.glob("*.md")) if self.learnings_dir.exists() else []
        if not files:
            return []

        keywords = self._extract_keywords(context)
        scored: list[tuple[float, str]] = []

        for f in files:
            content = f.read_text(encoding="utf-8")
            score = self._score(content, keywords)
            if score > 0:
                # 提取与关键词最相关的条目作为摘要
                reminder = self._extract_reminder(content, keywords)
                if reminder:
                    scored.append((score, reminder))

        scored.sort(key=lambda x: x[0], reverse=True)
        selected = [item for _, item in scored[:max_items]]
        logger.debug(f"经验匹配: 找到 {len(selected)} 条相关学习笔记")
        return selected

    def _extract_keywords(self, context: dict[str, Any]) -> set[str]:
        """从上下文中提取关键词."""
        keywords: set[str] = set()

        # 信号维度
        for dim in context.get("active_dimensions", []):
            keywords.add(dim.lower())

        # 决策方向
        direction = context.get("direction", "neutral")
        keywords.add(direction.lower())

        # 信号名称
        bundle = context.get("bundle")
        if bundle is not None:
            for signal in getattr(bundle, "signals", []):
                name = getattr(signal, "name", "")
                dim = getattr(signal, "dimension", "")
                keywords.update(name.lower().split())
                keywords.add(dim.lower())

        # 新闻关键词
        for news in context.get("news_raw", []):
            title = getattr(news, "title", "")
            keywords.update(title.lower().split())

        return keywords

    def _score(self, content: str, keywords: set[str]) -> float:
        """计算内容与关键词的匹配分数."""
        content_lower = content.lower()
        return sum(1 for kw in keywords if kw in content_lower)

    def _extract_reminder(self, content: str, keywords: set[str]) -> str | None:
        """从 markdown 内容中提取与关键词最相关的提醒."""
        # 优先找 ## 学到的规则
        marker = "## 学到的规则"
        idx = content.find(marker)
        if idx == -1:
            marker = "## "
            idx = content.find(marker)
        if idx == -1:
            return None

        section = content[idx:]
        best_score = 0
        best_line: str | None = None

        for line in section.splitlines()[1:]:
            line = line.strip()
            if not line or line.startswith("##"):
                continue
            # 去掉 markdown 列表标记
            if line.startswith(("- ", "* ", "1. ", "2. ", "3. ", "4. ", "5. ")):
                line = line[line.find(" ") + 1:]
            # 粗体标记也去掉
            line = line.replace("**", "")
            if not line:
                continue
            score = sum(1 for kw in keywords if kw in line.lower())
            if score > best_score:
                best_score = score
                best_line = line

        if best_line and len(best_line) > 180:
            best_line = best_line[:177] + "..."
        return best_line
