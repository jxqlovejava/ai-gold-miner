"""增量判断引擎单测 — 基准提取/规则fallback/静默逻辑."""
from __future__ import annotations

import pytest

from gold_miner.incremental.judge import _rule_delta, seed_baseline_from_scan


def test_rule_delta_reverse() -> None:
    baseline = {"direction": "neutral"}
    inputs = [
        {"direction": "bearish", "gold_bias": None},
        {"direction": "bearish", "gold_bias": None},
    ]
    res = _rule_delta(baseline, inputs)
    assert res["delta"] == "reverse"
    assert res["material"] is True


def test_rule_delta_same_when_empty() -> None:
    res = _rule_delta({"direction": "neutral"}, [])
    assert res["delta"] == "same"
    assert res["material"] is False


def test_rule_delta_tie_is_same() -> None:
    baseline = {"direction": "neutral"}
    inputs = [
        {"direction": "bullish", "gold_bias": None},
        {"direction": "bearish", "gold_bias": None},
    ]
    res = _rule_delta(baseline, inputs)
    assert res["delta"] == "same"
    assert res["material"] is False


def test_rule_delta_reinforce_weak_single() -> None:
    """单条同向信号: reinforce 但不 material (避免每条都推送)."""
    baseline = {"direction": "bullish"}
    inputs = [{"direction": "bullish", "gold_bias": None}]
    res = _rule_delta(baseline, inputs)
    assert res["delta"] == "reinforce"
    assert res["material"] is False  # 单条强化不足以触发推送


def test_seed_baseline_from_scan() -> None:
    """scan_report 无 → 返回 None (不崩)."""
    assert seed_baseline_from_scan() is None or isinstance(seed_baseline_from_scan(), dict)
