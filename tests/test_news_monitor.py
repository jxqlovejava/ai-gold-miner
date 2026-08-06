"""news_monitor 突发新闻方向标注测试.

覆盖: 方向/程度判断 + 上下文修正 + 未落地降级 + 否定句过滤 + 推送格式.
"""

from __future__ import annotations

import re

import pytest

from gold_miner.sentinel import news_monitor as nm


@pytest.fixture(autouse=True)
def _isolated_dedup(tmp_path, monkeypatch):
    """每次测试用独立的去重文件, 避免全局 /tmp 缓存污染."""
    monkeypatch.setattr(nm, "_DEDUP_FILE", tmp_path / "dedup.json")
    yield


def _analyze(title: str) -> dict | None:
    """分析单条标题, 返回首条告警 (或 None=被过滤)."""
    alerts = nm.analyze_headlines([{"title": title, "ts": None, "source": "测试"}])
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
