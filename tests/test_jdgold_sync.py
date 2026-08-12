"""jdgold_sync 三账本对账测试 (mock jdgold_client 数据, 隔离临时路径)."""
from __future__ import annotations

import json

import pytest

from gold_miner.data import jdgold_sync


@pytest.fixture
def sync_paths(monkeypatch, tmp_path):
    """把三账本路径 + 同步状态指到临时目录."""
    monkeypatch.setattr(jdgold_sync, "_orders_path", lambda: tmp_path / "conditional_orders.jsonl")
    monkeypatch.setattr(jdgold_sync, "_portfolio_path", lambda: tmp_path / "portfolio.yaml")
    monkeypatch.setattr(jdgold_sync, "_trade_log_path", lambda: tmp_path / "trade_log.md")
    monkeypatch.setattr(jdgold_sync, "_sync_state_path", lambda: tmp_path / "jdgold_sync_state.json")
    return tmp_path


# ── 条件单对账 ──────────────────────────────────────────────────

def test_sync_conditional_orders_upsert(sync_paths):
    """新单追加 + 已存在单更新 status, 保留本地字段."""
    orders_path = sync_paths / "conditional_orders.jsonl"
    orders_path.write_text(
        json.dumps({
            "id": "co_local_001", "status": "active", "type": "limit_buy",
            "bank": "MS", "trigger_price": 899.0, "quantity_g": 11.0,
            "note": "本地备注", "source_analysis": "2026-07-07-analysis",
        }) + "\n",
        encoding="utf-8",
    )

    jd_data = {
        "CMBC": {"datas": {"list": [
            {"status": "2", "tradeType": "1", "targetPrice": "899.0",
             "amount": "11.0"},  # 匹配已有单 → 更新 status
            {"status": "1", "tradeType": "2", "targetPrice": "950.0",
             "orderGram": "5"},  # 新单 → 追加
        ]}},
    }

    stats = jdgold_sync.sync_conditional_orders(jd_data)

    assert stats["active"] == 1  # 新加的 950 限卖 active
    lines = orders_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    by_price = {json.loads(l)["trigger_price"]: json.loads(l) for l in lines}
    # 匹配单: status 更新为 triggered, 本地字段保留
    assert by_price[899.0]["status"] == "triggered"
    assert by_price[899.0]["note"] == "本地备注"
    assert by_price[899.0]["source_analysis"] == "2026-07-07-analysis"
    # 新单: active
    assert by_price[950.0]["status"] == "active"
    assert by_price[950.0]["id"].startswith("co_jd_")


def test_sync_conditional_orders_failure_keeps_old(sync_paths):
    """数据格式错误 → 抛异常, 旧账本不变."""
    orders_path = sync_paths / "conditional_orders.jsonl"
    old = '{"id":"co_001","status":"active","type":"limit_buy","bank":"MS","trigger_price":899.0}\n'
    orders_path.write_text(old, encoding="utf-8")

    with pytest.raises(ValueError):
        jdgold_sync.sync_conditional_orders(None)

    assert orders_path.read_text(encoding="utf-8") == old


# ── 持仓对账 ────────────────────────────────────────────────────

def test_sync_holdings_updates_grams_avg_preserves_comments(sync_paths):
    """更新 grams/avg_cost, 保留行尾注释与其他字段."""
    portfolio = sync_paths / "portfolio.yaml"
    portfolio.write_text(
        "# 持仓配置\n"
        "positions:\n"
        "  gold_jd:\n"
        "    bank: MS\n"
        "    grams: 22.4586          # 旧值\n"
        "    avg_cost: 921.20        # 旧成本\n"
        "    hard_stop: 645\n"
        "    split:\n"
        "      core: 11.88\n",
        encoding="utf-8",
    )

    holdings = {"holdingList": [
        {"bankCode": "CZB", "totalGram": "5", "avgCostPrice": "900"},
        {"bankCode": "CMBC", "totalGram": "22.5", "avgCostPrice": "918.50"},
    ], "totalGramAll": "27.5", "avgCostPrice": "915"}

    stats = jdgold_sync.sync_holdings(holdings)

    assert stats == {"bank": "MS", "grams": 22.5, "avg_cost": 918.5}
    text = portfolio.read_text(encoding="utf-8")
    assert "    grams: 22.5000          # 旧值" in text
    assert "    avg_cost: 918.5000        # 旧成本" in text
    assert "    hard_stop: 645" in text
    assert "      core: 11.88" in text


def test_sync_holdings_no_cmbc_raises(sync_paths):
    """无民生记录 → 抛异常, 不动文件."""
    portfolio = sync_paths / "portfolio.yaml"
    portfolio.write_text("positions:\n  gold_jd:\n    grams: 1\n    avg_cost: 900\n", encoding="utf-8")

    with pytest.raises(ValueError, match="CMBC"):
        jdgold_sync.sync_holdings({"holdingList": [
            {"bankCode": "CZB", "totalGram": "5", "avgCostPrice": "900"},
        ]})


# ── 交易记录追记 ────────────────────────────────────────────────

def _mk_order(ts_ms: int, type_code: str = "BUY_GOLD") -> dict:
    return {
        "bizTime": str(ts_ms), "tradeTypeCode": type_code, "allAmount": "9600",
        "statusCode": "COMPLETE",
        "extInfo": json.dumps({"og": "10.01", "ogp": "959.01"}),
    }


def test_sync_trade_records_append_dedup(sync_paths):
    """首次追记全部, 二次运行 (同 bizTime) 不重复追加."""
    records = {"sum": {"BUY_GOLD": {"number": 1, "amount": 9600}},
               "list": [_mk_order(1786550538000), _mk_order(1786550539000)]}

    stats1 = jdgold_sync.sync_trade_records(records)
    assert stats1["appended"] == 2
    assert (sync_paths / "trade_log.md").exists()
    content1 = (sync_paths / "trade_log.md").read_text(encoding="utf-8")
    assert "BUY_GOLD" in content1

    # 二次运行: 无更新
    stats2 = jdgold_sync.sync_trade_records(records)
    assert stats2["appended"] == 0
    assert (sync_paths / "trade_log.md").read_text(encoding="utf-8") == content1

    # 新记录 → 只追新增
    records["list"].append(_mk_order(1786550540000, "SELL_GOLD"))
    stats3 = jdgold_sync.sync_trade_records(records)
    assert stats3["appended"] == 1


# ── run_all_sync 编排 ───────────────────────────────────────────

def test_run_all_sync_not_logged_in(monkeypatch):
    """未登录 → 返回需要登录授权, 不执行任何对账."""
    monkeypatch.setattr(
        "gold_miner.data.jdgold_client.check_login",
        lambda: (False, {"reason": "not_logged_in", "remaining_human": ""}),
    )

    report = jdgold_sync.run_all_sync()

    assert "需要登录授权" in report
    assert "not_logged_in" in report
