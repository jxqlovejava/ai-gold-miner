"""分级低吸高抛建议器测试 (V9 成本管理原则)."""
from __future__ import annotations

from gold_miner.strategy.low_buy_high_sell import LowBuyHighSellAdvisor, LowBuyHighSellSignal


def _default_pools() -> dict[str, int]:
    """V9 三池配置 (40/20/20)."""
    return {"core": 40, "tactical": 20, "opportunity": 20}


def test_default_hold_no_signals():
    """无信号时: 核心池持有, 机动池持有, 高抛=持有, 低吸=等待回调."""
    advisor = LowBuyHighSellAdvisor()
    sig = advisor.evaluate(current_price=900.0, pools=_default_pools())

    assert isinstance(sig, LowBuyHighSellSignal)
    assert sig.core_pool["action"] == "持有"
    assert sig.tactical_pool["action"] == "持有"
    assert sig.opportunity_pool["action"] == "S 协议待命"
    assert sig.high_sell_suggestion == "持有"
    assert sig.low_buy_suggestion == "等待回调低吸 (核心池/机动池)"
    assert sig.triggered_signals == []


def test_core_pool_high_sell_disabled_by_default():
    """核心池默认 high_sell=False: ATR 触发也不减仓 (只机动池减)."""
    advisor = LowBuyHighSellAdvisor()
    sig = advisor.evaluate(
        current_price=900.0,
        pools=_default_pools(),
        atr_trailing_triggered=True,
        atr_trailing_price=890.0,
    )

    assert sig.core_pool["action"] == "持有"  # 核心池不动
    assert "core_atr_trailing" not in sig.triggered_signals


def test_core_pool_atr_reduces_when_enabled():
    """核心池启用 high_sell=True + ATR 触发 → 减半."""
    advisor = LowBuyHighSellAdvisor(config={
        "core_pool": {"low_buy": True, "high_sell": True},
        "tactical_pool": {"low_buy": True, "high_sell": True},
        "opportunity_pool": {"low_buy": False, "high_sell": False},
        "high_sell_signals": {
            "atr_trailing": True, "rebalance": True,
            "extreme_sentiment": True, "core_fundamental_break": True,
        },
        "low_buy_iron_rules": {"max_single_pct": 5, "gate_smart_money": True, "no_manual_t": True},
    })
    sig = advisor.evaluate(
        current_price=880.0,
        pools=_default_pools(),
        atr_trailing_triggered=True,
        atr_trailing_price=890.0,
    )

    assert "ATR 移动止盈减半" in sig.core_pool["action"]
    assert "r025" in sig.rule_ids


def test_tactical_rebalance_high_sell():
    """机动池超配+浮盈>20% → 波段高抛 (r020)."""
    advisor = LowBuyHighSellAdvisor()
    sig = advisor.evaluate(
        current_price=900.0,
        pools=_default_pools(),
        rebalance_overweight=True,
        pool_deviation_pp={"tactical": 15},
        pool_profit_pct={"tactical": 25},
    )

    assert sig.tactical_pool["action"] == "波段高抛"
    assert "tactical_rebalance" in sig.triggered_signals
    assert sig.high_sell_suggestion == "减仓 (信号触发)"


def test_tactical_extreme_sentiment():
    """RSI>80 + COT 转流出 → 机动池高抛 (情绪纪律; 不误引 r030 安全边际号)."""
    advisor = LowBuyHighSellAdvisor()
    sig = advisor.evaluate(
        current_price=950.0,
        pools=_default_pools(),
        rsi_value=82.0,
        cot_net_position_change=-5.0,
    )

    assert sig.tactical_pool["action"] == "波段高抛"
    assert "tactical_extreme_sentiment" in sig.triggered_signals
    assert "r030" not in sig.rule_ids  # r030 实为「安全边际」, 情绪高抛不标此号 (2026-09-04)


def test_rsi_high_but_cot_inflow_no_high_sell():
    """RSI>80 但 COT 转流入 → 不触发情绪高抛 (需双信号)."""
    advisor = LowBuyHighSellAdvisor()
    sig = advisor.evaluate(
        current_price=950.0,
        pools=_default_pools(),
        rsi_value=82.0,
        cot_net_position_change=3.0,
    )

    assert sig.tactical_pool["action"] == "持有"
    assert "tactical_extreme_sentiment" not in sig.triggered_signals


def test_smart_money_gate_closes_low_buy():
    """COT 转流出 → 低吸禁用 (MK4 闸门)."""
    advisor = LowBuyHighSellAdvisor()
    sig = advisor.evaluate(
        current_price=900.0,
        pools=_default_pools(),
        cot_net_position_change=-8.0,
    )

    assert sig.low_buy_suggestion == "禁用 (MK4 闸门)"
    assert any("闸门关闭" in w for w in sig.warnings)
    assert "r020" in sig.rule_ids


def test_core_fundamental_break_reduces():
    """央行购金转弱 → 核心池基本面逆转减仓 (当高抛启用时)."""
    advisor = LowBuyHighSellAdvisor(config={
        "core_pool": {"low_buy": True, "high_sell": True},
        "tactical_pool": {"low_buy": True, "high_sell": True},
        "opportunity_pool": {"low_buy": False, "high_sell": False},
        "high_sell_signals": {
            "atr_trailing": True, "rebalance": True,
            "extreme_sentiment": True, "core_fundamental_break": True,
        },
        "low_buy_iron_rules": {"max_single_pct": 5, "gate_smart_money": True, "no_manual_t": True},
    })
    sig = advisor.evaluate(
        current_price=900.0,
        pools=_default_pools(),
        central_bank_buying_slow=True,
    )

    assert "基本面逆转减仓" in sig.core_pool["action"]
    assert "core_fundamental_break" in sig.triggered_signals


def test_to_dict_roundtrip():
    """to_dict 输出可序列化."""
    advisor = LowBuyHighSellAdvisor()
    sig = advisor.evaluate(current_price=900.0, pools=_default_pools())
    d = sig.to_dict()

    assert d["core_pool"]["pool"] == "core"
    assert d["high_sell_suggestion"] == "持有"
    assert isinstance(d["triggered_signals"], list)


def test_config_key_defense_empty():
    """空配置 → 回退默认值, 不 KeyError."""
    advisor = LowBuyHighSellAdvisor(config={})
    sig = advisor.evaluate(current_price=940.0, pools=_default_pools())
    assert sig.core_pool["pool"] == "core"
    assert sig.tactical_pool["pool"] == "tactical"


def test_config_key_defense_partial():
    """部分配置 (缺 tactical_pool) → 回退默认, 不 KeyError."""
    advisor = LowBuyHighSellAdvisor(config={"core_pool": {"low_buy": True, "high_sell": False}})
    sig = advisor.evaluate(current_price=940.0, pools=_default_pools())
    assert sig.core_pool["action"] == "持有"


def test_low_buy_disabled_when_all_pools_off():
    """所有池 low_buy=False → 低吸禁用 (配置禁止)."""
    advisor = LowBuyHighSellAdvisor(config={
        "core_pool": {"low_buy": False, "high_sell": False},
        "tactical_pool": {"low_buy": False, "high_sell": False},
        "opportunity_pool": {"low_buy": False, "high_sell": False},
        "high_sell_signals": {},
        "low_buy_iron_rules": {},
    })
    sig = advisor.evaluate(current_price=940.0, pools=_default_pools())
    assert sig.low_buy_suggestion == "禁用 (配置禁止低吸)"


def test_current_price_zero_no_crash():
    """current_price 为 0 → 不崩溃 (保留接口兼容)."""
    advisor = LowBuyHighSellAdvisor()
    sig = advisor.evaluate(current_price=0.0, pools=_default_pools())
    assert sig.high_sell_suggestion == "持有"


# ----------------------------------------------------------------------
# r037 仓位感知 stance (2026-09-04): 全部默认值 → 旧行为兼容; 显式传参 → 新逻辑
# ----------------------------------------------------------------------

def test_stance_build_when_low_exposure():
    """现仓 13% ≤ 阶段目标 20%×0.8 → stance=build (建仓优先)."""
    advisor = LowBuyHighSellAdvisor()
    sig = advisor.evaluate(
        current_price=960.0, pools=_default_pools(),
        current_exposure_pct=13, target_exposure_pct=20, max_exposure_pct=80,
    )
    assert sig.stance == "build"
    assert "13" in sig.stance_reason


def test_stance_defend_near_cap():
    """现仓 76% 近上限 80% → stance=defend."""
    advisor = LowBuyHighSellAdvisor()
    sig = advisor.evaluate(
        current_price=960.0, pools=_default_pools(),
        current_exposure_pct=76, target_exposure_pct=20, max_exposure_pct=80,
    )
    assert sig.stance == "defend"


def test_stance_balance_default_when_no_exposure():
    """未传仓位/目标 → stance=balance, 输出与旧版完全一致 (向后兼容)."""
    advisor = LowBuyHighSellAdvisor()
    sig = advisor.evaluate(current_price=960.0, pools=_default_pools())
    assert sig.stance == "balance"
    assert sig.low_buy_suggestion == "等待回调低吸 (核心池/机动池)"
    assert sig.low_buy_bands == []


def test_build_trigger_when_price_in_low_band():
    """build + 现价进入低吸带 → 输出可执行「低吸触发」档位 (r037)."""
    advisor = LowBuyHighSellAdvisor()
    bands = [
        {"price": 925, "grams": 10, "existing": False},
        {"price": 878, "grams": 15, "existing": True},
    ]
    sig = advisor.evaluate(
        current_price=920.0, pools=_default_pools(),
        current_exposure_pct=13, target_exposure_pct=20, max_exposure_pct=80,
        price_in_low_band=True, low_band_suggestions=bands,
    )
    assert sig.stance == "build"
    assert "低吸触发" in sig.low_buy_suggestion
    assert [b["price"] for b in sig.low_buy_bands] == [925, 878]
    assert any("r037" in w or "低吸触发" in w for w in sig.warnings)


def test_smart_money_gate_degrades_in_build_when_cot_out_only():
    """build + 仅 COT 转出 (无综合流佐证) → 闸门放行 (r037 降级), 不标 r020."""
    advisor = LowBuyHighSellAdvisor()
    sig = advisor.evaluate(
        current_price=920.0, pools=_default_pools(),
        current_exposure_pct=13, target_exposure_pct=20, max_exposure_pct=80,
        cot_net_position_change=-8.0,
    )
    assert sig.stance == "build"
    assert "禁用" not in sig.low_buy_suggestion
    assert "r020" not in sig.rule_ids


def test_smart_money_gate_still_closes_in_build_with_outflow():
    """build + COT 转出 + 综合流出 → 仍关闸防接飞刀."""
    advisor = LowBuyHighSellAdvisor()
    sig = advisor.evaluate(
        current_price=920.0, pools=_default_pools(),
        current_exposure_pct=13, target_exposure_pct=20, max_exposure_pct=80,
        cot_net_position_change=-8.0, smart_money_flow="outflow",
    )
    assert sig.low_buy_suggestion == "禁用 (MK4 闸门)"
    assert "r020" in sig.rule_ids


def test_gate_strict_in_balance_backward_compat():
    """balance (旧路径) + COT 转出 → 保持禁用, 与既有 test_smart_money_gate_closes_low_buy 一致."""
    advisor = LowBuyHighSellAdvisor()
    sig = advisor.evaluate(
        current_price=920.0, pools=_default_pools(),
        cot_net_position_change=-8.0,
    )
    assert sig.low_buy_suggestion == "禁用 (MK4 闸门)"


def test_divergence_cot_inflow_gld_outflow_keeps_gate_open_in_build():
    """9/2 型: build + COT 仍在吸(转流入)但 GLD/综合背离 → 闸门放行, 不误伤低位低吸."""
    advisor = LowBuyHighSellAdvisor()
    sig = advisor.evaluate(
        current_price=920.0, pools=_default_pools(),
        current_exposure_pct=13, target_exposure_pct=20, max_exposure_pct=80,
        cot_net_position_change=3.0, smart_money_flow="divergence",
    )
    assert sig.low_buy_suggestion != "禁用 (MK4 闸门)"


def test_build_cooldown_overrides_trigger_when_dense_buys():
    """build + 到带 + 操作节奏冷却(近窗口密集连买) → 转「低吸冷却」非触发."""
    advisor = LowBuyHighSellAdvisor()
    sig = advisor.evaluate(
        current_price=920.0, pools=_default_pools(),
        current_exposure_pct=13, target_exposure_pct=20, max_exposure_pct=80,
        price_in_low_band=True,
        low_band_suggestions=[{"price": 925, "grams": 10}],
        low_buy_cooldown=True,
    )
    assert "低吸冷却" in sig.low_buy_suggestion
    assert "低吸触发" not in sig.low_buy_suggestion
    assert any("冷却" in w for w in sig.warnings)


def test_build_trigger_when_no_cooldown_default():
    """默认 low_buy_cooldown=False → 到带仍触发 (向后兼容)."""
    advisor = LowBuyHighSellAdvisor()
    sig = advisor.evaluate(
        current_price=920.0, pools=_default_pools(),
        current_exposure_pct=13, target_exposure_pct=20, max_exposure_pct=80,
        price_in_low_band=True,
        low_band_suggestions=[{"price": 925, "grams": 10}],
    )
    assert "低吸触发" in sig.low_buy_suggestion
