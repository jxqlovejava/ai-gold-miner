"""gold_bias 显式方向字段的系统性回归测试.

事故背景 (.learnings/2026-08-10):
1. 关键词词表缺"下修" → 非农-2.3万误判中性 (同义词漏配)
2. 失业率4.1%系参与率下降的分母幻觉 → 组合语义关键词无法识别
3. 初请"低于预期"命中弱词判多 → 反向指标极性错误 (申请少=劳动力强=偏鹰利空)
修复: 写入时同步判定 gold_bias 显式字段, 引擎优先读取, 关键词降级 fallback,
两者冲突时生成待复核告警信号。
"""

from datetime import UTC, datetime, timedelta

import pytest

from gold_miner.data.calendar import (
    CalendarEvent,
    EventCalendar,
    EventImpact,
    EventType,
)
from gold_miner.signals.base import SignalDirection
from gold_miner.signals.recent_events import (
    RecentEventSignalGenerator,
    _infer_direction_from_event,
)

NFP_ACTUAL = "-2.3万人 (预期+8.0万, 前值下修至+2.0万), 失业率4.1% (前值4.2%)"
CLAIMS_ACTUAL = "实际 19.9万 (预期20.2万, 前值19.8万) 低于预期"  # 含"低于"弱词


class TestKeywordFallback:
    """无 gold_bias 时走关键词路径 (8/10 词表修复的回归保护)."""

    def test_nfp_downward_revision_is_bullish(self) -> None:
        direction, conflict = _infer_direction_from_event("非农", NFP_ACTUAL, "+8.0万")
        assert direction is SignalDirection.BULLISH
        assert conflict is None

    def test_strong_text_stays_bearish(self) -> None:
        direction, _ = _infer_direction_from_event("GDP", "上修至+3.2%, 增长加速", None)
        assert direction is SignalDirection.BEARISH

    def test_no_keyword_is_neutral(self) -> None:
        direction, _ = _infer_direction_from_event("某事件", "结果已发布", None)
        assert direction is SignalDirection.NEUTRAL

    # ── 2026-08-10 系统性修复: '加息/降息概率走低'方向反转 (修复前误标利空/利多) ──
    # 根因: 裸'加息'子串检查先于反转构式 → '加息概率走低' 误判利空。反转判定已收敛至 direction_lexicon。

    def test_hike_probability_declining_is_bullish(self) -> None:
        """加息概率走低 → 收紧预期↓ → 利多 (修复前误判 bearish)."""
        for actual in [
            "美联储9月加息概率走低至40%",
            "CME FedWatch显示9月加息概率下降",
            "美联储加息概率下滑",
            "加息概率回落至42%",
        ]:
            direction, conflict = _infer_direction_from_event("加息概率观测", actual, None)
            assert direction is SignalDirection.BULLISH, actual
            assert conflict is None

    def test_cut_probability_declining_is_bearish(self) -> None:
        """降息概率走低 → 宽松预期↓ → 利空 (修复前误判 bullish)."""
        direction, _ = _infer_direction_from_event(
            "降息概率观测", "美联储9月降息概率走低", None,
        )
        assert direction is SignalDirection.BEARISH

    def test_hike_probability_rising_stays_bearish(self) -> None:
        """加息概率回升/升温 → 仍利空 (反转构式不误伤升温情形)."""
        for actual in ["9月加息概率回升至60%", "加息预期升温"]:
            direction, _ = _infer_direction_from_event("加息预期观测", actual, None)
            assert direction is SignalDirection.BEARISH, actual


class TestExplicitGoldBias:
    """显式 gold_bias 优先于关键词, 并处理极性/组合语义."""

    def test_explicit_overrides_wrong_polarity(self) -> None:
        """初请"低于预期"关键词判多, 但反向指标实际利空 → 显式 bearish 胜出 + 冲突告警."""
        direction, conflict = _infer_direction_from_event(
            "初请", CLAIMS_ACTUAL, None, gold_bias="bearish",
        )
        assert direction is SignalDirection.BEARISH
        assert conflict is not None and "冲突" in conflict

    def test_explicit_agrees_with_keywords_no_conflict(self) -> None:
        direction, conflict = _infer_direction_from_event(
            "非农", NFP_ACTUAL, None, gold_bias="bullish",
        )
        assert direction is SignalDirection.BULLISH
        assert conflict is None

    def test_explicit_neutral_suppresses_keyword(self) -> None:
        """混合地缘事件: 关键词误判时显式 neutral 以写入判定为准."""
        direction, conflict = _infer_direction_from_event(
            "协议", "谈判低于预期进展", None, gold_bias="neutral",
        )
        assert direction is SignalDirection.NEUTRAL
        assert conflict is not None  # 关键词 bullish vs 显式 neutral → 告警


class TestHedgedReasoning:
    """复杂对冲事件不再产生假阳性冲突告警 (2026-08-27 事故修复).

    事故: Flash PMI(写 neutral, 关键词因"加息概率回落"判 bullish) 与
    国债回购(写 bullish, 关键词因二阶风险注"鹰派加息"判 bearish) 两连假阳性。
    修复: actual 含双向对冲标记(一阶/二阶/对冲/双向/取中性) 或 利多+利空并存
    时, 关键词推断不可靠 → 跳过 naive 冲突告警, 以显式 gold_bias 为准。
    """

    FLASH_PMI = (
        "美国: 综合PMI 56.0(前值54.5, 52个月新高); 价格压力降温, '产出更热+通胀更冷'"
        "goldilocks组合; gold_bias=neutral判定: 一阶通胀降温->加息概率回落->利多金价; "
        "二阶增长新高->risk-on+避险需求下降->利空金价; 双向对冲取中性"
    )
    BUYBACK = (
        "一阶TGA支出=向银行体系注入准备金+压制长端收益率->实际利率预期下行->利多金价; "
        "二阶风险升级标注: 流动性注入放大通胀反弹风险->鹰派加息->实际利率反升; "
        "gold_bias=bullish维持"
    )

    def test_hedged_flash_pmi_no_conflict(self) -> None:
        direction, conflict = _infer_direction_from_event(
            "全球Flash PMI(8月)", self.FLASH_PMI, None, gold_bias="neutral",
        )
        assert direction is SignalDirection.NEUTRAL
        assert conflict is None

    def test_hedged_buyback_no_conflict(self) -> None:
        direction, conflict = _infer_direction_from_event(
            "美财政部扩大长端国债回购", self.BUYBACK, None, gold_bias="bullish",
        )
        assert direction is SignalDirection.BULLISH
        assert conflict is None

    def test_unhedged_true_conflict_still_warns(self) -> None:
        """无对冲标记 + 关键词与显式方向相反 → 仍须告警 (不误杀真实冲突)."""
        direction, conflict = _infer_direction_from_event(
            "测试", "数据低于预期, 就业放缓, 降息预期升温", None, gold_bias="bearish",
        )
        assert direction is SignalDirection.BEARISH
        assert conflict is not None


class TestSerde:
    """gold_bias 序列化往返 + 写入校验."""

    def test_jsonl_roundtrip(self, tmp_path) -> None:
        cal = EventCalendar(data_path=tmp_path / "cal.jsonl")
        ev = CalendarEvent(
            name="测试事件",
            event_type=EventType.NFP,
            scheduled_at=datetime.now(tz=UTC) - timedelta(days=1),
            impact=EventImpact.HIGH,
            actual=NFP_ACTUAL,
            gold_bias="bullish",
        )
        cal.add_event(ev, force=True)

        cal2 = EventCalendar(data_path=tmp_path / "cal.jsonl")
        loaded = [e for e in cal2.events if e.name == "测试事件"]
        assert loaded and loaded[0].gold_bias == "bullish"

    def test_invalid_gold_bias_rejected(self, tmp_path) -> None:
        cal = EventCalendar(data_path=tmp_path / "cal.jsonl")
        ev = CalendarEvent(
            name="测试事件",
            event_type=EventType.NFP,
            scheduled_at=datetime.now(tz=UTC) - timedelta(days=1),
            impact=EventImpact.HIGH,
            actual="x",
        )
        cal.add_event(ev, force=True)
        with pytest.raises(ValueError, match="gold_bias"):
            cal.update_event_result(
                name="测试事件",
                scheduled_at=ev.scheduled_at,
                actual="y",
                gold_bias="to_the_moon",  # type: ignore[arg-type]
            )


class TestConflictSignal:
    """generate_signals 层: 冲突事件产出待复核告警信号."""

    def test_conflict_signal_emitted(self, tmp_path) -> None:
        cal = EventCalendar(data_path=tmp_path / "cal.jsonl")
        cal.add_event(
            CalendarEvent(
                name="初请失业金(极性测试)",
                event_type=EventType.PMI,
                scheduled_at=datetime.now(tz=UTC) - timedelta(days=1),
                impact=EventImpact.MEDIUM,
                actual=CLAIMS_ACTUAL,
                gold_bias="bearish",
            ),
            force=True,
        )
        signals = RecentEventSignalGenerator(calendar=cal).generate_signals()
        conflict_signals = [s for s in signals if "方向冲突待复核" in s.name]
        assert len(conflict_signals) == 1
        assert conflict_signals[0].score == 0.0  # 告警不计分, 只提醒人工复核
        # 主信号方向以写入判定为准
        main = [s for s in signals if s.name.startswith("近期事件: 初请")]
        assert main and main[0].direction is SignalDirection.BEARISH
