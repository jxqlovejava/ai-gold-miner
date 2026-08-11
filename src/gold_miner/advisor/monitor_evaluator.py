"""Active monitor 触发条件评估器.

把自然语言 trigger_condition 中的可量化阈值与当前市场数据对比，
对已触发的 monitor 自动调用 calendar.close_monitor() 并生成结果文本。
无法量化或缺少数据的条件标记为“需人工复核”。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from loguru import logger

from gold_miner.data.calendar import CalendarEvent, EventCalendar


@dataclass
class MonitorContext:
    """评估 monitor 所需当前市场数据."""

    gold_price: float | None = None          # 国内 Au99.99 人民币/克
    minsheng_price: float | None = None      # 民生积存金 人民币/克
    xauusd: float | None = None              # 国际现货黄金 美元/盎司
    oil_price: float | None = None           # Brent/WTI 美元/桶（可选）


class MonitorEvaluator:
    """评估 active monitor 的 trigger_condition 是否已满足."""

    def __init__(self, calendar: EventCalendar | None = None) -> None:
        self.calendar = calendar or EventCalendar()

    def evaluate_and_close(
        self,
        monitors: list[CalendarEvent],
        ctx: MonitorContext,
    ) -> list[tuple[CalendarEvent, str]]:
        """评估 monitors，触发则关闭并返回被关闭的列表."""
        closed: list[tuple[CalendarEvent, str]] = []
        for monitor in monitors:
            triggered, result = self._evaluate(monitor, ctx)
            if triggered and result and self.calendar.close_monitor(monitor.name, result):
                closed.append((monitor, result))
                logger.debug(f"[Monitor] {monitor.name} 触发并关闭: {result}")
        return closed

    def _evaluate(
        self,
        monitor: CalendarEvent,
        ctx: MonitorContext,
    ) -> tuple[bool, str]:
        condition = (monitor.trigger_condition or "").strip()
        if not condition:
            return False, "无条件"

        # 事件后评估型 monitor: trigger_condition 描述"数据公布后的人工路由/分情景评估"
        # (如 CPI 公布后按回落/持平/过热三情景路由)。这类条件没有可自动量化的价格阈值，
        # 数字只是情景说明，强制规则引擎解析会把标识符/时间/百分比误当价格 (如 "L1"→1)。
        # 标记为"需人工复核"，等数据公布后由分析路由，绝不自动 close。
        if re.search(r"(?:公布后|待评估|评估后|数据后|数据公布)", condition):
            return False, f"需人工复核: {condition[:80]}..."

        # 顶层按“或”拆分
        or_clauses = re.split(r"(?:或|or|OR)\s*", condition)
        any_manual = False

        for clause in or_clauses:
            triggered, result, manual = self._evaluate_clause(clause.strip(), ctx)
            if manual:
                any_manual = True
            if triggered:
                return True, result

        if any_manual:
            return False, f"需人工复核: {condition}"
        return False, "未触发"

    def _evaluate_clause(
        self,
        clause: str,
        ctx: MonitorContext,
    ) -> tuple[bool, str, bool]:
        """评估一个“且”子句."""
        and_parts = re.split(r"(?:且|and|AND)\s*", clause)
        clause_results: list[str] = []
        any_manual = False

        evaluated = 0
        for part in and_parts:
            part = part.strip()
            if not part:
                continue

            price, label = self._price_for_clause(part, ctx)
            if price is None:
                any_manual = True
                continue

            triggered, result = self._evaluate_part(part, price, label)
            evaluated += 1
            if triggered:
                clause_results.append(result)
            elif result == "manual":
                any_manual = True
            else:
                # 有一个 and 部分明确未触发，整个 clause 不触发
                return False, "", any_manual

        if clause_results and evaluated > 0 and not any_manual:
            return True, "; ".join(clause_results), False
        if any_manual:
            return False, "", True
        return False, "", False

    def _evaluate_part(
        self,
        part: str,
        price: float,
        label: str,
    ) -> tuple[bool, str]:
        """评估一个具体比较部分."""
        # 去掉日期/月份数字，避免误把“9月”当阈值
        part_clean = re.sub(r"\d+\s*[年月日]", "", part)

        # 去掉事件时间 (如 20:30, 08:30) — 事件钟点不是价格阈值
        part_clean = re.sub(r"\d{1,2}:\d{2}", "", part_clean)

        # 合并千位分隔符数字 (如 4,100 → 4100)，避免拆成 4 和 100
        part_clean = re.sub(
            r"(?<!\d)(\d{1,3}(?:,\d{3})+)(?!\d)",
            lambda m: m.group(0).replace(",", ""),
            part_clean,
        )

        # 去掉百分比数字 (如 5%, >2%) — 百分比/幅度不是绝对价格阈值
        part_clean = re.sub(r"(?<!\d)\d+(?:\.\d+)?\s*[%％]", "", part_clean)

        # 去掉字母+数字标识符 (如 L1, RSI2, K3) — 协议/指标编号不是价格
        part_clean = re.sub(r"[A-Za-z]\d+(?:\.\d+)?", "", part_clean)

        # 提取所有数字及其上下文
        numbers = [
            (m.start(), float(m.group(1)))
            for m in re.finditer(r"(?<!\d)(\d+(?:\.\d+)?)(?!\d)", part_clean)
        ]
        if not numbers:
            return False, "manual"

        for pos, value in numbers:
            op = self._operator_at_position(part_clean, pos)
            if op is None:
                continue

            if op == "range":
                # 区间需要两个数字，已在 range 解析里处理
                continue

            if op in ("<=", "le", "≤") and price <= value:
                return True, f"{label} {price:.2f} ≤ {value:.2f}"
            elif op in (">=", "ge", "≥") and price >= value:
                return True, f"{label} {price:.2f} ≥ {value:.2f}"
            elif op in ("<", "lt") and price < value:
                return True, f"{label} {price:.2f} < {value:.2f}"
            elif op in (">", "gt") and price > value:
                return True, f"{label} {price:.2f} > {value:.2f}"

        # 尝试解析“之间/区间”
        range_match = re.search(r"(\d+(?:\.\d+)?)\s*[-~～]\s*(\d+(?:\.\d+)?)", part)
        if range_match:
            lo, hi = sorted([float(range_match.group(1)), float(range_match.group(2))])
            if lo <= price <= hi:
                return True, f"{label} {price:.2f} 在 {lo:.2f}-{hi:.2f} 区间"

        return False, "manual"

    @staticmethod
    def _operator_at_position(text: str, pos: int) -> str | None:
        """根据数字在文本中的位置，查找附近的比较运算符."""
        window = 8
        before = text[max(0, pos - window):pos]
        after = text[pos:pos + window]
        combined = before + after

        # 区间词
        if "之间" in combined or "区间" in combined:
            return "range"

        # 小于等于
        if re.search(r"(≤|<=|不超过|低于|跌破|小于等于|至多)", combined):
            return "<="

        # 大于等于
        if re.search(r"(≥|>=|至少|站稳|大于等于|不低于)", combined):
            return ">="

        # 严格小于
        if re.search(r"\b(<|小于|低于|跌破)\b", combined):
            return "<"

        # 严格大于
        if re.search(r"\b(>|大于|高于|突破|超过)\b", combined):
            return ">"

        return None

    @staticmethod
    def _price_for_clause(clause: str, ctx: MonitorContext) -> tuple[float | None, str]:
        """根据子句中的资产关键词选择对应价格."""
        clause_lower = clause.lower()

        if "民生积存金" in clause or "积存金" in clause:
            if ctx.minsheng_price is not None:
                return ctx.minsheng_price, "民生积存金"
            return None, "民生积存金"

        if "xauusd" in clause_lower or "国际现货" in clause:
            if ctx.xauusd is not None:
                return ctx.xauusd, "XAUUSD"
            return None, "XAUUSD"

        if "金价" in clause or "黄金价格" in clause:
            if ctx.xauusd is not None:
                return ctx.xauusd, "金价(国际)"
            if ctx.gold_price is not None:
                return ctx.gold_price, "金价(国内)"
            return None, "金价"

        if "油价" in clause or "brent" in clause_lower or "wti" in clause_lower:
            if ctx.oil_price is not None:
                return ctx.oil_price, "油价"
            return None, "油价"

        return None, "未知资产"
