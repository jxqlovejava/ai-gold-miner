"""三情景目标区间传导链完整性校验测试 (r035).

背景 (2026-08-14 事故):
  报告「看多突破」情景只写「停火破裂→地缘避险→冲965」单层利多, 漏二阶效应
  「油价↑→通胀↑→联储鹰派→实际利率↑→压制金价」, 也未标注时间尺度分化。
  本测试确保 validate_scenario_transmissions 能拦截这类单层传导。
"""

from __future__ import annotations

from gold_miner.scenarios.price_target import (
    PriceTargetScenario,
    build_price_target_matrix,
    validate_scenario_transmissions,
)


def _scenario(**kwargs) -> PriceTargetScenario:
    """构造默认情景, 便于按需覆盖字段."""
    defaults = dict(
        name="测试情景",
        direction="bullish",
        probability_pct=30,
        gold_low=950.0,
        gold_high=965.0,
        xauusd_low=4380.0,
        xauusd_high=4450.0,
        trigger_conditions="停火破裂→地缘避险→金价冲高",
        transmission_channels=["利多: 地缘避险溢价冲高"],
        falsification="",
        reasoning="",
    )
    defaults.update(kwargs)
    return PriceTargetScenario(**defaults)


class TestValidateScenarioTransmissions:
    def test_single_layer_transmission_warns(self):
        """单层传导 (只有利多, 无二阶, 无时间尺度) → 警告二阶 + 时间尺度."""
        warnings = validate_scenario_transmissions(
            [_scenario(name="看多突破")]
        )
        assert len(warnings) >= 2
        joined = "; ".join(warnings)
        assert "缺二阶传导" in joined
        assert "缺时间尺度" in joined

    def test_geopolitical_keyword_detection(self):
        """油价/霍尔木兹/美伊等关键词都应触发强制检查."""
        for kw in ["停火", "霍尔木兹", "油价", "美伊", "Hormuz", "oil"]:
            s = _scenario(trigger_conditions=f"{kw} 相关事件")
            warnings = validate_scenario_transmissions([s])
            assert warnings, f"关键词 {kw} 应触发二阶传导检查"

    def test_complete_transmission_no_warning(self):
        """利多 + 二阶利空 + 时间尺度 → 无警告."""
        s = _scenario(
            name="看多突破(先冲后落)",
            transmission_channels=[
                "利多: 地缘避险溢价 → 短期冲高 (short-term)",
                "利空: 油价→通胀→联储鹰派→实际利率↑ → 中期回落 (medium-term)",
            ],
        )
        assert validate_scenario_transmissions([s]) == []

    def test_neutral_scenario_not_flagged(self):
        """中性情景 (非方向性) → 不检查, 无警告."""
        s = _scenario(
            direction="neutral",
            trigger_conditions="数据温和+聪明钱流出",
            transmission_channels=["中性: 多空博弈震荡"],
        )
        assert validate_scenario_transmissions([s]) == []

    def test_non_geopolitical_not_flagged(self):
        """非地缘/油价触发 (纯利率驱动) → 不强制检查."""
        s = _scenario(
            trigger_conditions="实际利率上行+美元走强",
            transmission_channels=["利空: 实际利率上行压制"],
        )
        assert validate_scenario_transmissions([s]) == []

    def test_second_order_keyword_alone_counts_as_bearish(self):
        """传导链含油价→通胀→联储关键词 → 视为已评估二阶 (不报缺二阶)."""
        s = _scenario(
            transmission_channels=[
                "利多: 避险 (short-term)",
                "利空: 油价→通胀→联储→利率压制 (medium-term)",
            ]
        )
        warnings = validate_scenario_transmissions([s])
        assert not any("缺二阶传导" in w for w in warnings)


class TestBuildPriceTargetMatrix:
    def test_matrix_structure(self):
        """矩阵输出结构完整: 每情景含全部字段 + 区间推算正确."""
        matrix = build_price_target_matrix(
            current_price=950.0,
            atr=7.7,
            base_xauusd=4384.0,
            scenarios=[
                {
                    "name": "看多突破(先冲后落)",
                    "direction": "bullish",
                    "probability_pct": 20,
                    "gold_delta_pct_low": 0.5,
                    "gold_delta_pct_high": 1.5,
                    "trigger_conditions": "停火破裂→地缘避险→冲高",
                    "transmission_channels": [
                        "利多: 避险 → 短期冲高 (short-term)",
                        "利空: 油价→通胀→联储→利率 → 中期回落 (medium-term)",
                    ],
                },
                {
                    "name": "高位震荡",
                    "direction": "neutral",
                    "probability_pct": 50,
                    "gold_delta_pct_low": -2.0,
                    "gold_delta_pct_high": 0.5,
                    "trigger_conditions": "停火拖延+数据温和",
                    "transmission_channels": ["中性: 多空博弈"],
                },
                {
                    "name": "回调修正",
                    "direction": "bearish",
                    "probability_pct": 30,
                    "gold_delta_pct_low": -5.0,
                    "gold_delta_pct_high": -2.0,
                    "trigger_conditions": "停火续约→溢价回吐",
                    "transmission_channels": [
                        "利空: 溢价回吐 (short-term)",
                        "利多: 央行购金承接 (medium-term)",
                    ],
                },
            ],
        )

        assert len(matrix) == 3
        # 概率合计 = 100
        assert sum(s.probability_pct for s in matrix) == 100
        # 区间基于现价推导: 看多 low 应 > 现价, 回调 high 应 < 现价
        bull = matrix[0]
        assert bull.gold_low >= 950.0
        bear = matrix[2]
        assert bear.gold_high <= 950.0
        # 传导链已填充
        assert all(s.transmission_channels for s in matrix)
        # 校验通过 (传导链完整)
        assert validate_scenario_transmissions(matrix) == []

    def test_zero_price_returns_empty(self):
        assert build_price_target_matrix(
            current_price=0, atr=0, base_xauusd=0, scenarios=[{}]
        ) == []

    def test_low_high_ordering(self):
        """gold_delta 区间边界自动排序, low<=high."""
        matrix = build_price_target_matrix(
            current_price=1000.0,
            atr=10.0,
            base_xauusd=4600.0,
            scenarios=[
                {
                    "name": "情景",
                    "direction": "neutral",
                    "probability_pct": 100,
                    "gold_delta_pct_low": 2.0,   # 故意颠倒: low 值 > high 值
                    "gold_delta_pct_high": -1.0,
                    "trigger_conditions": "测试",
                    "transmission_channels": [],
                }
            ],
        )
        assert matrix[0].gold_low <= matrix[0].gold_high
