"""投资思维模型 — 10个经典投资思维框架."""

from gold_miner.doctrine.models import MentalModel

MODEL_CONTRARIAN = MentalModel(
    id="m001",
    name="逆向思维 (Contrarian Thinking)",
    description="当大多数人看法一致时，往往已经反映在价格里。真正的机会在共识的对面。巴菲特：'别人贪婪时我恐惧，别人恐惧时我贪婪。'",
    key_principle="市场极端情绪是反向指标。当散户/媒体一致性看多时减仓，一致恐慌时加仓。",
    when_to_apply="市场情绪处于极端位置（恐惧贪婪指数>85或<15），或媒体/分析师观点高度一致时",
    gold_application="当'黄金永远涨'成为街头话题时警惕回调；当没人谈论黄金时开始关注",
    reference="Warren Buffett, David Dreman",
    related_strategies=["s007", "s008"],
)

MODEL_TREND = MentalModel(
    id="m002",
    name="趋势跟踪 (Trend Following)",
    description="价格以趋势方式运动，趋势一旦形成倾向于持续。不要预测拐点，要跟随趋势直到它反转。",
    key_principle="'趋势是你的朋友'。确认趋势后顺势而为，不逆势抄底或摸顶。",
    when_to_apply="市场呈现明确的上升或下降趋势（ADX>25，均线多头/空头排列）",
    gold_application="金价在MA20上方运行不做空，在MA20下方运行不做多。等待趋势确认而非预判转折。",
    reference="Richard Donchian, Ed Seykota, Turtle Traders",
    related_strategies=["s003", "s005"],
)

MODEL_MEAN_REVERSION = MentalModel(
    id="m003",
    name="均值回归 (Mean Reversion)",
    description="价格短期会偏离均值，但长期倾向于回归。极端偏离往往是交易机会而非趋势信号。",
    key_principle="价格偏离均值的幅度越大，回归的概率越高。但需要区分'偏离'和'趋势改变'。",
    when_to_apply="RSI进入超买/超卖区域（>70/<30），或价格偏离MA60超过2个标准差",
    gold_application="金价短期暴涨远离60日均线时考虑减仓；暴跌远离时考虑分批买入",
    reference="John Bogle, Jeremy Siegel",
    related_strategies=["s002", "s007"],
)

MODEL_CYCLE = MentalModel(
    id="m004",
    name="周期思维 (Cycle Thinking)",
    description="经济和市场有周期性：复苏→过热→滞胀→衰退→复苏。理解当前所处周期位置，比预测短期涨跌更重要。",
    key_principle="在周期底部布局、在周期顶部收获。最大的风险是在错误周期位置做错误方向。",
    when_to_apply="评估宏观经济周期阶段（通过PMI、利率、就业等指标综合判断）",
    gold_application="滞胀期和衰退初期是黄金最佳表现期；经济强劲增长期黄金相对弱势",
    reference="Howard Marks, Ray Dalio",
    related_strategies=["s003", "s006"],
)

MODEL_ASYMMETRIC = MentalModel(
    id="m005",
    name="非对称机会 (Asymmetric Opportunity)",
    description="寻找'下行有限、上行无限'的赌注。Taleb：'不要做大概率赢小钱、小概率亏大钱的事。'",
    key_principle="一笔交易的赔率比胜率更重要。追求1:3以上的风险收益比。",
    when_to_apply="评估任何交易机会时，优先衡量如果判断错误会亏多少 vs 如果判断正确会赚多少",
    gold_application="在关键支撑位附近做多（止损紧贴支撑下方，上行空间大）；在历史高位追多的赔率差",
    reference="Nassim Taleb, Mohnish Pabrai",
    related_strategies=["s004", "s006", "s008"],
)

MODEL_MARGIN_OF_SAFETY = MentalModel(
    id="m006",
    name="安全边际 (Margin of Safety)",
    description="以低于内在价值的价格买入，为判断错误留出缓冲空间。价格越低，安全边际越大。",
    key_principle="不在估值过高时买入，不在泡沫中追高。每笔交易都需要'安全垫'。",
    when_to_apply="评估买入时，考虑最坏情况下会跌到哪，当前价格离那个位置有多远",
    gold_application="参考黄金生产成本（~$1200-1500）作为长期底部锚定。金价越接近成本线，安全边际越大",
    reference="Benjamin Graham, Seth Klarman",
    related_strategies=["s001", "s002", "s004"],
)

MODEL_OCCAM = MentalModel(
    id="m007",
    name="奥卡姆剃刀 (Occam's Razor)",
    description="最简单的解释通常是正确的。投资不需要复杂的模型，核心驱动力只有少数几个。",
    key_principle="如果2个指标就能解释价格变动，不需要20个。简化决策框架，减少噪音。",
    when_to_apply="分析被过多因素淹没时，退回最核心的3个驱动因素（实际利率、美元、避险需求）",
    gold_application="黄金中长期走势80%由实际利率和美元解释。其他因素多为短期噪音。",
    reference="William of Ockham, John Bogle",
    related_strategies=["s003", "s006"],
)

MODEL_REFLEXIVITY = MentalModel(
    id="m008",
    name="反身性 (Reflexivity)",
    description="市场参与者的预期会影响基本面，形成自我强化的循环。上涨引发追涨→追涨推动上涨。直到预期不可持续。",
    key_principle="趋势中有反身性（正反馈），拐点处反身性断裂（负反馈）。识别当前处于哪个阶段。",
    when_to_apply="市场出现'这次不一样'的叙事时，或价格与基本面的背离持续扩大时",
    gold_application="央行购金→金价上涨→更多央行购金是典型的反身性循环。关注循环何时可能断裂",
    reference="George Soros",
    related_strategies=["s003", "s005"],
)

MODEL_CIRCLE_OF_COMPETENCE = MentalModel(
    id="m009",
    name="能力圈 (Circle of Competence)",
    description="只在自己真正理解的领域下重注。不懂的不碰。知道自己不知道什么比知道自己知道什么更重要。",
    key_principle="如果不理解为什么涨/跌，就不应该交易。每一笔交易都应该能用一句话说清逻辑。",
    when_to_apply="面对不熟悉的市场环境或交易品种时，宁可错过不可做错",
    gold_application="如果不理解美联储政策对金价的传导机制，就不要在FOMC前重仓押注方向",
    reference="Warren Buffett, Charlie Munger",
    related_strategies=["s001", "s006"],
)

MODEL_COMPOUNDING = MentalModel(
    id="m010",
    name="长期复利 (Long-term Compounding)",
    description="财富增长的核心不是短期暴利而是长期复利。避免大亏比追求大赚更重要。",
    key_principle="年化15%持续20年 > 翻倍后腰斩。保护本金是第一优先级。",
    when_to_apply="评估策略时从长期视角看，不因短期波动改变长期配置逻辑",
    gold_application="黄金的复利价值在于危机保护而非价格增值。它让你的股票仓位能在暴跌时拿住不卖",
    reference="Albert Einstein, Charlie Munger",
    related_strategies=["s001", "s006", "s008"],
)


ALL_MODELS: list[MentalModel] = [
    MODEL_CONTRARIAN,
    MODEL_TREND,
    MODEL_MEAN_REVERSION,
    MODEL_CYCLE,
    MODEL_ASYMMETRIC,
    MODEL_MARGIN_OF_SAFETY,
    MODEL_OCCAM,
    MODEL_REFLEXIVITY,
    MODEL_CIRCLE_OF_COMPETENCE,
    MODEL_COMPOUNDING,
]


def get_model_by_id(model_id: str) -> MentalModel | None:
    for m in ALL_MODELS:
        if m.id == model_id:
            return m
    return None
