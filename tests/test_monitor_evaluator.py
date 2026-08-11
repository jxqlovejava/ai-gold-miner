"""MonitorEvaluator 触发条件评估回归测试.

覆盖 2026-08-11 修复: 自然语言 trigger_condition 中的标识符/时间/百分比数字
不再被误当作价格阈值 (事故: L1 monitor 的 "L1" 被解析为价格 1, 误触发 945.68 ≥ 1.00)。
"""

from datetime import datetime, timedelta, timezone

from gold_miner.advisor.monitor_evaluator import (
    MonitorContext,
    MonitorEvaluator,
)
from gold_miner.data.calendar import CalendarEvent, EventImpact, EventType

TZ = timezone(timedelta(hours=-4))


def _monitor(name: str, trigger_condition: str) -> CalendarEvent:
    return CalendarEvent(
        name=name,
        event_type=EventType.MONITOR,
        scheduled_at=datetime(2026, 8, 12, 8, 30, 0, tzinfo=TZ),
        impact=EventImpact.HIGH,
        trigger_condition=trigger_condition,
        status="active",
    )


def _ctx() -> MonitorContext:
    return MonitorContext(
        gold_price=947.0,
        minsheng_price=945.68,
        xauusd=4362.81,
        oil_price=85.0,
    )


class TestEventPostEvaluationMonitor:
    """事件后评估型 monitor (数据公布后人工路由) 不应自动用价格阈值触发."""

    def test_l1_cpi_router_not_auto_triggered(self):
        """L1 引擎 CPI 三结果路由 monitor: 描述数据公布后的人工分情景评估,
        数字是情景说明不是价格阈值, 必须标记人工复核而非自动触发."""
        cond = (
            "8/12 CPI 20:30北京 公布后评估: (a)回落利好→金价站稳¥4500(积存金950)"
            "→三关齐=L1放行试盘5%首批2.5%(¥5k); (b)等回踩935-945试盘; "
            "(c)利空→L1暂停, V9低吸880/905接棒"
        )
        triggered, result = MonitorEvaluator()._evaluate(_monitor("L1", cond), _ctx())
        assert not triggered, f"事件后评估型 monitor 不应自动触发: {result}"
        assert "人工复核" in result

    def test_cpi_dip_scenario_not_auto_triggered(self):
        """CPI 分情景承接档 monitor 同样为事件后路由型."""
        cond = "8/12 CPI 20:30 公布后: 若跌破905→880承接档有效; 若站稳950→承接档概率降低"
        triggered, _ = MonitorEvaluator()._evaluate(_monitor("CPI承接档", cond), _ctx())
        assert not triggered


class TestNonPriceNumbersIgnored:
    """标识符/时间/百分比/日期数字不得被当价格阈值."""

    def test_identifier_number_L1_not_price(self):
        """协议编号 L1 中的 1 不是价格阈值."""
        # 回归测试 (修复前会误触发): "未站稳950→L1" 中修饰 950 的 "站稳" (>=) 落在
        # window 内, 原逻辑把 L1 的 "1" 解析为价格阈值 → 945.68 >= 1 恒真误触发.
        cond = "积存金未站稳950→L1放行"
        triggered, result = MonitorEvaluator()._evaluate(_monitor("t", cond), _ctx())
        assert not triggered, f"L1 标识符不应触发: {result}"
        assert "人工复核" in result or "未触发" in result

    def test_percentage_not_price(self):
        """XAUUSD 单日跌幅 >2% 的 2 是百分比不是价格, 不得以 4362>2 恒真触发."""
        cond = "美伊宣布停火 且 XAUUSD单日跌幅>2%"
        triggered, result = MonitorEvaluator()._evaluate(_monitor("t", cond), _ctx())
        assert not triggered, f"百分比数字不应作为价格触发: {result}"

    def test_time_clock_not_price(self):
        """事件时间 20:30 的 20/30 不是价格阈值."""
        cond = "CPI 20:30公布后 若跌破880则承接"
        triggered, _ = MonitorEvaluator()._evaluate(_monitor("t", cond), _ctx())
        assert not triggered


class TestPriceConditionsStillWork:
    """真实的绝对价格条件仍应自动触发."""

    def test_oil_price_above_80_triggers(self):
        cond = "油价持续>80美元"
        triggered, result = MonitorEvaluator()._evaluate(_monitor("t", cond), _ctx())
        assert triggered, f"油价 85>80 应触发: {result}"
        assert "80" in result

    def test_minsheng_price_above_920_triggers(self):
        """积存金 >920 且现价 945.68 应触发 (仅价格子句, 无人工事实部分)."""
        cond = "积存金>920"
        triggered, result = MonitorEvaluator()._evaluate(_monitor("t", cond), _ctx())
        assert triggered, f"积存金 945.68>920 应触发: {result}"

    def test_xauusd_thousand_separator(self):
        """千位分隔符 XAUUSD>$4,200 应解析为 4200 而非 4 和 100."""
        # 注意: "$>" 之间无单词边界, ">" 运算符解析失败属原有缺陷,
        # 此处验证千位合并后数字语义正确 (避免把 4,200 拆成 4 和 100 误判 4362>4).
        # 用无 $ 前缀的写法验证千位合并本身.
        cond = "XAUUSD>4,200"
        triggered, result = MonitorEvaluator()._evaluate(_monitor("t", cond), _ctx())
        assert triggered, f"XAUUSD 4362.81>4200 应触发: {result}"
