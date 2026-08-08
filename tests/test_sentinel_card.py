"""黄金哨兵消息人话格式测试.

覆盖: symbol_cn/currency_cn 术语映射 / format_alerts 人话卡片（无 P 代码标签、保留数值）/
XAUUSD 异动补涨建议 / 日历观测事件人话化.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from gold_miner.sentinel import engine as sentinel_engine
from gold_miner.sentinel.engine import SentinelConfig, SentinelEngine
from gold_miner.sentinel.models import (
    AlertLevel,
    GoldQuote,
    PortfolioSnapshot,
    SentinelAlert,
    currency_cn,
    format_alerts,
    symbol_cn,
)

UTC = timezone.utc


def _quote(symbol: str, price: float, currency: str, chg: float, prev: float) -> GoldQuote:
    return GoldQuote(
        symbol=symbol, price=price, currency=currency,
        change_pct=chg, prev_close=prev, source="test",
        fetched_at=datetime.now(),
    )


def _portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        instrument="积存金", platform="民生银行",
        grams=12.45, avg_cost=891.0, current_price=939.81,
        market_value=11699.0, unrealized_pnl=610.0, unrealized_pnl_pct=5.5,
        hard_stop=623.0, secondary_stop=846.0,
    )


def test_symbol_cn_mapping():
    assert symbol_cn("XAUUSD") == "国际金价（XAUUSD）"
    assert symbol_cn("积存金(MS)") == "积存金（民生银行）"
    assert symbol_cn("积存金") == "积存金"
    assert symbol_cn("其他代码") == "其他代码"
    assert currency_cn("USD") == "美元"
    assert currency_cn("CNY") == "元/克"


def test_format_alerts_human_with_numbers():
    """人话卡片: 中文术语 + 完整数值 + 无 P 代码标签."""
    quotes = [
        _quote("XAUUSD", 4339.06, "USD", 2.32, 4240.55),
        _quote("积存金(MS)", 939.81, "CNY", -0.07, 940.47),
    ]
    alerts = [
        SentinelAlert(
            level=AlertLevel.P1,
            title="国际金价（XAUUSD）日内大涨 +2.32%",
            detail="当前 4339.06 美元，前收 4240.55 美元",
            suggestion="明天国内开盘大概率补涨，留意开盘价",
        ),
        SentinelAlert(
            level=AlertLevel.P2,
            title="例行观察 · 9月加息概率>75% + 积存金破860 → 重估长线逻辑",
            detail="",
        ),
    ]
    card = format_alerts(alerts, quotes, _portfolio())

    # 中文术语 + 英文码 (括号)
    assert "国际金价（XAUUSD） 4339.06 美元" in card
    assert "积存金（民生银行） 939.81 元/克" in card
    # 人话句 + 数值
    assert "较昨收 4240.55 上涨 2.32%" in card
    assert "明天国内开盘大概率补涨" in card
    assert "例行观察 · 9月加息概率>75%" in card
    # 持仓人话 + 数值
    assert "你持有 12.45 克" in card
    assert "成本均价 891 元/克" in card
    assert "浮盈 +610 元（+5.5%）" in card
    assert "止损线 846 元" in card
    assert "距止损还有 11.1%" in card
    # 人话分组标题
    assert "需要关注" in card
    assert "例行提醒" in card
    # 不含机器标签
    assert "P1 关注" not in card
    assert "P2 提醒" not in card
    assert "P0 紧急" not in card
    assert "止损距:" not in card


def test_xauusd_surge_suggestion(monkeypatch, tmp_path):
    """XAUUSD 日内大涨 → 附加"明天国内开盘大概率补涨"建议."""
    monkeypatch.setattr(sentinel_engine, "fetch_quotes", lambda: [
        _quote("XAUUSD", 4339.06, "USD", 2.32, 4240.55),
        _quote("积存金(MS)", 939.81, "CNY", -0.07, 940.47),
    ])
    cfg = SentinelConfig(
        portfolio_path=tmp_path / "none.yaml",
        orders_path=tmp_path / "orders.jsonl",
        calendar_path=tmp_path / "cal.jsonl",
    )
    result = SentinelEngine(cfg).run()
    xau = [a for a in result.alerts
           if a.level == AlertLevel.P1 and "XAUUSD" in a.title]
    assert xau
    assert "国内黄金下个交易日" in xau[0].suggestion
    assert "开盘大概率补涨" in xau[0].suggestion
    assert symbol_cn("XAUUSD") in xau[0].title


def test_xauusd_drop_suggestion(monkeypatch, tmp_path):
    """XAUUSD 日内大跌 → 附加"关注国内开盘是否补跌"建议."""
    monkeypatch.setattr(sentinel_engine, "fetch_quotes", lambda: [
        _quote("XAUUSD", 4100.00, "USD", -2.40, 4200.80),
    ])
    cfg = SentinelConfig(
        portfolio_path=tmp_path / "none.yaml",
        orders_path=tmp_path / "orders.jsonl",
        calendar_path=tmp_path / "cal.jsonl",
    )
    result = SentinelEngine(cfg).run()
    xau = [a for a in result.alerts if "XAUUSD" in a.title]
    assert xau
    assert "国内黄金下个交易日" in xau[0].suggestion
    assert "开盘大概率补跌" in xau[0].suggestion


def test_calendar_observation_humanized(tmp_path):
    """日历"观测:"事件 → 标题"🔍 ...", 带人话解释, 不带 📅 即将 前缀."""
    sat = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    (tmp_path / "cal.jsonl").write_text(json.dumps({
        "name": "观测: 9月加息概率>75% + 积存金破860 → 重估长线逻辑",
        "scheduled_at": sat,
        "impact": "high",
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    cfg = SentinelConfig(calendar_path=tmp_path / "cal.jsonl")
    alerts = SentinelEngine(cfg)._check_calendar()
    assert len(alerts) == 1
    assert alerts[0].title.startswith("🔍 ")
    assert "9月加息概率>75%" in alerts[0].title
    assert "📅 即将" not in alerts[0].title
    assert "美联储加息监控" in alerts[0].detail  # 人话解释已挂上
