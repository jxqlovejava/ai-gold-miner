"""增量判断引擎核心 — 突发新闻/新事件 → 对金价方向的新判断.

问题背景 (2026-08-22): 完整金价分析 (scan) 只在用户主动触发时运行, 持续
出现的新事件不会增量影响判断, 必须等下一次全量分析才反应 → 占用用户精力.

本引擎:
  1. 维护持久化"基准判断" (data/private/decision_state.json) — 记录最近一次
     全量分析的方向/评分/置信度/目标区间/关键驱动.
  2. 检测新出现的高影响信号 (突发新闻 P0/P1 + 48h 内已落地日历事件结果),
     过滤已吸收事件 (dedup).
  3. 增量判断: 新事件相对基准的 delta (强化/反向/无明显变化), 用 LLM 结构化
     判定 + 规则 fallback.
  4. 更新基准判断 + 记录历史.
  5. 输出微信卡片 — 仅当 delta 实质变化 (强化新驱动或反向) 才输出, 否则静默
     (空 stdout → Hermes cron 不推送), 避免与突发新闻预警重复刷屏.

Hermes cron 用法 (stdout 投递微信, 空 stdout 静默):
    PYTHONPATH=src python3 -m gold_miner.incremental
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DECISION_STATE_PATH = PROJECT_ROOT / "data" / "private" / "decision_state.json"
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"

BEIJING = timezone(timedelta(hours=8))

# 新信号回溯窗口
NEWS_LOOKBACK_HOURS = 4
EVENT_LOOKBACK_DAYS = 3
# 吸收去重 TTL
ABSORB_TTL_DAYS = 7


def _now_bj() -> datetime:
    return datetime.now(BEIJING)


# ──────────────────────────────────────────────────────────────
# 基准判断状态
# ──────────────────────────────────────────────────────────────

def _empty_state() -> dict:
    return {
        "baseline": None,
        "absorbed_events": [],
        "history": [],
    }


def load_state() -> dict:
    if not DECISION_STATE_PATH.exists():
        return _empty_state()
    try:
        return json.loads(DECISION_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.warning(f"[incremental] 读取 {DECISION_STATE_PATH} 失败, 重建空状态")
        return _empty_state()


def save_state(state: dict) -> None:
    DECISION_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    DECISION_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _latest_scan_report() -> Path | None:
    cands = sorted(OUTPUT_DIR.glob("scan_report_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


def seed_baseline_from_scan(path: Path | None = None) -> dict | None:
    """从 scan_report 提取基准判断 (决策/评分/置信度/建议区间/仓位).

    Args:
        path: 指定 scan_report 路径; None 时取最新一份.
    """
    rep = path if path is not None else _latest_scan_report()
    if not rep or not rep.exists():
        return None
    text = rep.read_text(encoding="utf-8")
    baseline: dict = {
        "updated_at": _now_bj().isoformat(),
        "source": rep.name,
    }
    # 仅解析"黄金投资决策仪表盘"区块 (报告末尾), 避免命中 Agent 建议仓位/中间置信度
    dash = text.split("黄金投资决策仪表盘", 1)[-1]
    # 信号: 持有 / 观望 → 方向
    m = re.search(r"信号:\s*(\S+)", dash)
    if m:
        baseline["direction_cn"] = m.group(1).strip()
    # 仓位: 0%
    m = re.search(r"仓位:\s*([\d.]+)%", dash)
    if m:
        baseline["stance_pct"] = float(m.group(1))
    # 建议区间: 979.38 ~ 989.22
    m2 = re.search(r"建议区间:\s*([\d.]+)\s*~\s*([\d.]+)", dash)
    if m2:
        baseline["target_range"] = [float(m2.group(1)), float(m2.group(2))]
    # 综合评分: +0.12 / 置信度: 62%
    m = re.search(r"综合评分:\s*([+-]?\d+\.\d+)", dash)
    if m:
        baseline["score"] = float(m.group(1))
    m = re.search(r"置信度:\s*(\d+)%", dash)
    if m:
        baseline["confidence"] = float(m.group(1)) / 100.0
    if not baseline.get("direction_cn") and "target_range" not in baseline:
        return None
    baseline["direction"] = "neutral"
    if baseline.get("direction_cn") in ("看多", "买入", "加仓", "增持"):
        baseline["direction"] = "bullish"
    elif baseline.get("direction_cn") in ("看空", "卖出", "减仓", "减持"):
        baseline["direction"] = "bearish"
    # 观望/持有/中性 → neutral (空仓+观望不得误判为 bullish)
    logger.info(f"[incremental] 基准已从 {rep.name} 建立: "
                f"方向={baseline.get('direction')} 评分={baseline.get('score')} "
                f"区间={baseline.get('target_range')}")
    return baseline


# ──────────────────────────────────────────────────────────────
# 新信号采集
# ──────────────────────────────────────────────────────────────

def _title_hash(title: str) -> str:
    return hashlib.md5(title.strip().encode()).hexdigest()[:12]


def _absorbed_keys(state: dict) -> set[str]:
    cutoff = _now_bj().timestamp() - ABSORB_TTL_DAYS * 86400
    return {
        e["key"] for e in state.get("absorbed_events", [])
        if e.get("ts", 0) > cutoff
    }


def collect_news_inputs(absorbed: set[str]) -> list[dict]:
    """突发新闻 (P0/P1, 非重复) → 增量输入."""
    try:
        from gold_miner.sentinel.news_monitor import analyze_headlines, fetch_gold_headlines

        headlines = fetch_gold_headlines()
        alerts = analyze_headlines(headlines)
    except Exception as e:
        logger.warning(f"[incremental] 新闻采集失败: {e}")
        return []
    out = []
    for a in alerts:
        if a.get("level") not in ("P0", "P1"):
            continue
        key = "news:" + _title_hash(a.get("title", ""))
        if key in absorbed:
            continue
        out.append({
            "key": key,
            "type": "news",
            "title": a.get("title", "")[:120],
            "level": a.get("level"),
            "direction": a.get("direction", "neutral"),
            "label": a.get("label", ""),
            "impact": a.get("impact", ""),
        })
    return out


def collect_event_inputs(absorbed: set[str]) -> list[dict]:
    """48h 内已落地日历事件结果 (非 monitor, 有 gold_bias) → 增量输入."""
    try:
        from gold_miner.data.calendar import EventCalendar

        cal = EventCalendar()
        events = cal.get_recent_events_with_results(lookback_days=EVENT_LOOKBACK_DAYS)
    except Exception as e:
        logger.warning(f"[incremental] 日历事件采集失败: {e}")
        return []
    out = []
    for e in events:
        bias = getattr(e, "gold_bias", None)
        if not bias or not e.actual:
            continue
        key = f"event:{e.name}|{e.scheduled_at.date()}"
        if key in absorbed:
            continue
        out.append({
            "key": key,
            "type": "event",
            "title": e.name[:80],
            "actual": (e.actual or "")[:160],
            "gold_bias": bias,
        })
    return out


# ──────────────────────────────────────────────────────────────
# 增量判断
# ──────────────────────────────────────────────────────────────

def _rule_delta(baseline: dict, inputs: list[dict]) -> dict:
    """LLM 不可用时的规则 fallback: 按新信号方向聚合判定 delta."""
    up = sum(1 for i in inputs if i.get("direction") == "bullish" or i.get("gold_bias") == "bullish")
    down = sum(1 for i in inputs if i.get("direction") == "bearish" or i.get("gold_bias") == "bearish")
    if not inputs:
        return {"delta": "same", "delta_cn": "无明显变化", "material": False}
    if up > down:
        delta, updated = "reinforce", "bullish"
    elif down > up:
        delta, updated = "reverse", "bearish"
    else:
        return {"delta": "same", "delta_cn": "方向拉锯", "material": False}
    b_dir = baseline.get("direction", "neutral")
    material = (delta == "reverse") or (delta == "reinforce" and up + down >= 2)
    return {
        "delta": delta, "delta_cn": "强化" if delta == "reinforce" else "反向",
        "updated_direction": updated, "material": material,
        "key_driver": f"{len(inputs)} 条新信号方向{'一致' if up != down else '分化'}",
        "action_note": "新信号方向明确, 建议下一交易日按更新方向评估操作" if material else "",
    }


def _llm_judge(baseline: dict, inputs: list[dict]) -> dict | None:
    try:
        from gold_miner.llm.client import LLMClient

        client = LLMClient()
        if not client.enabled:
            return None
    except Exception:
        return None

    b = baseline
    events_txt = "\n".join(
        f"- [{i['type']}] {i.get('title','')} 方向={i.get('direction') or i.get('gold_bias','')} "
        f"{i.get('impact') or i.get('actual','')}"
        for i in inputs
    ) or "（无）"
    prompt = f"""你是黄金市场增量判断分析师。当前有一个"基准判断"（最近一次全量金价分析的结论），
以及新出现的高影响信号。请判断新信号是否**实质改变了**对金价方向的判断。

## 基准判断
- 方向: {b.get('direction','neutral')}（中文: {b.get('direction_cn','观望')}）
- 综合评分: {b.get('score','-')} | 置信度: {b.get('confidence','-')}
- 仓位建议: {b.get('stance_pct','-')}%
- 目标区间(积存金元/g): {b.get('target_range','-')}

## 近{NEWS_LOOKBACK_HOURS}h 新出现的高影响信号
{events_txt}

## 要求
以 JSON 返回（不要其他文字）：
{{
  "delta": "reinforce|reverse|same",
  "delta_cn": "强化|反向|无明显变化",
  "updated_direction": "bullish|bearish|neutral",
  "updated_range_lo": 数字或null,
  "updated_range_hi": 数字或null,
  "key_driver": "一句话主驱动变化",
  "action_note": "操作提示(≤80字)",
  "material": true或false
}}
- delta=reinforce: 新信号强化现有方向（或补强关键驱动）
- delta=reverse: 新信号方向与基准相反，可能改变判断
- delta=same: 新信号无实质影响 → material=false → 静默
- 短期(1-2周)判断以直接传导为准；地缘/油价同时评估二阶传导
- updated_range 相对基准区间，仅当有明确方向性偏移时给出，否则 null"""
    try:
        data = client.chat_json(prompt, timeout=30, max_tokens=800, temperature=0.0)
    except Exception as e:
        logger.warning(f"[incremental] LLM 判断失败: {e}")
        return None
    if not isinstance(data, dict) or "delta" not in data:
        return None
    return data


def judge(baseline: dict, inputs: list[dict]) -> dict:
    """对增量输入做判断, 返回 judgement dict."""
    if not inputs:
        return {"delta": "same", "delta_cn": "无明显变化", "material": False}
    res = _llm_judge(baseline, inputs)
    if res is None:
        res = _rule_delta(baseline, inputs)
    return res


# ──────────────────────────────────────────────────────────────
# 输出
# ──────────────────────────────────────────────────────────────

def format_card(baseline: dict, judgement: dict, inputs: list[dict]) -> str:
    b = baseline
    lines = ["⚡ 金价增量判断", ""]
    lines.append(f"基准: {b.get('direction_cn','观望')} | 评分 {b.get('score','-')} | "
                 f"置信度 {int(b.get('confidence',0)*100)}% | 区间 {b.get('target_range','-')}")
    lines.append(f"delta: {judgement.get('delta_cn','无明显变化')} → "
                 f"{judgement.get('updated_direction','neutral')} | "
                 f"区间 {judgement.get('updated_range_lo','-')}~{judgement.get('updated_range_hi','-')}")
    if judgement.get("key_driver"):
        lines.append(f"🔍 驱动: {judgement.get('key_driver')}")
    if judgement.get("action_note"):
        lines.append(f"💡 操作: {judgement.get('action_note')}")
    lines.append("")
    for i in inputs[:4]:
        tag = "📰" if i["type"] == "news" else "📅"
        title = i.get("title", "")
        extra = i.get("actual") or i.get("impact") or ""
        lines.append(f"{tag} {title[:70]}")
        if extra:
            lines.append(f"   → {extra[:100]}")
    lines.append(f"🤖 {_now_bj().strftime('%m-%d %H:%M')} | 青蚨")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────

def run_incremental() -> str:
    """执行一次增量判断, 返回微信卡片 (空=静默不推送)."""
    state = load_state()
    absorbed = _absorbed_keys(state)

    # 1. 基准: 已有则沿用, 否则从最新 scan_report 建立
    baseline = state.get("baseline")
    if not baseline:
        baseline = seed_baseline_from_scan()
        if baseline:
            state["baseline"] = baseline
            save_state(state)
        else:
            logger.warning("[incremental] 无基准判断且无 scan_report, 静默退出")
            return ""

    # 2. 采集新信号
    inputs = collect_news_inputs(absorbed) + collect_event_inputs(absorbed)
    if not inputs:
        return ""  # 无新信号, 静默

    # 3. 增量判断
    judgement = judge(baseline, inputs)

    # 4. 记录吸收 (无论 material 与否, 避免重复评估)
    now_ts = _now_bj().timestamp()
    state["absorbed_events"] = [
        e for e in state.get("absorbed_events", [])
        if e.get("ts", 0) > now_ts - ABSORB_TTL_DAYS * 86400
    ] + [{"key": i["key"], "ts": now_ts} for i in inputs]
    state["history"] = (state.get("history", [])[-49:] + [{
        "ts": _now_bj().isoformat(),
        "event": inputs[0].get("title", ""),
        "delta": judgement.get("delta"),
        "material": bool(judgement.get("material")),
    }])

    # 5. 实质变化才推送
    if not judgement.get("material"):
        save_state(state)
        logger.info(f"[incremental] {len(inputs)} 条新信号, delta={judgement.get('delta')}, 不推送")
        return ""
    judgement["updated_at"] = _now_bj().isoformat()
    state["baseline"] = {
        **baseline,
        "direction": judgement.get("updated_direction", baseline.get("direction", "neutral")),
        "updated_at": _now_bj().isoformat(),
        "last_delta": judgement.get("delta"),
        "last_key_driver": judgement.get("key_driver", ""),
    }
    if judgement.get("updated_range_lo") and judgement.get("updated_range_hi"):
        state["baseline"]["target_range"] = [
            judgement["updated_range_lo"], judgement["updated_range_hi"],
        ]
    save_state(state)

    card = format_card(baseline, judgement, inputs)
    logger.info(f"[incremental] 推送增量判断: delta={judgement.get('delta')} "
                f"({len(inputs)} 条新信号)")
    return card


def main() -> int:
    card = run_incremental()
    if card:
        print(card, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
