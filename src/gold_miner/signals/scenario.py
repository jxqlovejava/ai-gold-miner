"""极端情景分析 — 系统性风险情景定义与黄金影响评估."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from gold_miner.signals.base import Signal, SignalBundle, SignalDirection, SignalStrength


@dataclass
class ScenarioDefinition:
    id: str
    name: str
    description: str
    gold_direction: SignalDirection
    gold_impact_score: float  # -1.0 ~ +1.0，对金价的方向+幅度
    duration: str  # short_term(周) | medium_term(月) | long_term(年) | permanent
    early_warnings: list[str]  # 预警指标
    confidence: float = 0.0  # 当前发生概率 (动态更新)
    last_updated: datetime = field(default_factory=datetime.now)
    # 多资产联动
    btc_impact_score: float = 0.0  # 对比特币的影响 (-1.0 ~ +1.0)
    btc_direction: str = ""  # "bullish" | "bearish" | "crash_then_pump" | "follow_gold"
    btc_note: str = ""
    # 多阶段路径 (用于 crash_then_pump 等情况)
    phase: str = ""  # "" | "two_phase"


# ---------------------------------------------------------------------------
# 预定义极端情景
# ---------------------------------------------------------------------------

PREDEFINED_SCENARIOS: dict[str, ScenarioDefinition] = {
    "us_debt_default_orderly": ScenarioDefinition(
        id="us_debt_default_orderly",
        name="美国债务违约—有序",
        description=(
            "技术性违约：政府短暂延迟支付后迅速达成协议恢复。"
            "美元信用受损但未崩溃，美联储快速注入流动性防止系统性风险。"
            "法币信用危机→非主权资产（黄金+BTC）同步暴涨。"
        ),
        gold_direction=SignalDirection.BULLISH,
        gold_impact_score=0.95,
        duration="long_term",
        early_warnings=[
            "美国CDS利差扩大 > 75bp",
            "债务上限谈判有进展但未达成",
            "短期国债收益率温和上升",
            "评级下调至AA但未到垃圾级",
            "美联储准备流动性工具",
            "市场恐慌但金融机构未出现系统性倒闭",
        ],
        btc_impact_score=1.00,
        btc_direction="bullish",
        btc_note="法币信用危机→BTC作为数字黄金与实物黄金同步暴涨，无流动性冲击",
    ),
    "us_debt_default_disorderly": ScenarioDefinition(
        id="us_debt_default_disorderly",
        name="美国债务违约—无序",
        description=(
            "实质性违约：美债被大幅降级，全球金融机构被迫平仓美债抵押品。"
            "第一阶段（1-3周）：流动性危机→所有资产无差别抛售，BTC暴跌（参照2020.3），黄金短暂跟跌。"
            "第二阶段（1-6月）：美联储无限QE救市→法币信用崩塌→BTC暴力反弹，黄金稳步上涨。"
        ),
        gold_direction=SignalDirection.BULLISH,
        gold_impact_score=0.90,
        duration="long_term",
        early_warnings=[
            "美国CDS利差爆发 > 150bp",
            "债务上限谈判完全破裂",
            "短期国债收益率极端飙升或跌至负值",
            "评级机构下调至BB+或以下（垃圾级）",
            "主要做市商无法为美债提供双边报价",
            "回购市场冻结，SOFR利率飙升",
            "美联储紧急降息+无限QE",
            "全球金融机构宣布美债相关损失",
        ],
        phase="two_phase",
        btc_impact_score=1.50,
        btc_direction="crash_then_pump",
        btc_note="第一阶段（流动性冲击）：BTC暴跌40-70%，黄金跌5-15%。第二阶段（QE救市）：BTC涨100-200%，黄金涨50-100%",
    ),
    "dollar_collapse": ScenarioDefinition(
        id="dollar_collapse",
        name="美元体系崩溃",
        description=(
            "全球去美元化加速，各国央行大规模抛售美元储备。"
            "DXY暴跌，以美元计价的黄金大幅上涨。"
        ),
        gold_direction=SignalDirection.BULLISH,
        gold_impact_score=0.90,
        duration="long_term",
        early_warnings=[
            "DXY跌破关键支撑位 (95/90)",
            "多国央行宣布本币结算替代美元",
            "BRICS扩展并推出替代结算体系",
            "美债外国持有量加速下降",
            "黄金占各国外汇储备比例快速上升",
        ],
        btc_impact_score=0.80,
        btc_direction="bullish",
        btc_note="美元崩溃→BTC作为非主权价值储存受益，但波动性放大",
    ),
    "global_recession": ScenarioDefinition(
        id="global_recession",
        name="全球经济衰退",
        description=(
            "主要经济体同步进入衰退，全球需求萎缩。"
            "央行被迫大幅降息+QE，实际利率暴跌，黄金受益。"
            "初期可能因流动性危机导致金价短暂下跌，随后反弹。"
        ),
        gold_direction=SignalDirection.BULLISH,
        gold_impact_score=0.70,
        duration="medium_term",
        early_warnings=[
            "美欧日中PMI同步低于50",
            "全球贸易量连续两个季度下滑",
            "大宗商品价格普跌（原油领先）",
            "美联储紧急降息50bp+",
            "VIX持续高于30",
        ],
        btc_impact_score=-0.40,
        btc_direction="crash_then_pump",
        btc_note="初期流动性冲击BTC承压，QE后反弹。历史参照2020.3→2020.12",
    ),
    "stagflation": ScenarioDefinition(
        id="stagflation",
        name="滞胀",
        description=(
            "高通胀+低增长的组合。传统货币政策工具失效，"
            "降息则通胀恶化，加息则经济崩溃。黄金在滞胀环境中表现优异。"
        ),
        gold_direction=SignalDirection.BULLISH,
        gold_impact_score=0.75,
        duration="medium_term",
        early_warnings=[
            "CPI/PCE持续 > 4% 且GDP增速 < 1%",
            "失业率上升但通胀不降（菲利普斯曲线失效）",
            "消费者信心指数大幅下滑",
            "实际利率持续为负",
            "供应端持续受限（能源/粮食/供应链）",
        ],
        btc_impact_score=-0.30,
        btc_direction="bearish",
        btc_note="滞胀下央行维持高利率，风险资产估值承压，BTC随科技股下跌",
    ),
    "geopolitical_crisis": ScenarioDefinition(
        id="geopolitical_crisis",
        name="重大地缘政治危机",
        description=(
            "大国军事冲突、重要海峡封锁、能源供应中断等。"
            "避险资金涌入黄金，同时供应端风险推高通胀预期。"
        ),
        gold_direction=SignalDirection.BULLISH,
        gold_impact_score=0.80,
        duration="short_term",
        early_warnings=[
            "军事部署/动员升级",
            "能源价格跳涨 > 20%",
            "航运指数飙升/航线中断",
            "Polymarket 战争相关市场概率暴增",
            "多国外交部发布旅行警告",
        ],
        btc_impact_score=0.10,
        btc_direction="follow_gold",
        btc_note="地缘危机期间BTC与黄金联动性不确定。战争初期资本管制可能限制加密交易",
    ),
    "fed_pivot_dovish": ScenarioDefinition(
        id="fed_pivot_dovish",
        name="美联储超预期转鸽",
        description=(
            "美联储在经济未明显恶化的情况下大幅降息或结束缩表。"
            "美元走弱，实际利率下行，黄金牛市的经典触发器。"
        ),
        gold_direction=SignalDirection.BULLISH,
        gold_impact_score=0.65,
        duration="medium_term",
        early_warnings=[
            "美联储官员公开转鸽",
            "市场定价降息概率 > 80%",
            "FOMC声明删除'限制性'措辞",
            "美联储意外降息 50bp+",
            "经济数据连续弱于预期",
        ],
        btc_impact_score=0.80,
        btc_direction="bullish",
        btc_note="降息→流动性宽松→风险资产全面受益，BTC弹性通常大于黄金",
    ),
    "deflation_spiral": ScenarioDefinition(
        id="deflation_spiral",
        name="通缩螺旋",
        description=(
            "价格持续下跌的自我强化循环。现金为王，资产价格崩盘。"
            "黄金短期承压（对手方为现金），但央行极端宽松后反弹。"
        ),
        gold_direction=SignalDirection.BEARISH,
        gold_impact_score=-0.40,
        duration="short_term",
        early_warnings=[
            "CPI连续三个月为负",
            "消费信贷大幅萎缩",
            "企业大幅降价去库存",
            "货币流通速度下降",
            "银行存款激增但贷款萎缩",
        ],
        btc_impact_score=-0.70,
        btc_direction="bearish",
        btc_note="通缩环境下现金为王，BTC作为风险资产可能暴跌。但央行QE后可能强势反弹",
    ),
    # ------------------------------------------------------------------
    # 看空情景
    # ------------------------------------------------------------------
    "volcker_style_hike": ScenarioDefinition(
        id="volcker_style_hike",
        name="沃尔克式暴力加息",
        description=(
            "通胀失控，美联储被迫将利率推至6%+。实际利率大幅转正，"
            "持有黄金的机会成本飙升。历史参照：1980年联邦基金利率20%，"
            "金价从850跌至300美元。当前若利率升至6%+且通胀回落至3%以下，"
            "实际利率+3%→黄金极度承压。"
        ),
        gold_direction=SignalDirection.BEARISH,
        gold_impact_score=-0.85,
        duration="medium_term",
        early_warnings=[
            "CPI/PCE连续3个月 > 4% 且上行",
            "美联储加息幅度超预期 (50bp+)",
            "联邦基金利率期货定价终端利率 > 6%",
            "实际利率 (TIPS收益率) 突破 +2.5%",
            "美联储官员持续鹰派表态",
            "工资-通胀螺旋迹象",
        ],
        btc_impact_score=-0.60,
        btc_direction="bearish",
        btc_note="高利率→风险资产估值全面压缩，BTC与纳斯达克高度相关，同步下跌",
    ),
    "new_bretton_woods": ScenarioDefinition(
        id="new_bretton_woods",
        name="新布雷顿森林体系",
        description=(
            "G20达成新全球储备货币协议，推出数字化SDR或类似机制替代美元。"
            "黄金作为过渡性避险资产的定位被削弱，央行储备中的黄金被部分替代。"
            "历史参照：1944年布雷顿森林体系建立后黄金被固定在$35/oz。"
            "但此情景概率极低—需要前所未有的全球政治协调。"
        ),
        gold_direction=SignalDirection.BEARISH,
        gold_impact_score=-0.70,
        duration="long_term",
        early_warnings=[
            "IMF正式提出SDR替代方案",
            "G20/G7峰会联合声明涉及储备货币改革",
            "BIS发布新储备资产框架",
            "多国央行同步减持黄金储备",
            "美联储/ECB/BOJ/BOE联合声明",
        ],
        btc_impact_score=0.20,
        btc_direction="bullish",
        btc_note="新储备体系可能包含数字化资产元素，BTC或受益但确定性低",
    ),
    "bitcoin_flippening": ScenarioDefinition(
        id="bitcoin_flippening",
        name="比特币替代黄金",
        description=(
            "比特币市值超越黄金，机构将'数字黄金'叙事内化。"
            "年轻一代投资者将比特币作为首选价值储存，黄金ETF资金持续流出。"
            "历史参照：2024年比特币ETF获批后黄金ETF流出约100亿美元。"
            "若加密市场成熟度进一步提升，黄金的'数字替代'效应加速。"
        ),
        gold_direction=SignalDirection.BEARISH,
        gold_impact_score=-0.50,
        duration="long_term",
        early_warnings=[
            "比特币市值突破黄金市值的50%+",
            "黄金ETF连续6个月净流出",
            "主流机构（贝莱德/富达）将BTC定位为黄金替代",
            "年轻群体持有BTC比例远超黄金",
            "Saylor等意见领袖主导'数字黄金'叙事",
        ],
        btc_impact_score=1.00,
        btc_direction="bullish",
        btc_note="此情景本质上就是资金从黄金轮动到BTC，零和博弈",
    ),
    "coordinated_cb_selloff": ScenarioDefinition(
        id="coordinated_cb_selloff",
        name="央行联合抛售黄金",
        description=(
            "主要国家央行协调减持黄金储备以回收流动性或压低金价。"
            "历史参照：1999年《央行黄金协议》(CBGA)签署后，英国抛售400吨→金价暴跌。"
            "2011-2015年央行购金暂停配合美元走强，金价跌45%。"
            "若G20协调抛售500吨+，金价将承受巨大压力。"
        ),
        gold_direction=SignalDirection.BEARISH,
        gold_impact_score=-0.60,
        duration="medium_term",
        early_warnings=[
            "WGC报告央行净购金转为净售金",
            "G20会议提及黄金储备管理协调",
            "IMF发布黄金储备处置框架",
            "单一国家宣布大规模抛售 (>100吨)",
            "CBGA或类似协议重新签署",
        ],
        btc_impact_score=0.30,
        btc_direction="bullish",
        btc_note="黄金抛售→部分资金可能流入BTC作为替代避险配置",
    ),
    "gold_industrial_substitution": ScenarioDefinition(
        id="gold_industrial_substitution",
        name="黄金工业替代",
        description=(
            "材料科学突破使黄金在电子/医疗/航天领域的工业用途被人工合成材料替代。"
            "工业需求占黄金年需求约8%，替代后减少约350吨/年的基础买盘。"
            "同时合成宝石技术成熟，黄金首饰需求也可能受合成材料冲击。"
            "概率极低，但若发生将永久削弱黄金的基本面支撑。"
        ),
        gold_direction=SignalDirection.BEARISH,
        gold_impact_score=-0.15,
        duration="permanent",
        early_warnings=[
            "论文发表：常温超导/替代导电材料突破",
            "半导体行业减少金键合线用量",
            "合成钻石/替代贵金属侵占珠宝市场",
            "主要消费国（中/印）工业用金量趋势性下降",
        ],
        btc_impact_score=0.0,
        btc_direction="",
        btc_note="仅影响黄金工业需求，BTC不受影响",
    ),
    "permanent_peace": ScenarioDefinition(
        id="permanent_peace",
        name="全球永久和平",
        description=(
            "全球地缘冲突系统性消退，大国关系进入长期稳定合作期。"
            "国防开支趋势性下降，地缘避险需求归零。"
            "黄金的'战争保险'溢价消失，金价回归纯粹的商品定价。"
            "概率近乎为零——但若发生，黄金将失去最重要的一块需求。"
        ),
        gold_direction=SignalDirection.BEARISH,
        gold_impact_score=-0.25,
        duration="permanent",
        early_warnings=[
            "联合国安理会重大改革通过",
            "全球军费连续3年下降",
            "大国签署全面裁军协议",
            "台海/南海/中东/俄乌 全部停火并达成长期协议",
        ],
        btc_impact_score=0.0,
        btc_direction="",
        btc_note="BTC也失去部分避险叙事，但受影响程度小于黄金",
    ),
}


# ---------------------------------------------------------------------------
# 情景分析器
# ---------------------------------------------------------------------------


class ScenarioAnalyzer:
    """极端情景分析器 — 评估当前市场是否逼近系统性风险情景."""

    SCENARIO_SIGNAL_THRESHOLD = 0.3  # 概率超此值生成信号

    def __init__(self) -> None:
        self.scenarios = dict(PREDEFINED_SCENARIOS)

    def update_probability(
        self,
        scenario_id: str,
        evidence_summary: dict[str, Any],
    ) -> None:
        """人工或依据数据更新情景概率."""
        if scenario_id in self.scenarios:
            self.scenarios[scenario_id].confidence = min(
                1.0, evidence_summary.get("probability", 0.0)
            )
            self.scenarios[scenario_id].last_updated = datetime.now()

    def assess_early_warnings(
        self,
        dxy_value: float | None = None,
        cds_spread: float | None = None,
        vix_value: float | None = None,
        cpi_value: float | None = None,
        gdp_growth: float | None = None,
        tbill_yield: float | None = None,
        fed_rate: float | None = None,
        real_rate: float | None = None,
        bitcoin_mcap_ratio: float | None = None,  # BTC市值/黄金市值
        gold_etf_flow_months: int = 0,  # 黄金ETF连续净流出月数
        central_bank_net: str = "",  # "buying" | "selling" | "neutral"
        polymarket_markets: list[Any] | None = None,
    ) -> dict[str, float]:
        """基于当前数据评估各情景的概率."""

        def _match_polymarket(keywords: list[str]) -> float:
            if not polymarket_markets:
                return 0.0
            for m in polymarket_markets:
                q = getattr(m, "question", "").lower()
                prob = getattr(m, "outcome_yes_price", 0)
                if any(kw.lower() in q for kw in keywords) and prob > 0.4:
                    return prob
            return 0.0

        probs: dict[str, float] = {}

        # 美国债务违约 — 有序 vs 无序
        debt_prob = 0.0
        if cds_spread and cds_spread > 30:
            debt_prob = min(cds_spread / 200, 0.5)
        if tbill_yield and tbill_yield > 7.0:
            debt_prob += 0.3
        pm_debt = _match_polymarket(["debt default", "debt ceiling", "us default"])
        total_debt_prob = min(debt_prob + pm_debt, 1.0)

        # 判断有序 vs 无序: 市场恐慌程度越高，无序概率越大
        panic_indicator = 0.0
        if cds_spread and cds_spread > 100:
            panic_indicator += min((cds_spread - 100) / 150, 0.6)
        if vix_value and vix_value > 35:
            panic_indicator += min((vix_value - 35) / 30, 0.4)
        if tbill_yield and tbill_yield > 9.0:
            panic_indicator += 0.3

        disorderly_ratio = min(panic_indicator, 1.0)  # 无序比例
        orderly_ratio = 1.0 - disorderly_ratio

        # 有序违约通常概率更高(因为更可能发生)，无序概率 = 总概率 × 无序占比
        probs["us_debt_default_orderly"] = total_debt_prob * orderly_ratio
        probs["us_debt_default_disorderly"] = total_debt_prob * disorderly_ratio

        # 美元崩溃
        dollar_prob = 0.0
        if dxy_value and dxy_value < 98:
            dollar_prob += (98 - dxy_value) / 30
        pm_dollar = _match_polymarket(["dollar collapse", "dedollarization", "brics currency"])
        probs["dollar_collapse"] = min(dollar_prob + pm_dollar, 1.0)

        # 全球衰退
        recession_prob = 0.0
        if gdp_growth is not None and gdp_growth < 1.0:
            recession_prob += (1.0 - gdp_growth) / 3
        pm_recession = _match_polymarket(["recession", "economic contraction", "negative growth"])
        probs["global_recession"] = min(recession_prob + pm_recession, 1.0)

        # 滞胀
        stag_prob = 0.0
        if cpi_value and gdp_growth is not None:
            if cpi_value > 3.5 and gdp_growth < 1.5:
                stag_prob = min((cpi_value - 3.5) / 5 + (1.5 - gdp_growth) / 2, 0.8)
        probs["stagflation"] = min(stag_prob, 1.0)

        # 地缘危机
        geo_prob = _match_polymarket([
            "war", "conflict", "invasion", "attack", "iran", "taiwan",
            "blockade", "nuclear", "missile",
        ])
        probs["geopolitical_crisis"] = min(geo_prob, 1.0)

        # 联储转鸽
        pivot_prob = 0.0
        pm_pivot = _match_polymarket(["fed cut", "rate cut", "fomc cut", "fed pivot", "dovish"])
        if gdp_growth is not None and gdp_growth < 1.5:
            pivot_prob += 0.3
        probs["fed_pivot_dovish"] = min(pivot_prob + pm_pivot, 1.0)

        # 通缩
        deflation_prob = 0.0
        if cpi_value is not None and cpi_value < 0.5:
            deflation_prob = max(0, (0.5 - cpi_value) / 2)
        probs["deflation_spiral"] = min(deflation_prob, 1.0)

        # ---- 看空情景 ----

        # 沃尔克式暴力加息
        volcker_prob = 0.0
        if cpi_value is not None and cpi_value > 3.5:
            volcker_prob += (cpi_value - 3.5) / 6  # CPI 4%→8%, 5%→25%
        if fed_rate is not None and fed_rate > 4.5:
            volcker_prob += (fed_rate - 4.5) / 6
        if real_rate is not None and real_rate > 1.5:
            volcker_prob += (real_rate - 1.5) / 3
        pm_hike = _match_polymarket(["fed hike", "rate hike", "tightening", "hawkish fed"])
        probs["volcker_style_hike"] = min(volcker_prob + pm_hike, 1.0)

        # 新布雷顿森林
        bwp_prob = _match_polymarket([
            "new reserve currency", "sdr", "bretton woods", "global currency reform",
            "imf reform", "reserve currency",
        ])
        probs["new_bretton_woods"] = min(bwp_prob * 0.7, 1.0)  # 概率本身很低

        # 比特币替代黄金
        btc_prob = 0.0
        if bitcoin_mcap_ratio is not None and bitcoin_mcap_ratio > 0.3:
            btc_prob = min(bitcoin_mcap_ratio, 1.0)
        if gold_etf_flow_months >= 3:
            btc_prob += gold_etf_flow_months * 0.05
        pm_btc = _match_polymarket([
            "bitcoin market cap", "bitcoin overtake", "bitcoin exceed gold",
            "digital gold", "bitcoin reserve",
        ])
        probs["bitcoin_flippening"] = min(btc_prob + pm_btc, 1.0)

        # 央行联合抛售
        cb_prob = 0.0
        if central_bank_net == "selling":
            cb_prob = 0.4
        pm_cb = _match_polymarket(["gold sell", "central bank gold", "gold reserve sell"])
        probs["coordinated_cb_selloff"] = min(cb_prob + pm_cb, 1.0)

        # 黄金工业替代 — 几乎不可自动评估，保持低位人工修正
        probs["gold_industrial_substitution"] = 0.02

        # 全球永久和平 — 一样不可自动评估
        probs["permanent_peace"] = 0.01

        # 更新内部状态
        for sid, prob in probs.items():
            if sid in self.scenarios:
                self.scenarios[sid].confidence = round(prob, 3)
                self.scenarios[sid].last_updated = datetime.now()

        return probs

    def generate_signals(self) -> list[Signal]:
        """将高概率情景转为交易信号."""
        signals: list[Signal] = []

        for scenario in self.scenarios.values():
            if scenario.confidence < self.SCENARIO_SIGNAL_THRESHOLD:
                continue

            score = scenario.gold_impact_score * scenario.confidence
            if scenario.gold_direction == SignalDirection.BEARISH:
                score = -abs(score)

            # 生成有区分度的描述
            if scenario.gold_impact_score > 0:
                if scenario.gold_impact_score >= 0.85:
                    impact_label = "极端利好"
                elif scenario.gold_impact_score >= 0.7:
                    impact_label = "强烈利好"
                elif scenario.gold_impact_score >= 0.5:
                    impact_label = "利好"
                else:
                    impact_label = "温和利好"
            else:
                if scenario.gold_impact_score <= -0.8:
                    impact_label = "极端利空"
                elif scenario.gold_impact_score <= -0.5:
                    impact_label = "强烈利空"
                elif scenario.gold_impact_score <= -0.3:
                    impact_label = "利空"
                else:
                    impact_label = "温和利空"

            signals.append(Signal(
                name=f"极端情景: {scenario.name}",
                dimension="scenario",
                direction=scenario.gold_direction,
                strength=(
                    SignalStrength.STRONG if scenario.confidence > 0.6
                    else SignalStrength.MODERATE
                ),
                score=round(max(-1.0, min(1.0, score)), 2),
                description=(
                    f"{scenario.name}概率 {scenario.confidence:.0%}，"
                    f"对金价{impact_label}，"
                    f"影响周期: {scenario.duration}"
                ),
                metadata={
                    "scenario_id": scenario.id,
                    "probability": scenario.confidence,
                    "impact_score": scenario.gold_impact_score,
                    "duration": scenario.duration,
                    "btc_impact": scenario.btc_impact_score,
                    "btc_direction": scenario.btc_direction,
                    "btc_note": scenario.btc_note,
                    "phase": scenario.phase,
                    "warnings_matched": scenario.early_warnings,
                },
            ))

        return signals

    def get_active_scenarios(self) -> list[ScenarioDefinition]:
        """获取当前活跃情景（概率 > 0）."""
        return [
            s for s in self.scenarios.values()
            if s.confidence > 0
        ]

    def get_high_probability_scenarios(self) -> list[ScenarioDefinition]:
        """获取高概率情景（概率 > 30%）."""
        return [
            s for s in self.scenarios.values()
            if s.confidence > self.SCENARIO_SIGNAL_THRESHOLD
        ]
