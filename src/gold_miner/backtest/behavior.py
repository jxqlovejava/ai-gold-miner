"""行为回测引擎 — AI 建议 vs 用户实际操作对比分析."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from gold_miner.config import settings


# ============================================================
# 输入数据类
# ============================================================


@dataclass
class TradingAction:
    """从对话 YAML 解析的单笔交易操作."""

    date: date
    action: str  # buy / sell
    instrument: str
    grams: float
    price: float
    amount_cny: float
    context: str = ""


@dataclass
class Recommendation:
    """从对话 YAML 解析的 AI 建议."""

    text: str
    status: str = "pending"  # executed / pending / rejected


@dataclass
class ConversationRecord:
    """单日对话记录 (一个文件)."""

    date: date
    session_id: str = "unknown"
    trading_actions: list[TradingAction] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    user_followed: str = "unknown"  # yes / no / partial / unknown
    outcome: str = "pending"  # gain / loss / breakeven / pending


@dataclass
class TradeLogTrade:
    """从 trade_log.md 解析的单笔交易."""

    time: str = ""  # 日期字符串或 "盘中"
    direction: str = ""  # buy / sell
    quantity_g: float = 0.0
    price: float = 0.0
    amount_cny: float = 0.0
    realized_pnl: float = 0.0


@dataclass
class TradeLogEntry:
    """trade_log.md 的一条操作记录."""

    date: date
    operation_name: str = ""
    trades: list[TradeLogTrade] = field(default_factory=list)
    pre_grams: float | None = None
    pre_cost: float | None = None
    post_grams: float | None = None
    post_cost: float | None = None


# ============================================================
# 输出数据类
# ============================================================


@dataclass
class TradeComparison:
    """AI 建议与用户实际操作的逐笔对比."""

    date: str = ""
    ai_action: str | None = None  # buy / sell / hold / None
    ai_quantity_g: float | None = None
    ai_note: str = ""
    actual_action: str | None = None
    actual_quantity_g: float | None = None
    match_type: str = "unmatched"  # followed / partial / missed / extra / unmatched
    pnl_diff_pct: float = 0.0


@dataclass
class BehavioralBacktestResult:
    """行为回测结果 — AI 建议 vs 用户实际."""

    # 时间区间
    start_date: str = ""
    end_date: str = ""
    total_days: int = 0
    initial_capital: float = 0.0

    # 数据统计
    total_conversations: int = 0
    total_recommendations: int = 0
    total_user_trades: int = 0
    total_ai_trades: int = 0

    # 纪律指标
    compliance_rate: float = 0.0  # 遵守率 (0~1)
    discipline_score: float = 0.0  # 纪律综合评分 (0~100)
    follow_trades: int = 0
    deviate_trades: int = 0
    missed_trades: int = 0
    extra_trades: int = 0
    direction_consistency: float = 0.0  # 方向一致性 (0~1)
    position_match_pct: float = 0.0  # 仓位匹配度 (0~1)

    # 收益对比
    ai_cumulative_return_pct: float = 0.0
    actual_cumulative_return_pct: float = 0.0
    deviation_cost_pct: float = 0.0  # actual - ai (负值即偏离损失)
    deviation_cost_abs: float = 0.0

    # 详细记录
    comparisons: list[TradeComparison] = field(default_factory=list)
    ai_equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    actual_equity_curve: list[tuple[datetime, float]] = field(default_factory=list)


# ============================================================
# 辅助函数
# ============================================================


def _parse_date(raw: Any) -> date:
    """安全解析日期."""
    if isinstance(raw, date):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, str):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                return datetime.strptime(raw.strip(), fmt).date()
            except ValueError:
                continue
    return date(1970, 1, 1)


# ============================================================
# ConversationParser
# ============================================================


class ConversationParser:
    """解析 data/private/conversations/YYYY-MM-DD.md 文件."""

    def __init__(self, conversations_dir: Path | None = None) -> None:
        self.conversations_dir = conversations_dir or settings.private_data_path / "conversations"

    def parse_all(self) -> list[ConversationRecord]:
        """扫描目录, 解析所有对话文件."""
        if not self.conversations_dir.exists():
            logger.warning(f"对话目录不存在: {self.conversations_dir}")
            return []

        records: list[ConversationRecord] = []
        pattern = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md"
        for f in sorted(self.conversations_dir.glob(pattern)):
            record = self._parse_file(f)
            if record:
                records.append(record)
        logger.info(f"已加载 {len(records)} 条对话记录")
        return records

    def _parse_file(self, path: Path) -> ConversationRecord | None:
        """解析单个文件, 提取 YAML 元数据."""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            logger.warning(f"无法读取对话文件: {path}")
            return None

        # 提取 ```yaml ... ``` 元数据区块
        match = re.search(r"```yaml\s*\n(.+?)\n```", text, re.DOTALL)
        if not match:
            logger.warning(f"对话文件无 YAML 元数据: {path.name}")
            return None

        yaml_text = match.group(1)
        try:
            data = yaml.safe_load(yaml_text)
        except yaml.YAMLError:
            logger.warning(f"YAML 解析失败: {path.name}")
            return None

        if not isinstance(data, dict):
            return None

        conv_date = _parse_date(data.get("date", path.stem))

        # 解析 trading_actions
        actions: list[TradingAction] = []
        for ta in data.get("trading_actions", []) or []:
            if not isinstance(ta, dict):
                continue
            actions.append(TradingAction(
                date=_parse_date(ta.get("date", conv_date)),
                action=str(ta.get("action", "")),
                instrument=str(ta.get("instrument", "")),
                grams=float(ta.get("grams", 0)),
                price=float(ta.get("price", 0)),
                amount_cny=float(ta.get("amount_cny", 0)),
                context=str(ta.get("context", "")),
            ))

        # 解析 recommendations
        recs: list[Recommendation] = []
        for r in data.get("recommendations", []) or []:
            if not isinstance(r, dict):
                continue
            recs.append(Recommendation(
                text=str(r.get("text", "")),
                status=str(r.get("status", "pending")),
            ))

        return ConversationRecord(
            date=conv_date,
            session_id=str(data.get("session_id", "unknown")),
            trading_actions=actions,
            recommendations=recs,
            user_followed=str(data.get("user_followed", "unknown")),
            outcome=str(data.get("outcome", "pending")),
        )


# ============================================================
# TradeLogParser
# ============================================================


class TradeLogParser:
    """解析 data/private/trade_log.md 为结构化记录."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.private_data_path / "trade_log.md"

    def parse(self) -> list[TradeLogEntry]:
        """解析 trade_log.md, 返回所有操作记录."""
        if not self.path.exists():
            logger.warning(f"交易日志不存在: {self.path}")
            return []

        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            logger.warning(f"无法读取交易日志: {self.path}")
            return []

        entries = self._split_sections(text)
        logger.info(f"已加载 {len(entries)} 条交易记录")
        return entries

    def _split_sections(self, text: str) -> list[TradeLogEntry]:
        """按 --- 分隔符切分, 提取每个操作节."""
        sections = re.split(r"\n---+\s*\n", text)
        entries: list[TradeLogEntry] = []
        for section in sections:
            section = section.strip()
            if not section:
                continue
            entry = self._parse_section(section)
            if entry:
                entries.append(entry)
        return entries

    def _parse_section(self, text: str) -> TradeLogEntry | None:
        """解析单个操作节."""
        # 提取标题: ## YYYY-MM-DD — 操作名称
        header_match = re.search(
            r"^##\s+(\d{4}-\d{2}-\d{2})\s*[—\-–]\s*(.+)$",
            text, re.MULTILINE,
        )
        if not header_match:
            return None

        op_date = _parse_date(header_match.group(1))
        op_name = header_match.group(2).strip()

        trades = self._extract_trades(text)
        pre_grams, pre_cost = self._extract_pre_position(text)
        post_grams, post_cost = self._extract_post_position(text)

        return TradeLogEntry(
            date=op_date,
            operation_name=op_name,
            trades=trades,
            pre_grams=pre_grams,
            pre_cost=pre_cost,
            post_grams=post_grams,
            post_cost=post_cost,
        )

    def _extract_trades(self, text: str) -> list[TradeLogTrade]:
        """从操作节中提取交易明细.

        支持两种表格格式:
        - 多笔格式: | 笔数 | 时间 | 方向 | 数量 | 成交价 | 成交金额 |
        - 单笔格式: | 字段 | 内容 |  (键值对)
        """
        trades: list[TradeLogTrade] = []

        # 格式 A: 多笔交易表
        multi_pattern = (
            r"^\|\s*\d+\s*\|\s*([\d\-]+(?:\s+[\d:]+)?)\s*\|"
            r"\s*(卖出|买入|买|卖)\s*\|"
            r"\s*([\d.]+)\s*g?\s*\|"
            r"\s*\*{0,2}([\d.,]+)\*{0,2}\s*\|"
            r"\s*\*{0,2}([\d,]+)\*{0,2}"
        )
        multi_rows = re.findall(multi_pattern, text, re.MULTILINE)
        for row in multi_rows:
            direction = "sell" if "卖" in row[1] else "buy"
            qty = float(row[2])
            price_val = float(row[3].replace(",", ""))
            amount = float(row[4].replace(",", ""))
            trades.append(TradeLogTrade(
                time=row[0].strip(),
                direction=direction,
                quantity_g=qty,
                price=price_val,
                amount_cny=amount,
            ))

        if trades:
            return trades

        # 格式 B: 单笔键值对表
        kv_rows = re.findall(
            r"^\|\s*(.+?)\s*\|\s*\*{0,2}(.+?)\*{0,2}\s*\|",
            text, re.MULTILINE,
        )
        if not kv_rows:
            return trades

        direction = ""
        quantity = 0.0
        price_val = 0.0
        amount = 0.0

        for key, value in kv_rows:
            key_clean = key.strip()
            val_clean = re.sub(r"\*+", "", value).strip()
            if key_clean in ("方向",):
                direction = "sell" if "卖" in val_clean else ("buy" if "买" in val_clean else "")
            elif key_clean in ("数量",):
                m = re.search(r"([\d.]+)", val_clean)
                if m:
                    quantity = float(m.group(1))
            elif key_clean in ("成交价", "条件价"):
                m = re.search(r"([\d.]+)", val_clean)
                if m:
                    price_val = float(m.group(1))
            elif key_clean in ("成交金额", "预计回收"):
                m = re.search(r"([\d.,]+)", val_clean)
                if m:
                    amount = float(m.group(1).replace(",", ""))

        if direction and quantity > 0:
            trades.append(TradeLogTrade(
                time="",
                direction=direction,
                quantity_g=quantity,
                price=price_val,
                amount_cny=amount or quantity * price_val,
            ))

        return trades

    @staticmethod
    def _extract_pre_position(text: str) -> tuple[float | None, float | None]:
        """提取操作前持仓 (克数, 成本均价)."""
        block_match = re.search(
            r"操作前持仓.*?\n\n?((?:\|.*(?:\n|$))+)",
            text, re.DOTALL,
        )
        if not block_match:
            return None, None
        block = block_match.group(1)
        grams = TradeLogParser._extract_table_value(block, "持仓量")
        cost = TradeLogParser._extract_table_value(block, "成本均价")
        return grams, cost

    @staticmethod
    def _extract_post_position(text: str) -> tuple[float | None, float | None]:
        """提取操作后持仓 (克数, 成本均价)."""
        block_match = re.search(
            r"操作后持仓.*?\n\n?((?:\|.*(?:\n|$))+)",
            text, re.DOTALL,
        )
        if not block_match:
            return None, None
        block = block_match.group(1)
        grams = TradeLogParser._extract_table_value(block, "持仓量")
        cost = TradeLogParser._extract_table_value(block, "剩余成本")
        return grams, cost

    @staticmethod
    def _extract_table_value(text: str, field: str) -> float | None:
        """从键值对表格中提取数值."""
        for line in text.split("\n"):
            if field in line:
                m = re.search(r"([\d.,]+)", line)
                if m:
                    return float(m.group(1).replace(",", ""))
        return None


# ============================================================
# BehavioralBacktestEngine
# ============================================================


class BehavioralBacktestEngine:
    """行为回测引擎 — 对比 AI 建议 vs 用户实际操作."""

    def __init__(
        self,
        conversations_dir: Path | None = None,
        trade_log_path: Path | None = None,
        initial_capital: float = 100_000.0,
    ) -> None:
        self.conversation_parser = ConversationParser(conversations_dir)
        self.trade_log_parser = TradeLogParser(trade_log_path)
        self.initial_capital = initial_capital

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def run(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> BehavioralBacktestResult:
        """执行行为回测, 返回对比结果."""
        # 1. 加载数据
        conversations = self.conversation_parser.parse_all()
        trade_entries = self.trade_log_parser.parse()

        if not conversations and not trade_entries:
            logger.warning("无对话记录或交易记录")
            return BehavioralBacktestResult()

        # 2. 日期范围过滤
        conversations = self._filter_conversations(conversations, start_date, end_date)
        trade_entries = self._filter_trade_entries(trade_entries, start_date, end_date)

        if not conversations and not trade_entries:
            logger.warning("指定范围内无对话或交易记录")
            return BehavioralBacktestResult()

        # 3. 交叉引用: AI 建议 ↔ 实际交易
        comparisons = self._cross_reference(conversations, trade_entries)

        # 4. 模拟两条路径
        ai_curve = self._simulate_ai_path(conversations, comparisons)
        actual_curve = self._simulate_actual_path(trade_entries)

        # 5. 计算指标
        return self._calculate_metrics(
            conversations, trade_entries, comparisons,
            ai_curve, actual_curve,
            start_date, end_date,
        )

    # ------------------------------------------------------------------
    # 过滤
    # ------------------------------------------------------------------

    @staticmethod
    def _filter_conversations(
        conversations: list[ConversationRecord],
        start: str | None,
        end: str | None,
    ) -> list[ConversationRecord]:
        if not start and not end:
            return conversations
        start_dt = _parse_date(start) if start else None
        end_dt = _parse_date(end) if end else None
        result: list[ConversationRecord] = []
        for c in conversations:
            if start_dt and c.date < start_dt:
                continue
            if end_dt and c.date > end_dt:
                continue
            result.append(c)
        return result

    @staticmethod
    def _filter_trade_entries(
        entries: list[TradeLogEntry],
        start: str | None,
        end: str | None,
    ) -> list[TradeLogEntry]:
        if not start and not end:
            return entries
        start_dt = _parse_date(start) if start else None
        end_dt = _parse_date(end) if end else None
        result: list[TradeLogEntry] = []
        for e in entries:
            if start_dt and e.date < start_dt:
                continue
            if end_dt and e.date > end_dt:
                continue
            result.append(e)
        return result

    # ------------------------------------------------------------------
    # 交叉引用
    # ------------------------------------------------------------------

    def _cross_reference(
        self,
        conversations: list[ConversationRecord],
        trade_entries: list[TradeLogEntry],
    ) -> list[TradeComparison]:
        """将 AI 建议与用户交易进行匹配."""
        comparisons: list[TradeComparison] = []
        matched_entry_indices: set[int] = set()
        trade_sorted = sorted(trade_entries, key=lambda e: e.date)

        for conv in sorted(conversations, key=lambda c: c.date):
            for rec in conv.recommendations:
                ai_action = self._infer_action_from_text(rec.text)
                ai_qty = self._infer_quantity_from_text(rec.text)

                best_idx = self._find_matching_trade(
                    rec, conv.date, trade_sorted, matched_entry_indices,
                )

                if best_idx is not None:
                    matched = trade_sorted[best_idx]
                    matched_entry_indices.add(best_idx)
                    total_user_qty = sum(t.quantity_g for t in matched.trades)
                    actual_action = matched.trades[0].direction if matched.trades else None

                    # 分类匹配程度
                    if ai_qty is None:
                        match_type = "followed"
                    elif ai_qty > 0 and total_user_qty > 0:
                        ratio = abs(total_user_qty - ai_qty) / max(ai_qty, 0.001)
                        match_type = "followed" if ratio < 0.3 else "partial"
                    elif ai_action and actual_action and ai_action == actual_action:
                        match_type = "followed"
                    else:
                        match_type = "partial"

                    comparisons.append(TradeComparison(
                        date=str(conv.date),
                        ai_action=ai_action,
                        ai_quantity_g=ai_qty,
                        ai_note=rec.text,
                        actual_action=actual_action,
                        actual_quantity_g=total_user_qty or None,
                        match_type=match_type,
                    ))
                else:
                    # 无匹配交易
                    match_type = "followed" if rec.status == "executed" else "missed"
                    comparisons.append(TradeComparison(
                        date=str(conv.date),
                        ai_action=ai_action,
                        ai_quantity_g=ai_qty,
                        ai_note=rec.text,
                        actual_action=None,
                        actual_quantity_g=None,
                        match_type=match_type,
                    ))

        # 额外交易 (用户执行但未匹配到 AI 建议)
        for idx, entry in enumerate(trade_sorted):
            if idx not in matched_entry_indices:
                total_qty = sum(t.quantity_g for t in entry.trades)
                comparisons.append(TradeComparison(
                    date=str(entry.date),
                    ai_action=None,
                    ai_quantity_g=None,
                    ai_note="",
                    actual_action=entry.trades[0].direction if entry.trades else None,
                    actual_quantity_g=total_qty or None,
                    match_type="extra",
                ))

        comparisons.sort(key=lambda c: c.date)
        return comparisons

    @staticmethod
    def _infer_action_from_text(text: str) -> str | None:
        """从中文建议文本推断操作方向."""
        if not text:
            return None
        text_lower = text.lower()
        if any(w in text_lower for w in ["加仓", "买入", "增持", "buy"]):
            return "buy"
        if any(w in text_lower for w in ["减仓", "卖出", "减持", "清仓", "平仓", "sell"]):
            return "sell"
        if any(w in text_lower for w in ["持有", "观望", "hold", "不动", "不加不减"]):
            return "hold"
        return None

    def _infer_quantity_from_text(self, text: str) -> float | None:
        """从建议文本中推断数量 (克 或 元)."""
        if not text:
            return None
        # 万元 → 金额
        m = re.search(r"(\d+)\s*万", text)
        if m:
            return float(m.group(1)) * 10_000
        # "约 x 元"
        m = re.search(r"约\s*([\d.,]+)\s*元", text)
        if m:
            return float(m.group(1).replace(",", ""))
        # 克数: "55.56g" / "55.56克" / "5g" / "11.1114克"
        m = re.search(r"([\d.]+)\s*[g克]", text)
        if m:
            return float(m.group(1))
        # 百分比: "5%"
        m = re.search(r"(\d+\.?\d*)\s*%", text)
        if m:
            return self.initial_capital * float(m.group(1)) / 100
        return None

    @staticmethod
    def _find_matching_trade(
        rec: Recommendation,
        conv_date: date,
        trade_entries: list[TradeLogEntry],
        matched_indices: set[int],
    ) -> int | None:
        """在 conv_date ±2 天内查找匹配的用户交易."""
        window_start = conv_date - timedelta(days=2)
        window_end = conv_date + timedelta(days=2)

        for idx, entry in enumerate(trade_entries):
            if idx in matched_indices:
                continue
            if entry.date < window_start or entry.date > window_end:
                continue
            # 如果有方向信息, 用方向过滤
            return idx  # 先按日期匹配, 返回第一个窗口内的交易

        return None

    # ------------------------------------------------------------------
    # 权益曲线模拟
    # ------------------------------------------------------------------

    def _simulate_ai_path(
        self,
        conversations: list[ConversationRecord],
        comparisons: list[TradeComparison],
    ) -> list[tuple[datetime, float]]:
        """模拟完全按 AI 建议操作的理论权益曲线."""
        capital = self.initial_capital
        position_g = 0.0

        initial_dt = datetime.combine(
            min(c.date for c in conversations) if conversations else date.today(),
            datetime.min.time(),
        )
        curve: list[tuple[datetime, float]] = [(initial_dt, capital)]

        for comp in sorted(comparisons, key=lambda c: c.date):
            if comp.match_type == "extra":
                continue  # AI 未建议, 不计入 AI 路径

            trade_dt = _parse_date(comp.date)
            trade_datetime = datetime.combine(trade_dt, datetime.min.time())

            if comp.ai_action == "buy" and comp.ai_quantity_g:
                qty = comp.ai_quantity_g
                cost = qty * (comp.actual_quantity_g and comp.ai_quantity_g
                              and comp.actual_quantity_g / max(comp.ai_quantity_g, 0.001)
                              or 0)
                # 使用对话中的价格或 estimate
                price_est = self._estimate_price_from_text(comp.ai_note)
                if price_est and price_est > 0:
                    cost = qty * price_est
                else:
                    cost = qty * 900  # fallback

                if cost <= capital:
                    position_g += qty
                    capital -= cost

            elif comp.ai_action == "sell" and comp.ai_quantity_g:
                sell_qty = min(position_g, comp.ai_quantity_g)
                price_est = self._estimate_price_from_text(comp.ai_note) or 900
                capital += sell_qty * price_est
                position_g -= sell_qty

            # 估值: 按成本基准估算
            equity = capital + position_g * 900
            curve.append((trade_datetime, max(equity, 1.0)))

        return curve

    def _simulate_actual_path(
        self,
        trade_entries: list[TradeLogEntry],
    ) -> list[tuple[datetime, float]]:
        """模拟用户实际操作路径的权益曲线."""
        capital = self.initial_capital
        position_g = 0.0

        initial_dt = datetime.combine(
            min(e.date for e in trade_entries) if trade_entries else date.today(),
            datetime.min.time(),
        )
        curve: list[tuple[datetime, float]] = [(initial_dt, capital)]

        for entry in sorted(trade_entries, key=lambda e: e.date):
            for trade in entry.trades:
                price = trade.price if trade.price > 0 else 900
                if trade.direction == "buy":
                    position_g += trade.quantity_g
                    capital -= trade.quantity_g * price
                elif trade.direction == "sell":
                    sell_qty = min(position_g, trade.quantity_g)
                    capital += sell_qty * price
                    position_g -= sell_qty

            dt = datetime.combine(entry.date, datetime.min.time())
            equity = capital + position_g * 900
            curve.append((dt, max(equity, 1.0)))

        return curve

    @staticmethod
    def _estimate_price_from_text(text: str) -> float | None:
        """从文本中提取价格信息."""
        # "均价913.67" / "941.50元/克"
        m = re.search(r"([\d.]+)\s*元\s*/?\s*克", text)
        if m:
            return float(m.group(1))
        m = re.search(r"均价\s*([\d.]+)", text)
        if m:
            return float(m.group(1))
        return None

    # ------------------------------------------------------------------
    # 指标计算
    # ------------------------------------------------------------------

    def _calculate_metrics(
        self,
        conversations: list[ConversationRecord],
        trade_entries: list[TradeLogEntry],
        comparisons: list[TradeComparison],
        ai_curve: list[tuple[datetime, float]],
        actual_curve: list[tuple[datetime, float]],
        start_date: str | None,
        end_date: str | None,
    ) -> BehavioralBacktestResult:
        """计算所有行为回测指标."""

        # 时间范围
        all_dates = [
            _parse_date(c.date) for c in comparisons
            if c.date and _parse_date(c.date) != date(1970, 1, 1)
        ]
        start = min(all_dates) if all_dates else date.today()
        end = max(all_dates) if all_dates else date.today()
        total_days = (end - start).days

        # 分类统计
        followed = [c for c in comparisons if c.match_type == "followed"]
        partial = [c for c in comparisons if c.match_type == "partial"]
        missed = [c for c in comparisons if c.match_type == "missed"]
        extra = [c for c in comparisons if c.match_type == "extra"]

        # 合规率
        rateable = [c for c in comparisons if c.match_type in ("followed", "partial", "missed")]
        compliance_rate = (
            (len(followed) + 0.5 * len(partial)) / len(rateable)
            if rateable else 0.0
        )

        # 方向一致性
        dir_pairs = [c for c in comparisons if c.ai_action and c.actual_action]
        dir_score = (
            sum(1 for c in dir_pairs if c.ai_action == c.actual_action) / len(dir_pairs) * 100
            if dir_pairs else 0.0
        )

        # 仓位匹配度
        qty_pairs = [
            c for c in comparisons
            if c.ai_quantity_g and c.actual_quantity_g and c.ai_quantity_g > 0
        ]
        pos_score = (
            sum(
                1 for c in qty_pairs
                if abs(c.ai_quantity_g - c.actual_quantity_g) / c.ai_quantity_g < 0.3
            ) / len(qty_pairs) * 100
            if qty_pairs else 0.0
        )

        # 纪律评分: 执行率 40% + 方向一致性 30% + 仓位匹配 30%
        exec_score = compliance_rate * 100
        discipline = exec_score * 0.4 + dir_score * 0.3 + pos_score * 0.3

        # 收益对比
        ai_final = ai_curve[-1][1] if len(ai_curve) > 1 else self.initial_capital
        actual_final = actual_curve[-1][1] if len(actual_curve) > 1 else self.initial_capital
        ai_return = (ai_final - self.initial_capital) / self.initial_capital
        actual_return = (actual_final - self.initial_capital) / self.initial_capital
        deviation = actual_return - ai_return

        # 用户交易总数
        total_user_trades = sum(len(e.trades) for e in trade_entries)

        return BehavioralBacktestResult(
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            total_days=total_days,
            initial_capital=self.initial_capital,
            total_conversations=len(conversations),
            total_recommendations=sum(len(c.recommendations) for c in conversations),
            total_user_trades=total_user_trades,
            total_ai_trades=len(rateable),
            compliance_rate=round(compliance_rate, 4),
            discipline_score=round(discipline, 1),
            follow_trades=len(followed),
            deviate_trades=len(partial),
            missed_trades=len(missed),
            extra_trades=len(extra),
            direction_consistency=round(dir_score / 100, 4),
            position_match_pct=round(pos_score / 100, 4),
            ai_cumulative_return_pct=round(ai_return * 100, 4),
            actual_cumulative_return_pct=round(actual_return * 100, 4),
            deviation_cost_pct=round(deviation * 100, 4),
            deviation_cost_abs=round(deviation * self.initial_capital, 2),
            comparisons=comparisons,
            ai_equity_curve=ai_curve,
            actual_equity_curve=actual_curve,
        )


# ============================================================
# 报告输出
# ============================================================


def print_behavioral_report(result: BehavioralBacktestResult) -> None:
    """打印格式化的行为回测报告."""
    print()
    print("=" * 58)
    print("               行为回测报告")
    print("=" * 58)

    # 概览
    print(f"  期间:          {result.start_date} ~ {result.end_date} "
          f"({result.total_days}天)")
    print(f"  初始资金:      {result.initial_capital:>12,.2f}")
    print(f"  对话次数:      {result.total_conversations:>12}")
    print(f"  AI 建议数:     {result.total_recommendations:>12}")
    print(f"  用户交易数:    {result.total_user_trades:>12}")

    # 纪律评估
    print(f"\n  --- 纪律评估 ---")
    rateable = result.total_ai_trades
    print(f"  遵守率:        {result.compliance_rate:>11.1%}  "
          f"({result.follow_trades}/{rateable})")
    print(f"  跟随交易:      {result.follow_trades:>12}")
    print(f"  偏离交易:      {result.deviate_trades:>12}")
    print(f"  遗漏建议:      {result.missed_trades:>12}")
    print(f"  额外交易:      {result.extra_trades:>12}")

    # 收益对比
    print(f"\n  --- 收益对比 ---")
    ai_str = f"{result.ai_cumulative_return_pct:+.2f}%"
    actual_str = f"{result.actual_cumulative_return_pct:+.2f}%"
    dev_str = f"{result.deviation_cost_pct:+.2f}%"
    print(f"  完全按建议:    {ai_str:>11}")
    print(f"  实际操作:      {actual_str:>11}")
    print(f"  偏离成本:      {dev_str:>11}  (¥{result.deviation_cost_abs:+,.2f})")

    # 纪律评分
    print(f"\n  --- 纪律评分 ---")
    grade = _discipline_grade(result.discipline_score)
    print(f"  总分:          {grade} ({result.discipline_score:.0f}/100)")
    print(f"  - 建议执行率:  {result.compliance_rate:.0%}")
    print(f"  - 方向一致性:  {result.direction_consistency:.0%}")
    print(f"  - 仓位匹配度:  {result.position_match_pct:.0%}")

    # 逐笔对比
    if result.comparisons:
        print(f"\n  --- 逐笔对比 ---")
        header = f"  {'日期':<12} {'AI建议':<8} {'用户操作':<8} {'匹配':<6} {'AI量(g)':<9} {'实际量(g)':<9}"
        print(header)
        print(f"  {'-'*12} {'-'*8} {'-'*8} {'-'*6} {'-'*9} {'-'*9}")
        for c in result.comparisons:
            ai_label = c.ai_action or "--"
            actual_label = c.actual_action or "--"
            match_symbol = {
                "followed": "✓", "partial": "~", "missed": "✗",
                "extra": "!", "unmatched": "?",
            }.get(c.match_type, "?")
            ai_qty_str = f"{c.ai_quantity_g:.1f}" if c.ai_quantity_g else "--"
            act_qty_str = f"{c.actual_quantity_g:.1f}" if c.actual_quantity_g else "--"
            print(f"  {c.date:<12} {ai_label:<8} {actual_label:<8} "
                  f"{match_symbol:<6} {ai_qty_str:<9} {act_qty_str:<9}")

    # 匹配图例
    print(f"\n  匹配图例: ✓=遵守  ~=部分偏离  ✗=遗漏  !=额外交易")
    print("=" * 58)


def _discipline_grade(score: float) -> str:
    """纪律评分等级."""
    if score >= 90:
        return "S"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def save_behavioral_report(result: BehavioralBacktestResult, output_path: str) -> None:
    """保存行为回测结果为 JSON."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {
        "start_date": result.start_date,
        "end_date": result.end_date,
        "total_days": result.total_days,
        "initial_capital": result.initial_capital,
        "total_conversations": result.total_conversations,
        "total_recommendations": result.total_recommendations,
        "total_user_trades": result.total_user_trades,
        "total_ai_trades": result.total_ai_trades,
        "compliance_rate": result.compliance_rate,
        "discipline_score": result.discipline_score,
        "follow_trades": result.follow_trades,
        "deviate_trades": result.deviate_trades,
        "missed_trades": result.missed_trades,
        "extra_trades": result.extra_trades,
        "direction_consistency": result.direction_consistency,
        "position_match_pct": result.position_match_pct,
        "ai_cumulative_return_pct": result.ai_cumulative_return_pct,
        "actual_cumulative_return_pct": result.actual_cumulative_return_pct,
        "deviation_cost_pct": result.deviation_cost_pct,
        "deviation_cost_abs": result.deviation_cost_abs,
        "comparisons": [asdict(c) for c in result.comparisons],
        "ai_equity_curve": [
            [ts.isoformat(), eq] for ts, eq in result.ai_equity_curve
        ],
        "actual_equity_curve": [
            [ts.isoformat(), eq] for ts, eq in result.actual_equity_curve
        ],
    }

    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    logger.info(f"行为回测报告已保存至: {path}")
