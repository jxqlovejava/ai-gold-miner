"""初请/续请失业金综合方向判定回归测试.

背景 (2026-08-11): 旧逻辑只看"低于预期"→判 bearish, 漏判环比方向。
8/6 初请 19.9万 vs 前值 19.8万: 低于预期 (就业强→鹰派) 但 环比上升
(边际恶化→利多), 两因素对冲, 综合应为 NEUTRAL 而非 bearish。

修复: _infer_claims_direction 综合 预期差 + 环比涨幅 + 分母幻觉。
"""
from __future__ import annotations

from gold_miner.signals.base import SignalDirection
from gold_miner.signals.recent_events import (
    _infer_claims_direction,
    _infer_direction_from_event,
)


class TestInferClaimsDirection:
    def test_below_forecast_but_mom_up_is_neutral(self) -> None:
        """8/6 实际: 低于预期(鹰派) 但 环比上升(边际恶化) → 对冲, NEUTRAL."""
        d = _infer_claims_direction(
            "初请失业金人数", "19.9万", "20.2万", "19.8万",
        )
        assert d is SignalDirection.NEUTRAL

    def test_below_forecast_and_mom_down_is_bearish(self) -> None:
        """低于预期 + 环比下降 = 双重就业强 → bearish."""
        d = _infer_claims_direction("初请", "19.9万", "20.2万", "20.5万")
        assert d is SignalDirection.BEARISH

    def test_above_forecast_and_mom_up_is_bullish(self) -> None:
        """高于预期 + 环比上升 = 双重就业弱 → bullish."""
        d = _infer_claims_direction("初请", "21.0万", "20.2万", "20.5万")
        assert d is SignalDirection.BULLISH

    def test_below_forecast_mom_up_with_denominator_is_bullish(self) -> None:
        """低于预期但环比升 + 参与率下降(分母幻觉) → 明确利多, 不判鹰派."""
        d = _infer_claims_direction(
            "初请", "19.9万 参与率下降", "20.2万", "19.8万",
        )
        assert d is SignalDirection.BULLISH

    def test_non_claims_event_returns_none(self) -> None:
        """非初请事件 → None, 调用方回退通用关键词."""
        d = _infer_claims_direction("美国CPI", "3.2%", "3.1%", "3.0%")
        assert d is None

    def test_no_previous_falls_back_to_forecast_miss(self) -> None:
        """无前值 → 退化为预期差判定."""
        d = _infer_claims_direction("初请", "19.9万", "20.2万", None)
        assert d is SignalDirection.BEARISH

    def test_unparseable_actual_returns_none(self) -> None:
        d = _infer_claims_direction("初请", "待公布", "20.2万", "19.8万")
        assert d is None


class TestClaimsThroughDirectionFromEvent:
    """_infer_direction_from_event 层: claims 优先走综合判定."""

    def test_claims_uses_combined_logic_over_keyword(self) -> None:
        """8/6 场景: '低于预期'命中弱词(旧逻辑误判bullish), 综合判定应 neutral."""
        # 无 gold_bias, 走 keyword fallback → 综合判定
        d, conflict = _infer_direction_from_event(
            "初请失业金人数",
            "实际 19.9万 (预期20.2万, 前值19.8万) 低于预期",
            "20.2万",
            previous="19.8万",
        )
        assert d is SignalDirection.NEUTRAL
        assert conflict is None

    def test_claims_with_explicit_gold_bias_overrides(self) -> None:
        """显式 gold_bias 优先于综合判定."""
        d, conflict = _infer_direction_from_event(
            "初请失业金人数",
            "实际 19.9万 (预期20.2万, 前值19.8万) 低于预期",
            "20.2万",
            previous="19.8万",
            gold_bias="bearish",
        )
        assert d is SignalDirection.BEARISH
        # 综合判定 neutral, 不产生冲突告警
        assert conflict is None
