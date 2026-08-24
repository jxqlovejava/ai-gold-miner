#!/usr/bin/env python3
"""金价分析报告组装器 — scan_report 程序化组装报告骨架 (P2, 2026-08-22).

目标: 把「LLM 每次从头撰写整份报告」降为「LLM 只补 3 个推理板块」。
程序化从 scan_report 提取 决策/评分/置信度/维度表/军规/Munger/画像/博弈/后续关注/经验提醒,
从 portfolio.yaml 提取持仓, 从 conditional_orders.jsonl 提取 active 条件单,
按 docs/report_template.md 板块顺序组装报告骨架。

需要 LLM 推理的板块 (主驱动一句话/目标区间推导/条件单审查理由) 输出标题+占位,
由分析流程增量填充。

用法:
    python3 scripts/assemble_report.py                              # 自动选最新 scan_report + 今日报告
    python3 scripts/assemble_report.py --scan <scan_report.md>      # 指定 scan 报告
    python3 scripts/assemble_report.py --out <金价分析_YYYY-MM-DD.md>

注意: 输出遵循报告格式铁律 — 板块间仅空行, 不输出独立 --- 分隔线 (validate_report_format 校验).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"
PRIVATE_DIR = PROJECT_ROOT / "data" / "private"

# ═══════════════════════════════════════════════════════════════
# scan_report 板块提取
# ═══════════════════════════════════════════════════════════════

_ASCII_BOX = ("┌", "├", "└", "│")


def _latest_scan_report() -> Path | None:
    """自动选最新 scan_report_*.md."""
    cands = sorted(
        OUTPUT_DIR.glob("scan_report_*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return cands[0] if cands else None


def _extract_ascii_table(text: str) -> list[str]:
    """提取 scan_report 中第一张 ASCII 框线维度表, 转 markdown 表格行.

    识别: 仅取 │ 开头的数据行 (表头 + 数据), 跳过 ┌/├/└ 框线行.
    返回 markdown 表格行列表 (含表头/分隔/数据行).
    """
    lines = text.splitlines()
    rows: list[list[str]] = []
    for ln in lines:
        s = ln.strip()
        if not s.startswith("│"):
            continue  # 框线行 (┌├└) 与空行跳过
        cells = [c.strip() for c in s.strip("│").split("│")]
        rows.append(cells)
    if not rows:
        return []
    header = rows[0]
    sep = "|" + "|".join(["---"] * len(header)) + "|"
    out = ["| " + " | ".join(header) + " |", sep]
    for cells in rows[1:]:
        out.append("| " + " | ".join(cells) + " |")
    return out


def _extract_decision(text: str) -> dict[str, str]:
    """提取决策: 方向/评分/置信度/止损.

    方向优先取操作清单「持仓动作」(观望/买入/卖出/持有), 而非 ATR「信号」(持仓信号≠决策方向).
    """
    out: dict[str, str] = {}
    m = re.search(r"^\s*1\.\s*持仓动作:\s*(.+)$", text, re.M)
    if m:
        out["signal"] = m.group(1).strip()
    else:
        m = re.search(r"^\s*信号:\s*(.+)$", text, re.M)
        if m:
            out["signal"] = m.group(1).strip()
    m = re.search(r"综合评分:\s*([+\-−]?[\d.]+)", text)
    if m:
        out["score"] = m.group(1).strip()
    # 置信度优先取跨维度降权后的值 (步骤3「置信度 75% → 60%」行), 无降权才取原始值
    # (2026-08-24 修复: 原取首个匹配=原始值, 报告头部置信度高于实际)
    m = re.search(r"置信度[:：]?\s*\d+%?\s*→\s*(\d+%?)", text)
    if m:
        out["confidence"] = m.group(1).strip()
        m2 = re.search(r"置信度[:：]?\s*(\d+%?)\s*→", text)
        if m2:
            out["confidence_raw"] = m2.group(1).strip()
    else:
        m = re.search(r"置信度:\s*(\d+%?)", text)
        if m:
            out["confidence"] = m.group(1).strip()
    m = re.search(r"止损位:\s*([\d.]+)", text)
    if m:
        out["stop_loss"] = m.group(1).strip()
    m = re.search(r"建议区间:\s*([\d.]+)\s*~\s*([\d.]+)", text)
    if m:
        out["suggest_range"] = f"{m.group(1)} ~ {m.group(2)}"
    return out


def _extract_prices(text: str) -> dict[str, str]:
    """提取行情: 国内金价/积存金/国际金价."""
    out: dict[str, str] = {}
    m = re.search(r"国内金价:\s*([\d.]+)", text)
    if m:
        out["domestic"] = m.group(1)
    m = re.search(r"民生银行积存金:\s*([\d.]+)", text)
    if m:
        out["accum"] = m.group(1)
    m = re.search(r"国际金价 XAU/USD:\s*([\d.]+)", text)
    if m:
        out["intl"] = m.group(1)
    return out


def _extract_doctrine(text: str) -> tuple[str, list[str]]:
    """提取军规审查: (通过 X/Y, 警告行列表)."""
    summary = ""
    m = re.search(r"通过:\s*(\d+/\d+)", text)
    if m:
        summary = m.group(1)
    warns: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if re.match(r"^[⚠!]\s", s) or ("警告" in s and "◆" in s):
            warns.append(s)
    return summary, warns[:5]


def _extract_block(text: str, title: str, end_markers: tuple[str, ...]) -> str:
    """提取标题后的内容块, 到 end_markers 或下一个框线结束.

    标题匹配排除 INFO 日志行 (scan 报告混入 `| INFO |` 日志), 避免取到日志而非板块内容.
    """
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        # 排除 loguru 日志行 (格式 `HH:MM:SS | INFO     | ...`, INFO 后多空格)
        if title in ln and "| INFO" not in ln and "| DEBUG" not in ln and "| WARNING" not in ln:
            start = i + 1
            break
    if start is None:
        return ""
    block: list[str] = []
    started = False
    for ln in lines[start:]:
        s = ln.strip()
        if not s:
            if started:
                break
            continue
        if s.startswith("=") or s.startswith("-" * 10):
            if started:
                break
            continue  # 跳过标题后的框线行, 从实际内容开始收集
        if any(mk in s for mk in end_markers):
            break
        block.append(ln.rstrip())
        started = True
    return "\n".join(block)


def _extract_debate(text: str) -> dict[str, str]:
    """提取 Agent 博弈: bull/bear/pm 论据块."""
    out: dict[str, str] = {}
    bull = _extract_block(text, "🐂 多头分析师", ("🐻", "🏛️", "投资军规", "="))
    if bull:
        out["bull"] = bull
    bear = _extract_block(text, "🐻 空头分析师", ("🏛️", "投资军规", "="))
    if bear:
        out["bear"] = bear
    pm = _extract_block(text, "🏛️ 投资经理", ("投资军规", "="))
    if pm:
        out["pm"] = pm
    return out


def _extract_munger(text: str) -> str:
    """提取 Munger 模型列表 (所有 • 项, 不因空行截断)."""
    lines = text.splitlines()
    in_block = False
    items: list[str] = []
    for ln in lines:
        s = ln.strip()
        if "Munger 思维模型" in s and "| INFO" not in s:
            in_block = True
            continue
        if in_block:
            if "画像匹配" in s and "| INFO" not in s:
                break
            if s.startswith("•"):
                items.append(s.lstrip("• ").strip())
    return "\n".join(f"- {it}" for it in items)


def _extract_profile(text: str) -> str:
    """提取画像匹配."""
    return _extract_block(text, "画像匹配", ("经验提醒", "="))


def _extract_events(text: str) -> list[str]:
    """提取未来关注事件 (analysis.py step9 print 的「未来关注事件(未来14天)」板块)."""
    lines = text.splitlines()
    events: list[str] = []
    in_block = False
    for ln in lines:
        s = ln.strip()
        if "未来关注事件(未来14天" in s:
            in_block = True
            continue
        if in_block:
            if not s:
                continue
            if s.startswith("=") or s.startswith("-" * 10) or "| INFO" in ln:
                break
            events.append(s)
    return events


def _extract_event_results(text: str) -> list[str]:
    """提取近期事件结果回顾 (dashboard.py print 的「近期事件结果回顾:」板块)."""
    lines = text.splitlines()
    results: list[str] = []
    in_block = False
    for ln in lines:
        s = ln.strip()
        if "近期事件结果回顾" in s:
            in_block = True
            continue
        if in_block:
            if not s:
                continue
            if s.startswith("=") or s.startswith("-" * 10) or "| INFO" in ln:
                break
            results.append(s)
    return results


def _extract_reminders(text: str) -> list[str]:
    """提取经验提醒."""
    block = _extract_block(text, "经验提醒", ("=",))
    return [ln.strip().lstrip("0123456789. ") for ln in (block or "").splitlines() if ln.strip()][:5]


# ═══════════════════════════════════════════════════════════════
# scan 数据摘要 (digest, 2026-08-22 提速P4)
# ═══════════════════════════════════════════════════════════════

def _extract_framed_section(text: str, title: str) -> list[str]:
    """提取 scan_report 中 `==== / 标题 / ====` 框线板块正文, 到下一框线结束.

    标题后紧跟的框线跳过; 收到正文后再遇框线即收尾. 排除 INFO 日志行.
    """
    out: list[str] = []
    in_sec = False
    for ln in text.splitlines():
        s = ln.strip()
        if not in_sec:
            if title in s and "| INFO" not in ln and "| DEBUG" not in ln:
                in_sec = True
            continue
        if s.startswith("="):
            if out:
                break
            continue  # 标题后的闭合框线
        out.append(s)
    while out and not out[-1]:
        out.pop()
    return out


def _extract_infoblock(text: str, marker: str, max_lines: int = 8) -> list[str]:
    """提取标记行及其后续连续非空行 (缠论/日内分时/ATR 等短块)."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if marker in ln and "| INFO" not in ln and "| DEBUG" not in ln:
            block = [ln.strip()]
            for ln2 in lines[i + 1:]:
                if not ln2.strip() or "| INFO" in ln2 or "| DEBUG" in ln2:
                    break
                block.append(ln2.strip())
                if len(block) > max_lines:
                    break
            return block
    return []


def write_digest(scan_text: str, out_path: Path) -> int:
    """生成 scan 数据摘要: 技术面/聪明钱明细 + 缠论/分时/ATR.

    目的: REUSE 场景 LLM 只读 骨架(报告结构) + 摘要(推理所需明细),
    不再读 420 行 scan_report 全文 (~12k token/轮 -> 推理提速).
    摘要是 LLM 工作文件, 不进最终报告, 无格式校验约束.
    """
    lines: list[str] = []
    lines.append(f"# 📎 scan 数据摘要 · {datetime.now().strftime('%Y-%m-%d')}")
    lines.append("")
    lines.append("> LLM 推理用工作文件（技术面/聪明钱明细），报告骨架不含这些数据。")
    lines.append("")
    tech = _extract_framed_section(scan_text, "📊 技术面")
    if tech:
        lines.append("## 技术面明细")
        lines.extend(tech)
        lines.append("")
    smart = _extract_framed_section(scan_text, "聪明钱资金流")
    if smart:
        lines.append("## 聪明钱明细")
        lines.extend(smart)
        lines.append("")
    chan = _extract_infoblock(scan_text, "📊 缠论结构", max_lines=3)
    if chan:
        lines.append("## 缠论结构")
        lines.extend(chan)
        lines.append("")
    intraday = _extract_infoblock(scan_text, "⏱️ 日内分时", max_lines=7)
    if intraday:
        lines.append("## 日内分时")
        lines.extend(intraday)
        lines.append("")
    for ln in scan_text.splitlines():
        if "📐 ATR 移动止盈" in ln and "| INFO" not in ln:
            lines.append("## ATR")
            lines.append(ln.strip())
            lines.append("")
            break
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ scan 摘要已生成: {out_path}（{len(lines)} 行）", file=sys.stderr)
    return 0


# ═══════════════════════════════════════════════════════════════
# 持仓 / 条件单
# ═══════════════════════════════════════════════════════════════

def _load_portfolio() -> dict:
    path = PRIVATE_DIR / "portfolio.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_conditional_orders() -> list[dict]:
    path = PRIVATE_DIR / "conditional_orders.jsonl"
    if not path.exists():
        return []
    orders = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("status") == "active":
            orders.append(row)
    return orders


def _describe_order(o: dict) -> str:
    oid = o.get("id", "?")
    typ = o.get("type", "")
    direction = o.get("direction", "")
    tp = o.get("trigger_price", "")
    qty = o.get("quantity_g", "")
    if typ == "oco":
        tp_detail = (o.get("oco") or {})
        tp_p = tp_detail.get("take_profit", {}).get("price", "")
        sl_p = tp_detail.get("stop_loss", {}).get("price", "")
        return f"- {oid}（OCO {direction}）TP{tp_p}/SL{sl_p} {qty}g：保留（LLM 补充审查理由）"
    return f"- {oid}（{typ} {direction}@{tp} {qty}g）：保留（LLM 补充审查理由）"


# ═══════════════════════════════════════════════════════════════
# 报告组装
# ═══════════════════════════════════════════════════════════════

_DIR_MAP = {"持有": "持有", "观望": "观望", "买入": "买入", "卖出": "卖出"}


def assemble(scan_text: str, out_path: Path) -> None:
    decision = _extract_decision(scan_text)
    prices = _extract_prices(scan_text)
    table = _extract_ascii_table(scan_text)
    doctrine, warns = _extract_doctrine(scan_text)
    debate = _extract_debate(scan_text)
    munger = _extract_munger(scan_text)
    profile = _extract_profile(scan_text)
    events = _extract_events(scan_text)
    event_results = _extract_event_results(scan_text)
    reminders = _extract_reminders(scan_text)
    orders = _load_conditional_orders()
    pf = _load_portfolio()
    gold = (pf.get("positions") or {}).get("gold_jd", {})
    grams = float(gold.get("grams") or 0)
    avg_cost = float(gold.get("avg_cost") or 0)
    sell_fee = float(gold.get("sell_fee_pct") or 0) / 100
    net_be = (avg_cost / (1 - sell_fee)) if avg_cost > 0 and sell_fee < 1 else 0

    now = datetime.now()
    signal = decision.get("signal", "观望")
    score = decision.get("score", "-")
    conf = decision.get("confidence", "-")
    if decision.get("confidence_raw"):
        conf = f"{conf}（原 {decision['confidence_raw']}，跨维度不一致降权）"
    accum = prices.get("accum") or prices.get("domestic", "-")
    intl = prices.get("intl", "-")

    lines: list[str] = []
    lines.append(f"# 🥇 金价完整分析 · {now.strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append(f"> 积存金 **¥{accum}** | 国际 ${intl}/oz")
    lines.append("")
    lines.append(f"## 1. 📌 决策: **{signal}** | 综合评分 {score} | 置信度 {conf}")
    lines.append("建议仓位: 0%（空仓观察，LLM 补充）" if grams <= 0 else f"建议仓位: {grams}g（LLM 补充仓位表述）")
    if decision.get("stop_loss"):
        lines.append(f"止损: {decision['stop_loss']}")
    else:
        lines.append("止损: 无")
    # 占位符用「无」不用 em-dash「—」：LLM Edit 回填时复打 dash 极易打成变体，
    # 精确匹配失败报「Error editing file」（2026-08-22 事故）。全 ASCII/无近形字。
    lines.append("止盈: 无")
    lines.append("")
    lines.append("## 1.1 🔍 主驱动因素")
    lines.append("> （LLM 增量填充：一句话第一性主驱动 + 驱动排序表）")
    lines.append("")
    lines.append("## 1.2 🎯 金价目标区间预测（未来 1-3 个月）")
    lines.append("（LLM 增量填充：三情景目标区间表，含概率/触发条件/传导链 r035）")
    lines.append("")
    lines.append("## 2. 维度信号")
    if table:
        lines.extend(table)
    else:
        lines.append("（本期无触发）")
    lines.append("")
    # 8 维逐项明细 (AGENTS.md「多维度信号必须逐项说明」强制, 缺=无效分析; 2026-08-24 补)
    # 各维度明细板块从 scan_report 框线板块提取, 0 信号维度保留标题写空态
    _DIM_SECTIONS = [
        ("📊 技术面", "📊 技术面"),
        ("🏛️ 基本面", "🏛️ 基本面"),
        ("👔 聪明钱资金流", "聪明钱资金流"),
        ("📰 消息面", "📰 消息面"),
        ("💭 情绪面", "💭 情绪面"),
        ("📅 事件驱动/经济日历", "📅 经济日历"),
    ]
    for dim_title, scan_marker in _DIM_SECTIONS:
        sec = _extract_framed_section(scan_text, scan_marker)
        lines.append(f"### {dim_title}")
        if sec:
            lines.extend(sec)
        else:
            lines.append("（本期无触发 / 数据缺失）")
        lines.append("")
    # 缠论结构子板块 (report_template.md 2026-08-12 起强制, 技术面必含)
    chan_sec = _extract_infoblock(scan_text, "📊 缠论结构", max_lines=3)
    lines.append("### 🀄 缠论结构")
    if chan_sec:
        lines.extend(chan_sec[1:])  # 首行是 digest 标题行, 略
    else:
        lines.append("（本期无触发）")
    lines.append("")
    # 板块顺序 = 推理顺序: 军规(3) -> Munger(4) -> 画像(5) -> 博弈(6) -> 条件单(7)
    # (2026-08-22 调整: 博弈综合军规/Munger/画像为输入, 展示顺序与推理顺序统一)
    lines.append("## 3. 军规自查")
    lines.append(f"通过 {doctrine or '-'}")
    if warns:
        lines.extend(f"⚠️ {w}" for w in warns)
    else:
        lines.append("（本期无违规）")
    lines.append("")
    lines.append("## 4. Munger 模型")
    lines.append(munger if munger else "- （本期无触发）")
    lines.append("")
    lines.append("## 5. 画像匹配")
    if profile:
        lines.extend(f"- {ln.strip()}" for ln in profile.splitlines() if ln.strip())
    else:
        lines.append("✅ 兼容（LLM 补充约束检查）")
    lines.append("")
    lines.append("## 6. Agent 博弈")
    if debate.get("bull"):
        lines.append("🐮 **BullAgent**")
        lines.extend(f"  {ln}" for ln in debate["bull"].splitlines() if ln.strip())
    else:
        lines.append("🐮 **BullAgent**（本期无多头发言）")
    if debate.get("bear"):
        lines.append("🐻 **BearAgent**")
        lines.extend(f"  {ln}" for ln in debate["bear"].splitlines() if ln.strip())
    else:
        lines.append("🐻 **BearAgent**（本期无空头发言）")
    if debate.get("pm"):
        lines.append("💼 **PortfolioManager**")
        lines.extend(f"  {ln}" for ln in debate["pm"].splitlines() if ln.strip())
    else:
        lines.append("💼 **PortfolioManager**（观望）")
    lines.append("")
    lines.append("## 7. 条件单审查")
    if orders:
        for o in orders:
            lines.append(_describe_order(o))
        lines.append("- 提示: 单号后「LLM 补充审查理由」处由分析流程填充保留/撤销/修改依据")
    else:
        lines.append("（无 active 条件单）")
    lines.append("")
    lines.append("## 8. 后续关注")
    lines.append("### 📅 未来14天事件前瞻")
    if events:
        # step9 print 行已带「- 」前缀, 剥掉再统一加, 避免双横杠
        lines.extend(f"- {e.lstrip('- ')}" for e in events)
    else:
        lines.append("（本期无中高影响未来事件）")
    lines.append("")
    lines.append("### 📋 近期事件结果回顾")
    if event_results:
        lines.extend(f"- {r}" for r in event_results)
    else:
        lines.append("（本期无事件结果回顾）")
    lines.append("")
    lines.append("## 9. 📚 经验提醒")
    if reminders:
        lines.extend(f"- {r}" for r in reminders)
    else:
        lines.append("- （本期无触发）")
    lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ 报告骨架已组装: {out_path}（{len(lines)} 行）", file=sys.stderr)
    print(f"   LLM 增量板块: 主驱动因素(1.1) / 目标区间(1.2) / 条件单审查(7)", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="scan_report → 金价分析报告骨架")
    parser.add_argument("--scan", help="scan_report 路径 (默认最新 data/output/scan_report_*.md)")
    parser.add_argument("--out", help="输出路径 (默认 data/output/金价分析_YYYY-MM-DD.md)")
    parser.add_argument("--digest-only", action="store_true",
                        help="仅生成 scan 摘要(骨架+摘要双文件模式, 不组装报告骨架)")
    args = parser.parse_args(argv)

    scan_path = Path(args.scan) if args.scan else _latest_scan_report()
    if scan_path is None or not scan_path.exists():
        print("未找到 scan_report, 请用 --scan 指定", file=sys.stderr)
        return 1

    if args.digest_only:
        digest_path = OUTPUT_DIR / f"scan_digest_{datetime.now().strftime('%Y-%m-%d')}.md"
        return write_digest(scan_path.read_text(encoding="utf-8"), digest_path)

    if args.out:
        out_path = Path(args.out)
    else:
        out_path = OUTPUT_DIR / f"金价分析_{datetime.now().strftime('%Y-%m-%d')}.md"

    scan_text = scan_path.read_text(encoding="utf-8")
    # digest 与骨架同源同进程顺带刷新 (P6 2026-08-22: 消灭 quick_scan 全量路径里
    # --digest-only 的第二次 python 冷启动; digest 是 scan_text 的确定性产物)
    digest_path = OUTPUT_DIR / f"scan_digest_{datetime.now().strftime('%Y-%m-%d')}.md"
    write_digest(scan_text, digest_path)
    assemble(scan_text, out_path)
    # 每次全量分析后刷新增量判断基准 (问题#2/4: 增量引擎永远基于最新全量分析)
    try:
        from gold_miner.incremental.judge import seed_baseline_from_scan, load_state, save_state

        new_baseline = seed_baseline_from_scan(scan_path)
        if new_baseline:
            state = load_state()
            state["baseline"] = new_baseline
            # 新分析不继承旧 delta 痕迹, 但保留已吸收事件与历史
            state["baseline"].pop("last_delta", None)
            save_state(state)
            print(f"✅ 增量判断基准已刷新: 方向={new_baseline.get('direction')} "
                  f"评分={new_baseline.get('score')} 区间={new_baseline.get('target_range')}",
                  file=sys.stderr)
    except Exception as e:
        print(f"⚠️ 增量判断基准刷新失败(不影响报告): {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
