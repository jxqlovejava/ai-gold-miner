"""投资策略模板 — 8种适用于不同市场状态的策略."""

from gold_miner.doctrine.models import InvestmentStrategy

STRATEGY_DCA = InvestmentStrategy(
    id="s001",
    name="定投策略 (Dollar Cost Averaging)",
    description="固定周期以固定金额买入，无视短期价格波动，用时间平滑成本。适合长期看涨但短期不确定的市场。",
    applicable_regime="all",
    position_sizing="每月投入总资产的2-5%，不因价格变化调整金额",
    entry_rules=["每月固定日期（如1日或15日）定额买入", "价格大幅下跌时不追加也不减少，维持原计划"],
    exit_rules=["达到长期目标（如累计收益30%+）时分批退出", "基本面发生根本性变化时停止定投"],
    stop_loss_rule="不设止损，靠时间分散风险",
    mental_models=["长期复利", "安全边际"],
    pros=["无需择时，执行简单", "平滑波动，降低情绪干扰", "长期坚持胜率高"],
    cons=["单边下跌时浮亏时间可能较长", "单边上涨时建仓成本逐月上升", "牛市收益率低于一次性投入"],
)

STRATEGY_GRID = InvestmentStrategy(
    id="s002",
    name="网格交易 (Grid Trading)",
    description="预设价格上下限，将区间分成多个网格。价格下跌到网格线买入，上涨到网格线卖出。适合震荡市。",
    applicable_regime="ranging",
    position_sizing="总资金分成10-15份，每格一份",
    entry_rules=["在支撑位上方设置买入网格", "网格间距为近期波动区间的3-5%", "突破区间上沿暂停买入"],
    exit_rules=["价格触及网格上沿全部卖出", "突破区间下沿暂停卖出，等待确认"],
    stop_loss_rule="跌破区间下沿3%止损",
    mental_models=["均值回归", "安全边际"],
    pros=["震荡市收益稳定", "自动化程度高，情绪干扰小", "不需要判断方向"],
    cons=["单边趋势市中可能持续浮亏", "占用资金大", "突破行情中会错过大趋势"],
)

STRATEGY_TREND = InvestmentStrategy(
    id="s003",
    name="趋势跟踪 (Trend Following)",
    description="不预测方向，只跟随趋势。金叉做多、死叉做空/离场。核心假设：趋势会持续。适合趋势明显的市场。",
    applicable_regime="trending",
    position_sizing="初次建仓15-20%，趋势确认后加至30-40%",
    entry_rules=["MA20上穿MA60金叉确认", "ADX>25确认趋势强度", "突破前高/前低后顺势入场"],
    exit_rules=["MA20下穿MA60死叉离场", "ADX<20趋势减弱减仓", "移动止盈跟随MA20"],
    stop_loss_rule="入场K线最低价下方1%或ATR×2",
    mental_models=["趋势跟踪", "周期思维"],
    pros=["大趋势中收益极高", "不预测，减少主观判断", "风险收益比好"],
    cons=["震荡市中频繁止损", "信号滞后，可能错过初期行情", "假突破可能导致追高"],
)

STRATEGY_SCALING = InvestmentStrategy(
    id="s004",
    name="分批建仓 (Scaling In/Out)",
    description="将目标仓位分3-4次入场，金字塔式（价格越低买的越多）。退出时分批止盈。适合底部区域和回调买入。",
    applicable_regime="recovery",
    position_sizing="分4批：30%→30%→25%→15%（价格越低权重越小）或 25%→25%→25%→25%",
    entry_rules=["第一批在估值合理区间入场", "每下跌3-5%加一批", "跌破关键支撑暂停加仓"],
    exit_rules=["每上涨5-8%减一批", "保留底仓追踪长期趋势"],
    stop_loss_rule="总仓位跌破成本价5%止损",
    mental_models=["安全边际", "非对称机会", "均值回归"],
    pros=["降低择时压力", "平均成本优于一次性建仓", "容错率高"],
    cons=["需要较多现金储备", "持续上涨中仓位不足", "需要纪律执行"],
)

STRATEGY_BREAKOUT = InvestmentStrategy(
    id="s005",
    name="突破交易 (Breakout Trading)",
    description="等待价格突破关键阻力/支撑位后确认入场。核心逻辑：关键位突破后往往有惯性行情。适合关键位附近盘整后突破的市场。",
    applicable_regime="trending",
    position_sizing="突破确认后建仓20-25%，回踩确认加至35%",
    entry_rules=["收盘价突破关键位（前高/前低/整数关口）", "突破时成交量放大确认", "突破后回踩不破前位入场"],
    exit_rules=["反向突破止损离场", "达到量度目标（突破幅度×1.5-2）止盈"],
    stop_loss_rule="突破位下方1-2%",
    mental_models=["趋势跟踪", "非对称机会"],
    pros=["方向明确后入场风险低", "盈亏比好（1:2以上）", "假突破可快速止损"],
    cons=["假突破导致频繁小亏", "盘整期无信号", "需要等待确认，可能错过急涨"],
)

STRATEGY_CRISIS_HEDGE = InvestmentStrategy(
    id="s006",
    name="危机对冲 (Crisis Hedge)",
    description="将黄金作为资产组合的压舱石和危机保险。不追求交易收益，而是对冲系统性风险。适合任何时候作为底仓配置。",
    applicable_regime="crisis",
    position_sizing="总资产15-25%配置实物黄金/黄金ETF，不交易只持有",
    entry_rules=["任何时点均可建仓，不择时", "市场恐慌时（VIX>30）可适度加仓至25%"],
    exit_rules=["仅在需要流动性时减持", "基本面发生结构性变化时调整", "不因短期涨跌操作"],
    stop_loss_rule="不设止损，这是保险而非交易",
    mental_models=["非对称机会", "安全边际", "周期思维"],
    pros=["危机中有巨大超额收益", "与股票/债券低相关", "简单无需频繁操作"],
    cons=["长期持有有机会成本", "非危机期可能跑输其他资产", "需要足够的耐心和信念"],
)

STRATEGY_MEAN_REVERSION = InvestmentStrategy(
    id="s007",
    name="均值回归 (Mean Reversion)",
    description="假设价格会在极端值后向均值回归。RSI超卖买入、超买卖出。适合区间震荡市场。",
    applicable_regime="ranging",
    position_sizing="每笔5-10%，最多同时持有3笔",
    entry_rules=["RSI(14)<30且价格触及布林带下轨", "RSI(14)>70且价格触及布林带上轨做空/减仓"],
    exit_rules=["RSI回到40-60区间", "价格回到布林带中轨", "持仓不超过5个交易日"],
    stop_loss_rule="入场后反向超2%止损",
    mental_models=["均值回归", "逆向思维"],
    pros=["震荡市胜率高", "信号明确易执行", "持仓时间短"],
    cons=["趋势市中连续亏损", "需要严格止损", "不适合极端行情"],
)

STRATEGY_ANTIFRAGILE = InvestmentStrategy(
    id="s008",
    name="反脆弱配置 (Antifragile)",
    description="用小额损失换取非线性收益。配置少量资金于深度虚值期权或极端尾部事件对冲工具。大部分时间小亏，黑天鹅中大赚。",
    applicable_regime="all",
    position_sizing="总资产2-5%用于尾部对冲（黄金看涨期权/波动率产品/极端情景配置）",
    entry_rules=["持续小额配置，不择时", "VIX低位时（<15）加大期权配置（便宜）", "重大不确定性事件前适度加仓"],
    exit_rules=["黑天鹅事件发生后逐步兑现利润", "期权到期日前1个月滚动"],
    stop_loss_rule="期权到期归零或策略年度预算耗尽即止",
    mental_models=["非对称机会", "逆向思维", "周期思维"],
    pros=["黑天鹅事件中收益巨大", "下行有限上行无限", "保护整体组合"],
    cons=["大部分时间在亏小钱", "需要承受持续小损失的心理压力", "期权有时间衰减"],
)


ALL_STRATEGIES: list[InvestmentStrategy] = [
    STRATEGY_DCA,
    STRATEGY_GRID,
    STRATEGY_TREND,
    STRATEGY_SCALING,
    STRATEGY_BREAKOUT,
    STRATEGY_CRISIS_HEDGE,
    STRATEGY_MEAN_REVERSION,
    STRATEGY_ANTIFRAGILE,
]


def get_strategy_by_id(strategy_id: str) -> InvestmentStrategy | None:
    for s in ALL_STRATEGIES:
        if s.id == strategy_id:
            return s
    return None
