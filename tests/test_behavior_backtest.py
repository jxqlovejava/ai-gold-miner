"""行为回测测试."""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pytest

from gold_miner.backtest.behavior import (
    BehavioralBacktestEngine,
    BehavioralBacktestResult,
    ConversationParser,
    ConversationRecord,
    Recommendation,
    TradeComparison,
    TradeLogEntry,
    TradeLogParser,
    TradeLogTrade,
    TradingAction,
    _discipline_grade,
    _parse_date,
)


# ============================================================
# 辅助函数
# ============================================================


def _write_conversation_file(
    directory: Path,
    filename: str,
    yaml_data: str,
) -> Path:
    """写入一个模拟的对话文件."""
    directory.mkdir(parents=True, exist_ok=True)
    content = f"""# 测试对话

## 核心要点
- 测试

---

## 对话详情
测试内容

---

```yaml
{yaml_data}
```
"""
    path = directory / filename
    path.write_text(content, encoding="utf-8")
    return path


def _write_trade_log(path: Path, content: str) -> None:
    """写入模拟的 trade_log.md."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ============================================================
# _parse_date
# ============================================================


class TestParseDate:
    def test_parse_iso_string(self) -> None:
        assert _parse_date("2026-07-06") == date(2026, 7, 6)

    def test_parse_date_object(self) -> None:
        d = date(2026, 1, 15)
        assert _parse_date(d) == d

    def test_parse_invalid_returns_fallback(self) -> None:
        assert _parse_date("not-a-date") == date(1970, 1, 1)

    def test_parse_none_returns_fallback(self) -> None:
        assert _parse_date(None) == date(1970, 1, 1)


# ============================================================
# ConversationParser
# ============================================================


class TestConversationParser:
    def test_parse_valid_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            conv_dir = Path(tmpdir) / "conversations"
            yaml_content = """date: 2026-07-06
session_id: test-session-1
trading_actions:
  - date: 2026-07-02
    action: buy
    instrument: 积存金
    grams: 11.1114
    price: 899.97
    amount_cny: 9999.70
    context: 非农日加仓
recommendations:
  - text: 1万元加仓属于试探性回补
    status: executed
  - text: 未来加仓需多维度确认
    status: pending
user_followed: "yes"
outcome: pending"""
            _write_conversation_file(conv_dir, "2026-07-06.md", yaml_content)

            parser = ConversationParser(conv_dir)
            records = parser.parse_all()

            assert len(records) == 1
            r = records[0]
            assert r.date == date(2026, 7, 6)
            assert r.session_id == "test-session-1"
            assert r.user_followed == "yes"
            assert len(r.recommendations) == 2
            assert r.recommendations[0].text == "1万元加仓属于试探性回补"
            assert r.recommendations[0].status == "executed"
            assert len(r.trading_actions) == 1
            assert r.trading_actions[0].grams == 11.1114

    def test_parse_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            conv_dir = Path(tmpdir) / "empty_conversations"
            conv_dir.mkdir(parents=True, exist_ok=True)

            parser = ConversationParser(conv_dir)
            records = parser.parse_all()
            assert records == []

    def test_parse_missing_yaml_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            conv_dir = Path(tmpdir) / "conversations"
            conv_dir.mkdir(parents=True, exist_ok=True)
            # 没有 YAML 区块的文件
            (conv_dir / "2026-07-06.md").write_text(
                "# 无元数据的对话\n\n无 YAML 区块。\n",
                encoding="utf-8",
            )

            parser = ConversationParser(conv_dir)
            records = parser.parse_all()
            assert records == []  # 跳过无 YAML 的文件

    def test_parse_broken_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            conv_dir = Path(tmpdir) / "conversations"
            # 格式错误的 YAML
            _write_conversation_file(
                conv_dir, "2026-07-06.md",
                "date: [unclosed bracket\n  - bad",
            )

            parser = ConversationParser(conv_dir)
            records = parser.parse_all()
            assert records == []  # 跳过格式错误的文件

    def test_parse_nonexistent_directory(self) -> None:
        parser = ConversationParser(Path("/nonexistent/path/12345"))
        records = parser.parse_all()
        assert records == []


# ============================================================
# TradeLogParser
# ============================================================


class TestTradeLogParser:
    def test_parse_multi_row_table(self) -> None:
        content = """## 2026-06-12 — 减仓操作 #2

### 触发背景
- 测试背景

### 操作记录

| 笔数 | 成交时间 | 方向 | 数量 | 成交价 | 成交金额 |
|------|----------|------|------|--------|----------|
| 1 | 2026-06-12 | 卖出 | 5g | 911.13 | 4,555.65 |
| 2 | 2026-06-12 | 卖出 | 5g | 911.72 | 4,558.60 |
| **合计** | — | **卖出** | **10g** | **均价911.43** | **9,114.25** |
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "trade_log.md"
            _write_trade_log(path, content)

            parser = TradeLogParser(path)
            entries = parser.parse()

            assert len(entries) == 1
            e = entries[0]
            assert e.date == date(2026, 6, 12)
            assert "减仓操作" in e.operation_name
            assert len(e.trades) == 2
            assert e.trades[0].direction == "sell"
            assert e.trades[0].quantity_g == 5.0
            assert e.trades[0].price == 911.13

    def test_parse_single_row_kv_table(self) -> None:
        content = """## 2026-06-23 — 加仓操作

### 操作记录

| 字段 | 内容 |
|------|------|
| 方向 | **买入（加仓）** |
| 数量 | **55.56克** |
| 成交价 | **899.97 元/克** |
| 成交金额 | **50,000 元** |
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "trade_log.md"
            _write_trade_log(path, content)

            parser = TradeLogParser(path)
            entries = parser.parse()

            assert len(entries) == 1
            e = entries[0]
            assert len(e.trades) == 1
            assert e.trades[0].direction == "buy"
            assert e.trades[0].quantity_g == 55.56
            assert e.trades[0].price == 899.97

    def test_parse_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "nonexistent.md"
            parser = TradeLogParser(path)
            entries = parser.parse()
            assert entries == []

    def test_parse_position_tables(self) -> None:
        content = """## 2026-06-15 — 减仓操作

### 操作前持仓

| 指标 | 数值 |
|------|------|
| 持仓量 | 109.14 克 |
| 成本均价 | 1,014.42 元/克 |

### 操作后持仓

| 指标 | 数值 |
|------|------|
| 持仓量 | 104.14 克 |
| 剩余成本 | 105,639.06 元 |
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "trade_log.md"
            _write_trade_log(path, content)

            parser = TradeLogParser(path)
            entries = parser.parse()

            assert len(entries) == 1
            e = entries[0]
            assert e.pre_grams == 109.14
            assert e.pre_cost == 1014.42
            assert e.post_grams == 104.14
            assert e.post_cost == 105639.06


# ============================================================
# BehavioralBacktestEngine
# ============================================================


class TestBehavioralBacktestEngine:
    @pytest.fixture
    def engine(self) -> BehavioralBacktestEngine:
        return BehavioralBacktestEngine(initial_capital=100_000.0)

    def test_run_with_empty_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            conv_dir = Path(tmpdir) / "conversations"
            conv_dir.mkdir(parents=True, exist_ok=True)
            trade_path = Path(tmpdir) / "trade_log.md"
            trade_path.write_text("")

            engine = BehavioralBacktestEngine(
                conversations_dir=conv_dir,
                trade_log_path=trade_path,
            )
            result = engine.run()
            assert isinstance(result, BehavioralBacktestResult)
            # 空数据，大部分字段为零
            assert result.total_conversations == 0

    def test_run_with_conversations_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            conv_dir = Path(tmpdir) / "conversations"
            yaml_content = """date: 2026-07-06
recommendations:
  - text: 建议加仓 1 万元试探性回补
    status: executed
user_followed: "yes"
outcome: pending"""
            _write_conversation_file(conv_dir, "2026-07-06.md", yaml_content)

            trade_path = Path(tmpdir) / "trade_log.md"
            _write_trade_log(trade_path, "# 空日志\n")

            engine = BehavioralBacktestEngine(
                conversations_dir=conv_dir,
                trade_log_path=trade_path,
            )
            result = engine.run()

            assert result.total_conversations == 1
            assert result.total_recommendations == 1
            assert result.total_ai_trades == 1

    def test_run_with_matched_trades(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            # 对话：7月2日建议加仓
            conv_dir = Path(tmpdir) / "conversations"
            yaml_content = """date: 2026-07-02
recommendations:
  - text: 建议加仓 11.11 克
    status: executed
user_followed: "yes"
outcome: pending"""
            _write_conversation_file(conv_dir, "2026-07-02.md", yaml_content)

            # 交易日志：7月2日买入
            trade_content = """## 2026-07-02 — 加仓操作

### 操作记录

| 字段 | 内容 |
|------|------|
| 方向 | **买入（加仓）** |
| 数量 | **11.11克** |
| 成交价 | **899.97 元/克** |
"""
            trade_path = Path(tmpdir) / "trade_log.md"
            _write_trade_log(trade_path, trade_content)

            engine = BehavioralBacktestEngine(
                conversations_dir=conv_dir,
                trade_log_path=trade_path,
            )
            result = engine.run()

            assert result.total_conversations == 1
            assert result.total_recommendations == 1
            assert result.total_user_trades == 1
            # 应在 ±2 天窗口内匹配
            assert len(result.comparisons) >= 1

    def test_run_with_extra_user_trade(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            conv_dir = Path(tmpdir) / "conversations"
            conv_dir.mkdir(parents=True, exist_ok=True)

            # 仅交易，无对话
            trade_content = """## 2026-06-15 — 减仓操作

### 操作记录

| 字段 | 内容 |
|------|------|
| 方向 | **卖出（减仓）** |
| 数量 | **5克** |
| 成交价 | **941.50 元/克** |
"""
            trade_path = Path(tmpdir) / "trade_log.md"
            _write_trade_log(trade_path, trade_content)

            engine = BehavioralBacktestEngine(
                conversations_dir=conv_dir,
                trade_log_path=trade_path,
            )
            result = engine.run()

            assert result.total_conversations == 0
            assert result.total_user_trades == 1
            # 额外交易
            extra = [c for c in result.comparisons if c.match_type == "extra"]
            assert len(extra) >= 1


# ============================================================
# TradeComparison & Cross Reference
# ============================================================


class TestDirectionInference:
    def test_infer_buy(self) -> None:
        engine = BehavioralBacktestEngine()
        assert engine._infer_action_from_text("建议加仓 1 万元") == "buy"
        assert engine._infer_action_from_text("买入机会出现") == "buy"
        assert engine._infer_action_from_text("增持黄金") == "buy"

    def test_infer_sell(self) -> None:
        engine = BehavioralBacktestEngine()
        assert engine._infer_action_from_text("建议减仓至 50%") == "sell"
        assert engine._infer_action_from_text("清仓观望") == "sell"
        assert engine._infer_action_from_text("卖出 5g") == "sell"

    def test_infer_hold(self) -> None:
        engine = BehavioralBacktestEngine()
        assert engine._infer_action_from_text("建议继续持有") == "hold"
        assert engine._infer_action_from_text("不加不减，观望") == "hold"

    def test_infer_none(self) -> None:
        engine = BehavioralBacktestEngine()
        assert engine._infer_action_from_text("市场波动较大") is None
        assert engine._infer_action_from_text("") is None


class TestQuantityInference:
    def test_infer_grams(self) -> None:
        engine = BehavioralBacktestEngine()
        assert engine._infer_quantity_from_text("买入 11.11 克") == 11.11
        assert engine._infer_quantity_from_text("加仓 5g") == 5.0

    def test_infer_amount_yuan(self) -> None:
        engine = BehavioralBacktestEngine()
        assert engine._infer_quantity_from_text("加仓 1 万元") == 10000.0

    def test_infer_percentage(self) -> None:
        engine = BehavioralBacktestEngine()
        # 5% of 100,000 = 5,000
        assert engine._infer_quantity_from_text("加仓 5%") == 5000.0

    def test_infer_none(self) -> None:
        engine = BehavioralBacktestEngine()
        assert engine._infer_quantity_from_text("观望为主") is None
        assert engine._infer_quantity_from_text("") is None


# ============================================================
# 纪律评分
# ============================================================


class TestDisciplineGrade:
    def test_grade_s(self) -> None:
        assert _discipline_grade(95) == "S"
        assert _discipline_grade(90) == "S"

    def test_grade_a(self) -> None:
        assert _discipline_grade(85) == "A"
        assert _discipline_grade(80) == "A"

    def test_grade_b(self) -> None:
        assert _discipline_grade(75) == "B"
        assert _discipline_grade(70) == "B"

    def test_grade_c(self) -> None:
        assert _discipline_grade(65) == "C"

    def test_grade_d(self) -> None:
        assert _discipline_grade(50) == "D"

    def test_grade_f(self) -> None:
        assert _discipline_grade(30) == "F"
        assert _discipline_grade(0) == "F"


# ============================================================
# BehavioralBacktestResult
# ============================================================


class TestBehavioralBacktestResult:
    def test_defaults(self) -> None:
        result = BehavioralBacktestResult()
        assert result.total_conversations == 0
        assert result.compliance_rate == 0.0
        assert result.comparisons == []
        assert result.ai_equity_curve == []

    def test_fields_populated(self) -> None:
        result = BehavioralBacktestResult(
            start_date="2026-06-01",
            end_date="2026-07-01",
            total_conversations=5,
            compliance_rate=0.8,
            discipline_score=75.0,
            comparisons=[
                TradeComparison(
                    date="2026-06-15",
                    ai_action="sell",
                    actual_action="sell",
                    match_type="followed",
                )
            ],
        )
        assert result.start_date == "2026-06-01"
        assert result.compliance_rate == 0.8
        assert len(result.comparisons) == 1
        assert result.comparisons[0].match_type == "followed"
