"""情景预案结构化触发条件测试 (关键价+时间窗+证伪点+动作)."""

from __future__ import annotations

from gold_miner.pipeline.long_term_result import LongTermAnalysisResult
from gold_miner.strategy.scenario_triggers import (
    build_scenario_triggers,
    conditional_order_suggestions_from_triggers,
)


class TestBuildScenarioTriggers:
    def test_long_direction_pair_triggers(self):
        """多头方向 → 上行确认 + 上行证伪 对偶触发点."""
        triggers = build_scenario_triggers(
            direction="long", position_pct=0.15, current_spot=4371
        )
        assert len(triggers) == 2

        up = next(t for t in triggers if t.direction == "up")
        down = next(t for t in triggers if t.direction == "down")

        # 上行确认价在现价上方, 证伪点在现价下方
        assert up.key_price > 4371
        assert down.key_price < 4371
        assert up.time_window == "20小时"
        assert "证伪" in up.falsification
        assert "不破" in up.trigger_condition
        assert up.implied_action  # 动作非空

    def test_short_direction_actions(self):
        triggers = build_scenario_triggers(
            direction="short", position_pct=0.2, current_spot=3000
        )
        up = next(t for t in triggers if t.direction == "up")
        assert "减仓" in up.implied_action

    def test_zero_spot_returns_empty(self):
        assert build_scenario_triggers(
            direction="long", position_pct=0.1, current_spot=0
        ) == []


class TestConditionalOrderSuggestions:
    def test_suggestions_from_triggers(self):
        triggers = build_scenario_triggers(
            direction="long", position_pct=0.1, current_spot=3000
        )
        sugs = conditional_order_suggestions_from_triggers(triggers)
        assert len(sugs) == 2
        # 上行 → 限价买建议; 证伪 → 止损/减仓建议
        assert sugs[0]["type"] == "limit_buy"
        assert sugs[0]["direction"] == "buy"
        assert sugs[1]["type"] == "stop_loss"
        assert sugs[1]["direction"] == "reduce"

    def test_empty_triggers(self):
        assert conditional_order_suggestions_from_triggers([]) == []


class TestReportSerialization:
    def test_to_report_dict_includes_triggers(self):
        r = LongTermAnalysisResult(current_spot=3000)
        r.scenario_triggers = build_scenario_triggers(
            direction="long", position_pct=0.1, current_spot=3000
        )
        r.conditional_order_suggestions = conditional_order_suggestions_from_triggers(
            r.scenario_triggers
        )
        d = r.to_report_dict()
        assert len(d["scenario_triggers"]) == 2
        assert d["scenario_triggers"][0]["key_price"] > 3000
        assert "trigger_condition" in d["scenario_triggers"][0]
        assert "falsification" in d["scenario_triggers"][0]
        assert len(d["conditional_order_suggestions"]) == 2
