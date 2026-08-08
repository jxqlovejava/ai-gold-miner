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
    """RSI>80 + COT 转流出 → 机动池高抛 (r030)."""
    advisor = LowBuyHighSellAdvisor()
    sig = advisor.evaluate(
        current_price=950.0,
        pools=_default_pools(),
        rsi_value=82.0,
        cot_net_position_change=-5.0,
    )

    assert sig.tactical_pool["action"] == "波段高抛"
    assert "tactical_extreme_sentiment" in sig.triggered_signals
    assert "r030" in sig.rule_ids


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
