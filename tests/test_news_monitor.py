"""news_monitor 突发新闻方向标注测试.

覆盖: 方向/程度判断 + 上下文修正 + 未落地降级 + 否定句过滤 + 推送格式.
"""

from __future__ import annotations

import re

import pytest

from gold_miner.sentinel import news_monitor as nm


class _DisabledSemantic:
    """语义层禁用桩 — 现有用例按纯关键词规则跑."""

    enabled = False
    categories: set[str] = set()


class _FakeSemantic:
    """可编程语义分析器桩 — 预置 classify_many 返回结果."""

    enabled = True
    categories = {"geopolitical", "energy", "trade", "policy", "election"}

    def __init__(self, results=None):
        self.results = results or {}

    def classify_many(self, routed):
        return dict(self.results)


@pytest.fixture(autouse=True)
def _isolated_dedup(tmp_path, monkeypatch):
    """每次测试用独立的去重文件, 避免全局 /tmp 缓存污染; 默认禁用语义层."""
    monkeypatch.setattr(nm, "_DEDUP_FILE", tmp_path / "dedup.json")
    monkeypatch.setattr(nm, "_semantic_analyzer", lambda: _DisabledSemantic())
    yield


def _analyze(title: str, semantic=None) -> dict | None:
    """分析单条标题, 返回首条告警 (或 None=被过滤)."""
    alerts = nm.analyze_headlines(
        [{"title": title, "ts": None, "source": "测试"}], semantic=semantic
    )
    return alerts[0] if alerts else None


def _format(alerts: list[dict]) -> str:
    return nm.format_news_alerts(alerts, gold_price=4264, gold_change=0.4)


# ── 霍尔木兹: 上下文修正 (核心修复点) ──


def test_hormuz_agreement_is_bullish():
    a = _analyze("伊阿霍尔木兹原则上达成协议 分道航行重开海峡")
    assert a is not None
    assert a["direction"] == "bullish"
    assert a["severity"] == "major"
    assert "利多" in a["label"]


def test_hormuz_blockade_is_bullish():
    a = _analyze("霍尔木兹海峡遭封锁 油轮触雷爆炸")
    assert a is not None
    assert a["direction"] == "bullish"
    assert a["severity"] == "major"
    assert "利多" in a["label"]


# ── 美联储 ──


def test_fed_cut_is_bullish():
    a = _analyze("美联储宣布降息25个基点")
    assert a is not None
    assert a["direction"] == "bullish"
    assert a["severity"] == "major"


def test_fed_hold_is_neutral():
    a = _analyze("美联储维持利率按兵不动")
    assert a is not None
    assert a["direction"] == "neutral"


def test_warsh_hawkish_is_bearish():
    a = _analyze("沃什称坚持鹰派立场 继续抗通胀")
    assert a is not None
    assert a["direction"] == "bearish"
    assert a["severity"] == "moderate"


def test_warsh_neutral_by_default():
    a = _analyze("美联储主席沃什坚持精简沟通策略")
    assert a is not None
    assert a["direction"] == "neutral"


# ── 未落地降级 ──


def test_pending_hormuz_is_neutral():
    a = _analyze("交易员等待霍尔木兹海峡航运状况明朗化")
    assert a is not None
    assert a["direction"] == "neutral"
    assert a["severity"] == "minor"
    assert "方向未定" in a["impact"]


# ── 2026-08-07 系统性修复: 战争溢价传导链方向 + 过度解读 ──


def test_us_iran_conflict_is_bullish():
    """冲突升级→避险+战争溢价→利多 (修复前误标利空)."""
    for t in ["美军对伊朗核设施发动空袭", "伊朗向以色列发射导弹袭击", "美伊在霍尔木兹海峡附近交火"]:
        a = _analyze(t)
        assert a is not None, t
        assert a["direction"] == "bullish", t
        assert "利多" in a["label"], t


def test_merely_mentioning_iran_is_not_escalation():
    """仅提及/关注伊朗, 无冲突动作词 → 不告警 (修复前过度解读为冲突升级)."""
    for t in ["美股周四午盘走低，交易员关注伊朗局势", "伊朗局势受关注 美股早盘走高", "美伊紧张局势引发市场担忧"]:
        assert _analyze(t) is None, t


def test_houthi_red_sea_attack_is_bullish():
    """红海/胡塞/油轮袭击 → 利多 (新增 P0 规则, 此前无覆盖)."""
    for t in ["胡塞武装袭击沙特油轮 红海局势紧张", "红海商船遭袭 曼德海峡航运中断"]:
        a = _analyze(t)
        assert a is not None, t
        assert a["direction"] == "bullish", t


def test_hormuz_premium_retrace_is_neutral():
    """协议达成但战争溢价回吐 → 中性 (利多/利空对冲, 修复前误标利多)."""
    for t in ["美伊达成停火协议 战争溢价开始回吐", "霍尔木兹协议签署 金价利好兑现获利了结"]:
        a = _analyze(t)
        assert a is not None, t
        assert a["direction"] == "neutral", t


# ── 欧洲天然气 (传导弱, 不再误判利空) ──


def test_eu_gas_price_neutral():
    a = _analyze("欧洲天然气价格上涨 交易员等待霍尔木兹明朗化")
    assert a is not None
    assert a["direction"] == "neutral"
    assert a["severity"] == "minor"


def test_eu_gas_with_oil_context_bullish():
    a = _analyze("欧洲天然气价格大涨 原油供应危机升级")
    assert a is not None
    assert a["direction"] == "bullish"


# ── 2026-08-08 系统性修复: '削弱/降温/回落加息预期'方向反转 (修复前误标利空) ──


def test_weakened_hike_expectation_is_bullish():
    """非农爆冷削弱美联储加息预期 → 加息预期减弱 → 实际利率预期↓ → 利多 (修复前误标利空·重大)."""
    a = _analyze("非农爆冷削弱美联储加息预期 通胀数据接棒成为市场焦点")
    assert a is not None
    assert a["direction"] == "bullish"
    assert a["severity"] == "major"
    assert "利多" in a["label"]
    assert "利多金价" in a["impact"]


def test_cooling_hike_expectation_is_bullish():
    """加息预期降温/回落 (无美联储前缀) → 利多."""
    for t in ["加息预期降温 金价走高", "加息预期回落 美债收益率下行"]:
        a = _analyze(t)
        assert a is not None, t
        assert a["direction"] == "bullish", t


def test_weakened_cut_expectation_is_bearish():
    """削弱降息预期 → 降息预期减弱 → 实际利率预期↑ → 利空 (对称反转)."""
    a = _analyze("非农超预期削弱美联储降息预期")
    assert a is not None
    assert a["direction"] == "bearish"
    assert a["severity"] == "major"
    assert "利空" in a["label"]


def test_nfp_cold_standalone_is_bullish():
    """非农爆冷 (无加息字眼) → 劳动力恶化 → 降息预期↑ → 利多."""
    a = _analyze("美国7月非农爆冷 失业率意外上升")
    assert a is not None
    assert a["direction"] == "bullish"


def test_hike_expectation_heating_stays_bearish():
    """加息预期升温 → 仍利空 (反转规则不误伤升温情形)."""
    a = _analyze("美联储加息预期升温 黄金承压")
    assert a is not None
    assert a["direction"] == "bearish"


# ── 2026-08-10 系统性修复: '加息/降息概率走低'方向反转 (修复前误标利空/利多) ──
# 根因: 反转词表缺 '走低/下滑', 掉入裸'美联储.*加息'规则 → 强烈利空。词表已收敛至 direction_lexicon。


def test_hike_probability_declining_is_bullish():
    """加息概率走低 → 收紧预期↓ → 利多 (修复前误标「强烈利空金价」)."""
    for t in [
        "美联储9月加息概率走低 市场押注转向降息",
        "美国9月加息概率走低至40%",
        "CME FedWatch显示9月加息概率下降",
        "美联储加息概率下滑",
        "加息概率回落至42%",
        "交易员削减加息预期 概率降至43.9%",
    ]:
        a = _analyze(t)
        assert a is not None, t
        assert a["direction"] == "bullish", t
        assert "利空" not in a["impact"], t  # 不得再引用裸'加息'的利空因果链


def test_cut_probability_declining_is_bearish():
    """降息概率走低 → 宽松预期↓ → 实际利率预期↑ → 利空 (对称反转)."""
    a = _analyze("美联储9月降息概率走低")
    assert a is not None
    assert a["direction"] == "bearish"


def test_reversal_headline_upgrades_to_llm():
    """反转构式标题命中 _SEMANTIC_AMBIGUITY_PATTERNS → 升级 LLM 裁决 (第二道防线)."""
    assert nm._has_ambiguity_signal("美联储9月加息概率走低 市场押注转向降息")
    assert nm._has_ambiguity_signal("美联储9月降息概率走低")


# ── 否定句过滤 ──


def test_negated_hike_filtered():
    a = _analyze("美联储不会加息 维持宽松")
    assert a is None


# ── 推送格式 ──


def test_format_contains_direction_label():
    alerts = [
        {
            "title": "伊阿霍尔木兹原则上达成协议",
            "impact": "霍尔木兹协议→油价↓→利多金价",
            "level": "P0",
            "category": "energy",
            "direction": "bullish",
            "severity": "major",
            "label": "利多·重大",
            "source": "测试",
        },
    ]
    out = _format(alerts)
    assert "💡 🟢[利多·重大]" in out
    assert "🚨 重大突发" in out


def test_format_includes_item_and_footer_time():
    alerts = [
        {
            "title": "伊阿霍尔木兹原则上达成协议",
            "impact": "霍尔木兹协议→油价↓→利多金价",
            "level": "P0",
            "category": "energy",
            "direction": "bullish",
            "severity": "major",
            "label": "利多·重大",
            "source": "测试",
            "ts": 1780000000,
        },
    ]
    out = _format(alerts)
    assert re.search(r"\[能源危机 \d{2}:\d{2}\]", out)  # 单条新闻时间
    assert re.search(r"🕐 \d{2}-\d{2} \d{2}:\d{2}", out)  # 生成时间
    assert "📡 来源: 测试" in out  # 来源标注清晰
    assert "自动监控" not in out


def test_format_omits_item_time_when_unknown():
    alerts = [
        {
            "title": "伊阿霍尔木兹原则上达成协议",
            "impact": "霍尔木兹协议→油价↓→利多金价",
            "level": "P0",
            "category": "energy",
            "direction": "bullish",
            "severity": "major",
            "label": "利多·重大",
            "source": "测试",
        },
    ]
    out = _format(alerts)
    assert "[能源危机] 伊阿霍尔木兹" in out  # 无时间字段 → 保持原样


def test_format_p1_has_label():
    alerts = [
        {
            "title": "欧洲天然气价格上涨",
            "impact": "欧洲天然气对金价传导弱→方向未定",
            "level": "P1",
            "category": "energy",
            "direction": "neutral",
            "severity": "minor",
            "label": "中性·轻度",
            "source": "测试",
        },
    ]
    out = _format(alerts)
    assert "⚠️ 关注" in out
    assert "💡 ⚪[中性·轻度]" in out


# ── AI 语义推理层 (三层架构 Stage 2/3) ──


def test_semantic_corrects_hormuz_restriction():
    """事故标题: '不得通过'=通行限制(非缓和), AI 覆盖 regex 的'供应危机缓解'链."""
    title = "伊朗披露阿曼协议细节：美国和以色列船只不得通过霍尔木兹海峡"
    fake = _FakeSemantic({
        title: {
            "direction": "bullish", "severity": "major", "priority": "P0",
            "category": "energy",
            "transmission_chain": "霍尔木兹通行限制→供应中断风险→油价↑→利多金价(避险+抗通胀)；若油价↑持续推升加息预期则远期承压",
            "is_real_event": True, "is_pending": False, "confidence": 0.85,
        },
    })
    a = _analyze(title, semantic=fake)
    assert a is not None
    assert a["direction"] == "bullish"
    assert "通行限制" in a["impact"]
    assert "供应危机缓解" not in a["impact"]  # 修复前误链
    assert "利多金价" in a["impact"]


def test_semantic_drops_pure_mention():
    """纯提及(候选B) → AI 判 is_real_event=false → 不告警."""
    title = "美股周四午盘走低，交易员关注伊朗局势"
    fake = _FakeSemantic({
        title: {
            "is_real_event": False, "is_pending": False, "direction": "neutral",
            "severity": "minor", "priority": "P2", "category": "geopolitical",
            "transmission_chain": "纯提及/关注，无实质动作→不告警", "confidence": 0.9,
        },
    })
    assert _analyze(title, semantic=fake) is None


def test_semantic_broad_mention_real_event_becomes_alert():
    """候选B (无 strict 命中) 被 AI 判为真实事件 → 生成告警 (提升召回)."""
    title = "伊朗外长下周访问莫斯科 讨论地区局势"
    fake = _FakeSemantic({
        title: {
            "is_real_event": True, "is_pending": False, "direction": "neutral",
            "severity": "minor", "priority": "P1", "category": "geopolitical",
            "transmission_chain": "外交动向→局势不确定性→短期金价方向未定",
            "confidence": 0.8,
        },
    })
    a = _analyze(title, semantic=fake)
    assert a is not None
    assert a["category"] == "geopolitical"
    assert "金价方向未定" in a["impact"]


def test_pending_guard_overrides_llm_direction():
    """确定性守卫: 标题命中未落地/溢价回吐 → 强制中性, 覆盖 AI 给的利多."""
    title = "美伊达成停火协议 战争溢价开始回吐"
    fake = _FakeSemantic({
        title: {
            "is_real_event": True, "is_pending": False, "direction": "bullish",
            "severity": "major", "priority": "P0", "category": "geopolitical",
            "transmission_chain": "停火→长期降息利多", "confidence": 0.9,
        },
    })
    a = _analyze(title, semantic=fake)
    assert a is not None
    assert a["direction"] == "neutral"
    assert "方向未定" in a["impact"]


def test_semantic_failure_falls_back_to_regex():
    """AI 失败/无返回 → 回退关键词规则, 告警不丢失."""
    title = "伊阿霍尔木兹原则上达成协议 分道航行重开海峡"
    fake = _FakeSemantic({})  # classify_many 返回空 → 无 LLM 结果
    a = _analyze(title, semantic=fake)
    assert a is not None
    assert a["direction"] == "bullish"  # 保留 regex 上下文修正结果
    assert "利多金价" in a["impact"]


def test_semantic_low_confidence_falls_back_to_regex():
    """置信度过低(<0.5) → 不采用 AI 结果, 保留 regex."""
    title = "伊朗披露阿曼协议细节：美国和以色列船只不得通过霍尔木兹海峡"
    fake = _FakeSemantic({
        title: {
            "direction": "neutral", "severity": "minor", "priority": "P1",
            "category": "energy", "transmission_chain": "低置信结果",
            "is_real_event": True, "is_pending": False, "confidence": 0.3,
        },
    })
    a = _analyze(title, semantic=fake)
    assert a is not None
    assert "供应危机缓解" in a["impact"]  # 回退 regex 的覆盖链


def test_semantic_corrects_weakened_hike_via_escalation():
    """fed 类目带反转信号 → 升级路由 LLM, AI 裁决覆盖 regex."""
    title = "非农爆冷削弱美联储加息预期 通胀数据接棒成为市场焦点"
    fake = _FakeSemantic({
        title: {
            "direction": "bullish", "severity": "major", "priority": "P0",
            "category": "fed",
            "transmission_chain": "非农爆冷→削弱加息预期→实际利率预期↓→利多金价",
            "is_real_event": True, "is_pending": False, "confidence": 0.9,
        },
    })
    a = _analyze(title, semantic=fake)
    assert a is not None
    assert a["direction"] == "bullish"
    assert "利多金价" in a["impact"]


# ── 2026-08-10 修复: 公司签约类新闻混入突发预警 ──
# 方案A: 收紧地缘降级规则 (裸'协议|签署'不再独立命中 P0)
# 方案B: AI 不可用时金价相关性闸门兜底 (_has_gold_relevance)
# 事故标题: '迅策科技与天合算力签署战略合作备忘录' 被'签署'泛词误判 P0 利多


def test_corporate_signing_memo_dropped():
    """纯公司签约(无金价维度) → AI 不可用时不告警 (修复前误判 P0 利多)."""
    for t in [
        "迅策科技与天合算力签署战略合作备忘录",
        "华为与腾讯签署战略合作协议",
        "两家公司达成战略合作框架协议",
    ]:
        assert _analyze(t) is None, t


def test_geo_agreement_still_alerted():
    """地缘主体+协议 → 仍告警 (方案A收紧后不误伤真正的地缘缓和事件)."""
    for t in ["美伊达成停火协议", "霍尔木兹原则协议正式签署", "中东多国宣布全面停火"]:
        a = _analyze(t)
        assert a is not None, t
        assert a["direction"] == "bullish", t


def test_military_deployment_still_alerted():
    """军事部署(含'美军/航母'等词) → 相关性闸门不误杀."""
    a = _analyze("美军向中东增派航母战斗群")
    assert a is not None
    assert a["direction"] == "bullish"


def test_gold_relevance_gate():
    """相关性闸门单元: 无关商业标题 → False; 金价相关标题 → True."""
    for t in [
        "迅策科技与天合算力签署战略合作备忘录",
        "华为与腾讯签署战略合作协议 布局云计算",
        "某券商发布研报看好新能源板块",
    ]:
        assert not nm._has_gold_relevance(t), t
    for t in [
        "美伊达成停火协议",
        "霍尔木兹原则协议正式签署",
        "美军向中东增派航母战斗群",
        "非农爆冷削弱美联储加息预期",
        "黄金价格创历史新高",
        "布伦特原油价格飙升",
        "美联储维持利率按兵不动",
    ]:
        assert nm._has_gold_relevance(t), t


def test_irrelevant_candidate_dropped_by_gate():
    """命中规则但标题无金价维度 → AI 不可用时被相关性闸门丢弃.
    '科威特'裸国家名命中冲突外溢 P1 规则, 但标题是纯商业新闻."""
    assert _analyze("科威特某企业签署港口运营协议") is None


def test_ai_active_passes_relevance_gate():
    """AI 已裁决(真实事件) → 相关性闸门不重复拦截 (B 仅兜底 AI 不可用)."""
    title = "科威特某企业与海湾公司签署港口运营协议"
    fake = _FakeSemantic({
        title: {
            "is_real_event": True, "is_pending": False, "direction": "bullish",
            "severity": "moderate", "priority": "P1", "category": "geopolitical",
            "transmission_chain": "海湾地区商业合作→区域稳定→地缘风险缓释", "confidence": 0.85,
        },
    })
    a = _analyze(title, semantic=fake)
    assert a is not None


# ── 2026-08-10 修复: 海湾国家裸国名误报 (阿联酋扩产被误判'冲突外溢'利多) ──
# 根因: 旧规则 pattern 为裸国名交替 '科威特|巴林|卡塔尔|约旦|阿联酋|沙特.*遭.*袭击',
#       标题仅含'阿联酋'即命中, 套用写死的'冲突外溢→避险买盘'利多模板。
#       事故标题: '阿联酋退出欧佩克后，Adnoc Gas加码超80亿美元扩大天然气产能'
#       (纯能源投资; 金价相关性闸门因'天然气'放行 → 误报 P1 利多·中度)。
#       修复: 规则要求国名 + 军事冲突动作同现(双向), 裸国名不再命中。


def test_gulf_country_bare_mention_not_conflict():
    """海湾国名纯提及/商业投资(无冲突动作) → 不告警 (修复前误判冲突外溢利多)."""
    for t in [
        "阿联酋退出欧佩克后，Adnoc Gas加码超80亿美元扩大天然气产能",
        "卡塔尔宣布扩建液化天然气项目",
        "沙特启动新一轮经济多元化计划",
        "阿联酋与某国签署贸易协定",
    ]:
        assert _analyze(t) is None, t


def test_gulf_country_conflict_still_alerted():
    """海湾国家冲突(遭袭/遇袭/局势升级) → 仍告警方向利多."""
    for t in [
        "科威特遭伊朗导弹袭击",
        "沙特遇袭 红海局势紧张",
        "阿联酋局势升级 地区避险情绪升温",
        "伊朗空袭科威特境内目标",
    ]:
        a = _analyze(t)
        assert a is not None, t
        assert a["direction"] == "bullish", t


def test_gulf_country_conflict_uses_spillover_chain():
    """海湾规则专属命中 → 冲突外溢因果链 (区别于美伊/胡塞规则链)."""
    for t in [
        "沙特遇袭 红海局势紧张",
        "阿联酋局势升级 地区避险情绪升温",
        "巴林冲突持续 中东局势动荡",
    ]:
        a = _analyze(t)
        assert a is not None, t
        assert "冲突外溢" in a["impact"], t


# ── 2026-08-11 修复: 4 个推送误判案例 (用户实测微信推送复盘) ──
# 1) 假想/条件语气被顶格 P0   2) context_rules 顺序遮蔽中性规则
# 3) '重开前景未明'子串误判   4) 能源→加息预期→利空链缺失


def test_hypothetical_hassett_not_major():
    """假想语气(如果他身在美联储会降息) → 降级 P1·中度, 不顶格 P0 重大利多."""
    a = _analyze("哈塞特称如果他身在美联储 会维持利率不变或降息")
    assert a is not None
    assert a["level"] == "P1"  # 修复前 P0
    assert a["severity"] == "moderate"  # 修复前 major
    assert a["direction"] == "bullish"  # 假想鸽派仍是弱利多, 方向保留
    assert "假想" in a["impact"]


def test_real_fed_cut_still_major():
    """实际降息动作(非假想) → 不受假想守卫影响, 仍 P0 重大利多."""
    a = _analyze("美联储宣布降息25个基点")
    assert a is not None
    assert a["level"] == "P0"
    assert a["severity"] == "major"
    assert "假想" not in a["impact"]


def test_trader_watching_hormuz_agreement_neutral():
    """'交易员关注霍尔木兹协议'(未达成) → 中性, 不被'协议'泛词误标利多."""
    a = _analyze("开盘：美股小幅低开 交易员关注霍尔木兹海峡协议与通胀数据")
    assert a is not None
    assert a["direction"] == "neutral"  # 修复前 bullish·重大
    assert a["severity"] == "minor"
    assert "方向未定" in a["impact"]


def test_hormuz_reopen_prospect_unclear_neutral():
    """'霍尔木兹海峡重开前景未明' → 未落地中性, '重开'子串不误判'已重开利多'."""
    a = _analyze("霍尔木兹海峡重开前景未明 欧洲天然气价格大涨")
    assert a is not None
    assert a["direction"] == "neutral"  # 修复前 bullish 且因果链与标题相矛盾
    assert "方向未定" in a["impact"]


def test_energy_pushing_hike_expectation_bearish():
    """'能源价格飙升推高加息预期' → 利空 (修复前误判中性·轻度)."""
    for t in [
        "欧洲债市：德国国债和英国国债下跌 能源价格飙升推高加息预期",
        "能源价格大涨推升加息预期 金价承压",
    ]:
        a = _analyze(t)
        assert a is not None, t
        assert a["direction"] == "bearish", t
        assert "利空金价" in a["impact"], t
