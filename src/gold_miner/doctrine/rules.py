"""投资军规 — 15条不可协商的硬约束."""

from gold_miner.doctrine.models import InvestmentRule

# ------------------------------------------------------------------
# 仓位管理 (position_sizing)
# ------------------------------------------------------------------

RULE_SINGLE_POSITION_LIMIT = InvestmentRule(
    id="r001",
    name="单笔仓位上限",
    description="单笔交易不超过总资产20%，避免单次错误造成毁灭性损失",
    severity="block",
    category="position_sizing",
    check_fn="check_position_limit",
)

RULE_TOTAL_EXPOSURE = InvestmentRule(
    id="r002",
    name="总敞口上限",
    description="所有黄金相关头寸合计不超过总资产80%，保留20%现金应对极端波动",
    severity="block",
    category="position_sizing",
    check_fn="check_total_exposure",
)

RULE_GOLD_OVERWEIGHT = InvestmentRule(
    id="r003",
    name="黄金过重提示",
    description="黄金占比超过总资产50%时提示过度集中风险",
    severity="warn",
    category="position_sizing",
    check_fn="check_gold_overweight",
)

# ------------------------------------------------------------------
# 时机选择 (timing)
# ------------------------------------------------------------------

RULE_NO_HEAVY_BEFORE_DATA = InvestmentRule(
    id="r004",
    name="数据前不重仓",
    description="重大经济数据发布（非农/CPI/FOMC）前2小时内不新建重仓（>10%）",
    severity="warn",
    category="timing",
    check_fn="check_pre_data_heavy",
)

RULE_NO_CHASE = InvestmentRule(
    id="r005",
    name="不追涨杀跌",
    description="单日波动超3%时不追涨杀跌，等待回调或反弹后再操作",
    severity="block",
    category="timing",
    check_fn="check_no_chase",
)

RULE_FRIDAY_REDUCE = InvestmentRule(
    id="r006",
    name="周五减仓",
    description="周五收盘前考虑降低隔夜风险敞口至50%以下，避免周末黑天鹅",
    severity="warn",
    category="timing",
    check_fn="check_friday_exposure",
)

RULE_HOLIDAY_REDUCE = InvestmentRule(
    id="r007",
    name="长假减仓",
    description="长假（春节/国庆/圣诞）前降低风险敞口，避免长假期间不可控风险",
    severity="warn",
    category="timing",
    check_fn="check_holiday_exposure",
)

# ------------------------------------------------------------------
# 情绪纪律 (emotion)
# ------------------------------------------------------------------

RULE_CONSECUTIVE_STOP = InvestmentRule(
    id="r008",
    name="连续止损休整",
    description="连续3次止损后强制休整至少3个交易日，避免情绪化追损",
    severity="block",
    category="emotion",
    check_fn="check_consecutive_stops",
)

RULE_EXTREME_SENTIMENT = InvestmentRule(
    id="r009",
    name="情绪极端时暂停",
    description="市场情绪极端时（VIX>40或恐惧贪婪指数>90/<10）暂缓新开仓决策",
    severity="warn",
    category="emotion",
    check_fn="check_extreme_sentiment",
)

RULE_TRAILING_STOP_PROFIT = InvestmentRule(
    id="r010",
    name="盈利必须上移止损",
    description="浮盈超20%时必须将止损上移至成本价以上，锁定利润",
    severity="block",
    category="emotion",
    check_fn="check_trailing_stop",
)

RULE_ONE_SIDE_SIGNALS = InvestmentRule(
    id="r011",
    name="警惕一边倒信号",
    description="单一方向信号占比超80%时警惕反转，不盲目加仓",
    severity="warn",
    category="emotion",
    check_fn="check_one_sided_signals",
)

# ------------------------------------------------------------------
# 流程纪律 (process)
# ------------------------------------------------------------------

RULE_MULTI_DIMENSION = InvestmentRule(
    id="r012",
    name="多维度确认",
    description="交易决策必须基于至少2个维度（技术/基本面/消息/情绪）的信号一致性",
    severity="warn",
    category="process",
    check_fn="check_multi_dimension",
)

RULE_CONFLICT_CAUTIOUS = InvestmentRule(
    id="r013",
    name="分歧过大观望",
    description="多空双方置信度均>60%时优先观望，等方向明朗后再操作",
    severity="warn",
    category="process",
    check_fn="check_conflict_cautious",
)

RULE_MUST_SET_STOP = InvestmentRule(
    id="r014",
    name="必须设止损",
    description="任何交易必须预设止损位，无止损不开仓",
    severity="block",
    category="process",
    check_fn="check_stop_loss_set",
)

RULE_DECISION_RECORD = InvestmentRule(
    id="r015",
    name="书面决策记录",
    description="每次交易决策必须有书面记录，包含理由、预期、止损、复盘节点",
    severity="info",
    category="process",
    check_fn="check_decision_record",
)


# ------------------------------------------------------------------
# 全部规则集合
# ------------------------------------------------------------------

ALL_RULES: list[InvestmentRule] = [
    RULE_SINGLE_POSITION_LIMIT,
    RULE_TOTAL_EXPOSURE,
    RULE_GOLD_OVERWEIGHT,
    RULE_NO_HEAVY_BEFORE_DATA,
    RULE_NO_CHASE,
    RULE_FRIDAY_REDUCE,
    RULE_HOLIDAY_REDUCE,
    RULE_CONSECUTIVE_STOP,
    RULE_EXTREME_SENTIMENT,
    RULE_TRAILING_STOP_PROFIT,
    RULE_ONE_SIDE_SIGNALS,
    RULE_MULTI_DIMENSION,
    RULE_CONFLICT_CAUTIOUS,
    RULE_MUST_SET_STOP,
    RULE_DECISION_RECORD,
]


def get_rule_by_id(rule_id: str) -> InvestmentRule | None:
    for r in ALL_RULES:
        if r.id == rule_id:
            return r
    return None
