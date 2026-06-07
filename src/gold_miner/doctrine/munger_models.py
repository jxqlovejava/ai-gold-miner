"""Munger 多元思维模型库 — 从 mungermodels.com 爬取的 232 个模型.

其中 114 个被标记为与黄金投资相关，作为 doctrine 模块的扩展参考.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MungerModel:
    """芒格思维模型 — 轻量数据类."""

    slug: str
    name_cn: str
    name_en: str
    description: str
    discipline: str
    discipline_slug: str
    url: str
    gold_applicable: bool = False
    gold_relevance_reason: str = ""


def _load_models() -> tuple[list[MungerModel], list[MungerModel]]:
    """加载全部模型和黄金相关模型."""
    data_dir = Path(__file__).parents[3] / "data"

    all_models: list[MungerModel] = []
    gold_models: list[MungerModel] = []

    # Load full dataset
    full_path = data_dir / "munger_232_models.json"
    if full_path.exists():
        with open(full_path, encoding="utf-8") as f:
            raw = json.load(f)
        for m in raw.get("models", []):
            all_models.append(
                MungerModel(
                    slug=m["slug"],
                    name_cn=m["name_cn"],
                    name_en=m["name_en"],
                    description=m.get("description", ""),
                    discipline=m["discipline"],
                    discipline_slug=m["discipline_slug"],
                    url=m["url"],
                )
            )

    # Load gold-relevant subset
    gold_path = data_dir / "munger_gold_models.json"
    if gold_path.exists():
        with open(gold_path, encoding="utf-8") as f:
            raw = json.load(f)
        for m in raw.get("models", []):
            gold_models.append(
                MungerModel(
                    slug=m["slug"],
                    name_cn=m["name_cn"],
                    name_en=m["name_en"],
                    description=m.get("description", ""),
                    discipline=m["discipline"],
                    discipline_slug=m["discipline_slug"],
                    url=m["url"],
                    gold_applicable=m.get("gold_applicable", False),
                    gold_relevance_reason=m.get("gold_relevance_reason", ""),
                )
            )

    return all_models, gold_models


ALL_MODELS, GOLD_MODELS = _load_models()


def get_by_slug(slug: str) -> MungerModel | None:
    """按 slug 查找模型."""
    for m in ALL_MODELS:
        if m.slug == slug:
            return m
    return None


def get_by_discipline(discipline_slug: str) -> list[MungerModel]:
    """按学科筛选模型."""
    return [m for m in ALL_MODELS if m.discipline_slug == discipline_slug]


def search(query: str) -> list[MungerModel]:
    """关键词搜索模型."""
    q = query.lower()
    return [
        m
        for m in ALL_MODELS
        if q in m.name_cn.lower()
        or q in m.name_en.lower()
        or q in m.description.lower()
        or q in m.discipline.lower()
    ]


def list_disciplines() -> dict[str, int]:
    """返回各学科模型数量."""
    from collections import Counter

    return dict(Counter(m.discipline for m in ALL_MODELS))
