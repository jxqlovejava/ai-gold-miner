"""投资军规 — 15条不可协商的硬约束."""
from __future__ import annotations

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
# r016-r029 补全（操作/信息/心理/趋势/建仓/估值纪律）
# ------------------------------------------------------------------

RULE_ADJUST_BEFORE_DATA = InvestmentRule(
    id="r016",
    name="数据前提前调整",
    description="重大数据（非农/CPI/FOMC）公布前1-2天完成仓位调整，不赌数据方向",
    severity="warn",
    category="operations",
    check_fn="check_pre_data_adjustment",
)

RULE_CONDITIONAL_ORDERS = InvestmentRule(
    id="r017",
    name="条件单代替盯盘",
    description="用条件单代替盯盘手动下单，提前挂好避免盘中情绪干扰",
    severity="warn",
    category="operations",
    check_fn="check_conditional_orders",
)

RULE_REDUCE_ON_RALLY = InvestmentRule(
    id="r018",
    name="减仓趁反弹",
    description="减仓时趁反弹出，不追求卖在最高点——出不出比多卖几块重要100倍",
    severity="warn",
    category="operations",
    check_fn="check_reduce_on_rally",
)

RULE_CONSECUTIVE_VOLATILITY = InvestmentRule(
    id="r019",
    name="连续高波动暂停",
    description="连续两日单日波动>3%时，次日不操作，等待波动收敛",
    severity="warn",
    category="operations",
    check_fn="check_consecutive_high_volatility",
)

RULE_ETF_FLOW_PRIORITY = InvestmentRule(
    id="r020",
    name="ETF资金流向优先",
    description="ETF主力资金流向（当日）比CFTC报告（滞后8天）更及时，短期信号优先参考ETF资金流",
    severity="info",
    category="info_discipline",
    check_fn="check_etf_flow_priority",
)

RULE_RETAIL_BUY_INSTITUTIONAL_SELL = InvestmentRule(
    id="r021",
    name="散户抄底机构出货",
    description="散户抄底+机构出货的反弹不可持续，避免接飞刀",
    severity="warn",
    category="signal_discipline",
    check_fn="check_retail_buy_institutional_sell",
)

RULE_LOSS_DECISION_QUALITY = InvestmentRule(
    id="r022",
    name="浮亏决策质量下降",
    description="浮亏超10%后决策质量骤降，提前动作不要等",
    severity="warn",
    category="psychology",
    check_fn="check_loss_decision_quality",
)

RULE_EMPTY_PERSPECTIVE = InvestmentRule(
    id="r023",
    name="空仓视角检验",
    description="每笔操作前问：如果空仓，会在这个价格买入吗？不会就减",
    severity="warn",
    category="psychology",
    check_fn="check_empty_perspective",
)

RULE_SMART_MONEY_FLOW = InvestmentRule(
    id="r024",
    name="聪明钱与散户流向",
    description="买卖前先看机构/聪明钱/散户资金流向。短期上涨+散户抄底+机构出货=接飞刀",
    severity="warn",
    category="signal_discipline",
    check_fn="check_smart_money_flow",
)

RULE_ATR_TRAILING_STOP = InvestmentRule(
    id="r025",
    name="ATR移动止盈",
    description="日线14×ATR×2.5，从阶段高点回撤触发后减仓一半；成本价保护下止损不低于成本价",
    severity="block",
    category="trend",
    check_fn="check_atr_trailing_stop",
)

RULE_MA_TREND_FILTER = InvestmentRule(
    id="r026",
    name="均线趋势过滤",
    description="200日均线仅作长期过滤，不单独作为买卖信号；需60日均线+基本面/资金流向至少一个维度确认",
    severity="warn",
    category="trend",
    check_fn="check_ma_trend_filter",
)

RULE_GOLD_REBALANCE = InvestmentRule(
    id="r027",
    name="黄金仓位再平衡",
    description="黄金占总资产>55%预警，>60%时7个交易日内减仓至50%以下",
    severity="warn",
    category="position_sizing",
    check_fn="check_gold_rebalance",
)

RULE_STAGGERED_ENTRY = InvestmentRule(
    id="r028",
    name="分批建仓/加仓",
    description="新建仓或加仓须分>=2批，第二批最早5个交易日后执行，每批不超过计划量的50%",
    severity="warn",
    category="entry",
    check_fn="check_staggered_entry",
)

RULE_VALUATION_MARGIN = InvestmentRule(
    id="r029",
    name="安全边际加仓",
    description="加仓前须给出估值区间（DXY/实际利率/央行购金/金银比等多维度），当前价须处于估值区间下沿或回调支撑位；突破新高当日不追涨加仓",
    severity="warn",
    category="entry",
    check_fn="check_valuation_margin",
)

RULE_MARGIN_OF_SAFETY = InvestmentRule(
    id="r030",
    name="永远给自己留安全边际",
    description="任何决策必须明确安全边际（估值缓冲/仓位缓冲/止损保护/现金储备至少一项），禁止在没有犯错空间的位置下注",
    severity="warn",
    category="process",
    check_fn="check_margin_of_safety",
)

RULE_KELLY_POSITION = InvestmentRule(
    id="r031",
    name="凯利仓位参考",
    description="使用1/4 Kelly公式量化仓位上限，信号弱时自动缩仓，避免过度自信下注",
    severity="warn",
    category="position_sizing",
    check_fn="check_kelly_position",
)

RULE_FRICTION_COST = InvestmentRule(
    id="r032",
    name="摩擦成本核算",
    description="卖出/止盈/条件单决策必须按扣除卖出手续费后的净收益计算；净保本价=成本价÷(1-卖出费率)，费率见 portfolio.yaml sell_fee_pct",
    severity="warn",
    category="process",
    check_fn="check_friction_cost",
)

RULE_DATA_LANDING_TREND = InvestmentRule(
    id="r033",
    name="数据落地≠趋势确认",
    description="重大数据（CPI/PPI/非农/FOMC）公布后结果温和/符合预期≠趋势确认：市场常已预先定价。数据温和落地后 3 天(72h)内禁止任何加仓动作（绝对时间盒，趋势确认不构成豁免）；3 天后加仓仍须独立趋势确认（关键点突破回踩/均线多头/资金流同向）",
    severity="warn",
    category="entry",
    check_fn="check_data_landing_trend",
)

RULE_MILD_DATA_OSCILLATION_REDUCE = InvestmentRule(
    id="r034",
    name="数据温和期震荡止盈",
    description="重大数据温和落地后常现多空博弈震荡（冲高乏力+回落有撑）：满足「温和数据落地+高位震荡+聪明钱流出+已有浮盈」时，机动池主动部分止盈（≥1/3）、核心池最多减1/4，不等ATR破位才动作。与r033互补（r033管'温和不追买'，r034管'温和震荡不死扛'）",
    severity="warn",
    category="operations",
    check_fn="check_data_landing_reduce",
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
    RULE_ADJUST_BEFORE_DATA,
    RULE_CONDITIONAL_ORDERS,
    RULE_REDUCE_ON_RALLY,
    RULE_CONSECUTIVE_VOLATILITY,
    RULE_ETF_FLOW_PRIORITY,
    RULE_RETAIL_BUY_INSTITUTIONAL_SELL,
    RULE_LOSS_DECISION_QUALITY,
    RULE_EMPTY_PERSPECTIVE,
    RULE_SMART_MONEY_FLOW,
    RULE_ATR_TRAILING_STOP,
    RULE_MA_TREND_FILTER,
    RULE_GOLD_REBALANCE,
    RULE_STAGGERED_ENTRY,
    RULE_VALUATION_MARGIN,
    RULE_MARGIN_OF_SAFETY,
    RULE_KELLY_POSITION,
    RULE_FRICTION_COST,
    RULE_DATA_LANDING_TREND,
    RULE_MILD_DATA_OSCILLATION_REDUCE,
]


def get_rule_by_id(rule_id: str) -> InvestmentRule | None:
    for r in ALL_RULES:
        if r.id == rule_id:
            return r
    return None
