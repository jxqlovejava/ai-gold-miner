"""jdgold 登录对账 — 条件单/持仓/交易记录 三账本 (P2).

集成背景: docs/analysis/jdgold-integration-analysis-2026-08-12.md T3/T4/T5。
仅登录窗口 (约8h token) 内运行; 任一账本失败 → 保留旧账本, 不阻塞其余账本。

- 条件单: 覆写/合并 conditional_orders.jsonl (按 bank+type+trigger_price 匹配, 更新真实状态, 保留本地字段)
- 持仓:   portfolio.yaml positions.gold_jd 的 grams/avg_cost 对账 (民生 CMBC), 保留止损/拆分等本地字段
- 交易:   trade_log.md 追记 (按 bizTime 去重, 幂等)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from gold_miner.data.jdgold_client import _repo_root

_BEIJING = timezone(__import__("datetime").timedelta(hours=8))

# jdgold 条件单 → 项目 jsonl 映射
_JD_STATUS_MAP: dict[str, str] = {
    "1": "active", "2": "triggered", "3": "cancelled", "4": "cancelled", "5": "cancelled",
}
_JD_TYPE_MAP: dict[str, str] = {
    "1": "limit_buy", "2": "limit_sell", "3": "take_profit", "4": "stop_loss", "5": "oco",
}
_JD_BANK_MAP: dict[str, str] = {"CMBC": "MS", "CIB": "CIB", "CITIC": "ZX", "CZB": "ZS"}


def _private_dir() -> Path:
    return _repo_root() / "data" / "private"


def _orders_path() -> Path:
    return _private_dir() / "conditional_orders.jsonl"


def _portfolio_path() -> Path:
    return _private_dir() / "portfolio.yaml"


def _trade_log_path() -> Path:
    return _private_dir() / "trade_log.md"


def _sync_state_path() -> Path:
    return _private_dir() / "jdgold_sync_state.json"


# ═══════════════════════════════════════════════════════════════
# 条件单对账
# ═══════════════════════════════════════════════════════════════

def _first_float(item: dict, keys: tuple[str, ...]) -> float | None:
    for k in keys:
        v = item.get(k)
        if v in (None, ""):
            continue
        try:
            return float(v)
        except (ValueError, TypeError):
            continue
    return None


def _normalize_jd_order(item: dict, bank_code: str) -> dict | None:
    """jdgold 条件单原始项 → 项目 jsonl 形状; 无法归一化返回 None (跳过不损坏账本)."""
    status_code = str(item.get("status") or item.get("conditionalStatus") or "")
    type_code = str(item.get("tradeType") or item.get("conditionalType") or item.get("ruleType") or "")
    price = _first_float(item, ("targetPrice", "conditionalPrice", "orderPrice"))
    if price is None or price <= 0:
        return None
    otype = _JD_TYPE_MAP.get(type_code, "limit_buy")
    status = _JD_STATUS_MAP.get(status_code, "cancelled")
    return {
        "status": status,
        "type": otype,
        "bank": _JD_BANK_MAP.get(bank_code, bank_code),
        "trigger_price": price,
        "quantity_g": _first_float(item, ("amount", "gram", "orderGram")),
        "instrument": "积存金",
        "platform": "京东金融",
        "direction": "卖出" if otype in ("limit_sell", "take_profit", "stop_loss", "oco") else "买入",
    }


def _extract_order_list(bank_resp: Any) -> list[dict]:
    """从单银行 raw BFF response 提取订单列表 (字段名因银行而异, 防御式)."""
    if not isinstance(bank_resp, dict) or bank_resp.get("__error__"):
        return []
    datas = bank_resp.get("datas") or {}
    if isinstance(datas, list):
        return [x for x in datas if isinstance(x, dict)]
    if isinstance(datas, dict):
        for key in ("data", "list", "resultList"):
            val = datas.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
    return []


def sync_conditional_orders(orders_data: Any) -> dict:
    """jdgold 条件单 → 合并 conditional_orders.jsonl.

    匹配键 (bank, type, trigger_price): 命中 → 更新 status 等共享字段 (保留 note/source_analysis/oco 本地字段);
    jdgold 新单 → 追加; 本地有但 jdgold 无 → 保留 (本地手动/规划记录)。
    失败时保持旧账本不变 (抛异常由调用方捕获)。
    """
    path = _orders_path()
    if not isinstance(orders_data, dict):
        raise ValueError("条件单数据格式错误")

    existing: list[dict] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").strip().split("\n"):
            line = line.strip()
            if line:
                try:
                    existing.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    # 索引已有订单: key → order
    by_key: dict[tuple, dict] = {}
    for o in existing:
        k = (str(o.get("bank", "")), str(o.get("type", "")), round(float(o.get("trigger_price", 0)), 2))
        by_key.setdefault(k, o)

    seq = 0
    for bank_code, bank_resp in orders_data.items():
        if not isinstance(bank_resp, dict) or bank_resp.get("__error__"):
            continue
        for item in _extract_order_list(bank_resp):
            norm = _normalize_jd_order(item, str(bank_code))
            if not norm:
                continue
            k = (norm["bank"], norm["type"], round(norm["trigger_price"], 2))
            match = by_key.get(k)
            if match is not None:
                # 更新共享字段, 保留本地字段 (note/source_analysis/oco/created_at 等)
                match["status"] = norm["status"]
                match["bank"] = norm["bank"]
                match["type"] = norm["type"]
                if norm.get("quantity_g") is not None:
                    match["quantity_g"] = norm["quantity_g"]
                if not match.get("id"):
                    match["id"] = _gen_id("co_jd", seq)
            else:
                new_order = dict(norm)
                new_order["id"] = _gen_id("co_jd", seq)
                new_order["created_at"] = _now_iso()
                existing.append(new_order)
                by_key[k] = new_order
            seq += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(o, ensure_ascii=False) for o in existing) + "\n",
        encoding="utf-8",
    )
    active = sum(1 for o in existing if o.get("status") == "active")
    return {"total": len(existing), "active": active, "synced": seq}


def _gen_id(prefix: str, seq: int) -> str:
    return f"{prefix}_{datetime.now(_BEIJING):%Y%m%d}_{seq:04d}"


def _now_iso() -> str:
    return datetime.now(_BEIJING).isoformat()


# ═══════════════════════════════════════════════════════════════
# 持仓对账 (portfolio.yaml grams/avg_cost, 保留本地字段)
# ═══════════════════════════════════════════════════════════════

def _update_yaml_field(text: str, field: str, value: float) -> str:
    """只替换 4 空格缩进 (gold_jd 子字段) 的 grams:/avg_cost: 值, 保留行尾注释."""
    import re

    pat = re.compile(rf"^(    {re.escape(field)}:)\s*[^\s#]+(.*)$", re.MULTILINE)
    new, n = pat.subn(lambda m: f"{m.group(1)} {value:.4f}{m.group(2)}", text, count=1)
    return new


def sync_holdings(holdings_data: Any) -> dict:
    """jdgold 持仓 → 更新 portfolio.yaml positions.gold_jd (民生 CMBC).

    只改 grams/avg_cost, 保留 hard_stop/warn_line/secondary_stop/split/entry_date/sell_fee_pct 等本地字段。
    """
    if not isinstance(holdings_data, dict):
        raise ValueError("持仓数据格式错误")
    holding_list = holdings_data.get("holdingList") or []
    ms = next((h for h in holding_list if str(h.get("bankCode", "")).upper() == "CMBC"), None)
    if ms is None:
        raise ValueError("未找到民生银行(CMBC)持仓记录")
    grams = float(ms.get("totalGram") or 0)
    avg_cost = float(ms.get("avgCostPrice") or 0)
    if grams <= 0 or avg_cost <= 0:
        raise ValueError(f"民生持仓数据异常: grams={grams}, avg_cost={avg_cost}")

    path = _portfolio_path()
    if not path.exists():
        raise FileNotFoundError(f"portfolio.yaml 不存在: {path}")
    text = path.read_text(encoding="utf-8")
    new_text = _update_yaml_field(text, "grams", grams)
    new_text = _update_yaml_field(new_text, "avg_cost", avg_cost)
    if new_text == text:
        raise ValueError("未找到 grams/avg_cost 字段, 持仓对账失败")
    path.write_text(new_text, encoding="utf-8")
    return {"bank": "MS", "grams": grams, "avg_cost": avg_cost}


# ═══════════════════════════════════════════════════════════════
# 交易记录追记 (trade_log.md, 幂等)
# ═══════════════════════════════════════════════════════════════

def _biz_time_ms(order: dict) -> int:
    try:
        return int(order.get("bizTime") or 0)
    except (ValueError, TypeError):
        return 0


def _trade_row(order: dict) -> str:
    """raw BFF order → markdown 表格行."""
    ts_ms = _biz_time_ms(order)
    when = datetime.fromtimestamp(ts_ms / 1000, _BEIJING).strftime("%Y-%m-%d %H:%M") if ts_ms else "—"
    ttype = str(order.get("tradeTypeCode") or order.get("type") or "?")
    amount = order.get("allAmount")
    status = str(order.get("statusCode") or "?")
    grams = price = ""
    ext = order.get("extInfo")
    if ext:
        try:
            info = json.loads(ext) if isinstance(ext, str) else ext
            grams = str(info.get("og") or "")
            price = str(info.get("ogp") or "")
        except (json.JSONDecodeError, TypeError):
            pass
    return f"| {when} | {ttype} | {amount} | {grams} | {price} | {status} |"


def sync_trade_records(records_data: Any) -> dict:
    """jdgold 交易记录 → 追记 trade_log.md (按 bizTime 去重, 幂等)."""
    if not isinstance(records_data, dict):
        raise ValueError("交易记录数据格式错误")
    order_list = records_data.get("list") or []
    if not order_list:
        return {"appended": 0}

    state: dict = {}
    spath = _sync_state_path()
    if spath.exists():
        try:
            state = json.loads(spath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {}
    last = int(state.get("trade_last_biz_time", 0) or 0)

    new_orders = sorted(
        (o for o in order_list if _biz_time_ms(o) > last and _biz_time_ms(o) > 0),
        key=_biz_time_ms,
    )
    if not new_orders:
        return {"appended": 0}

    path = _trade_log_path()
    header = f"\n## {datetime.now(_BEIJING):%Y-%m-%d} — jdgold 对账自动追记\n\n"
    header += "| 时间 | 类型 | 金额 | 克数 | 克单价 | 状态 |\n|---|---|---|---|---|---|\n"
    rows = "\n".join(_trade_row(o) for o in new_orders)
    with path.open("a", encoding="utf-8") as f:
        f.write(header + rows + "\n")

    state["trade_last_biz_time"] = new_orders[-1]["bizTime"]
    spath.parent.mkdir(parents=True, exist_ok=True)
    spath.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return {"appended": len(new_orders)}


# ═══════════════════════════════════════════════════════════════
# 编排
# ═══════════════════════════════════════════════════════════════

def maybe_pre_sync(max_age_hours: float = 6.0) -> str:
    """分析第一步前置同步 (P2, 非阻塞): 已登录 + 距上次同步超阈值才对账.

    未登录 / 未超节流阈值 → 返回空串 (调用方跳过)。
    用于 pipeline/analysis.py Step 1 前置; cron 无登录自动跳过。
    """
    try:
        from gold_miner.data.jdgold_client import check_login

        logged_in, _ = check_login()
        if not logged_in:
            return ""

        spath = _sync_state_path()
        if spath.exists():
            try:
                state = json.loads(spath.read_text(encoding="utf-8"))
                last = state.get("last_sync_at")
                if last:
                    last_dt = datetime.fromisoformat(last)
                    if (datetime.now(_BEIJING) - last_dt).total_seconds() < max_age_hours * 3600:
                        return ""
            except (json.JSONDecodeError, ValueError, OSError):
                pass

        report = run_all_sync()
        # 记录同步时间 (节流锚点)
        try:
            state = json.loads(spath.read_text(encoding="utf-8")) if spath.exists() else {}
            state["last_sync_at"] = datetime.now(_BEIJING).isoformat()
            spath.parent.mkdir(parents=True, exist_ok=True)
            spath.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        return report
    except Exception:
        return ""


def run_all_sync() -> str:
    """执行三账本对账, 返回 markdown 报告 (任一账本失败独立降级, 保留旧账本)."""
    from gold_miner.data.jdgold_client import (
        check_login,
        fetch_conditional_orders,
        fetch_holdings,
        fetch_trade_records,
    )

    logged_in, info = check_login()
    if not logged_in:
        reason = info.get("reason") or "未登录"
        return (
            f"## 🔐 jdgold 对账\n需要登录授权才能对账。原因: {reason}。\n"
            f"请先完成 jdgold 登录 (约8h有效) 后再运行 sync。"
        )

    lines = [f"## 🔄 jdgold 对账 · {datetime.now(_BEIJING):%Y-%m-%d %H:%M}"]
    lines.append(f"登录剩余: {info.get('remaining_human', '?')}")
    lines.append("")

    # 1) 条件单
    try:
        stats = sync_conditional_orders(fetch_conditional_orders("all"))
        lines.append(f"✅ 条件单: 对账 {stats['synced']} 条, 账本共 {stats['total']} 条 (active {stats['active']})")
    except Exception as e:
        logger.warning(f"条件单对账失败: {e}")
        lines.append(f"⚠️ 条件单对账失败 (保留旧账本): {e}")

    # 2) 持仓
    try:
        stats = sync_holdings(fetch_holdings())
        lines.append(f"✅ 持仓: 民生 {stats['grams']:.2f}g @ {stats['avg_cost']:.2f} 已对账 (止损/拆分保留)")
    except Exception as e:
        logger.warning(f"持仓对账失败: {e}")
        lines.append(f"⚠️ 持仓对账失败 (保留旧持仓): {e}")

    # 3) 交易记录
    try:
        stats = sync_trade_records(fetch_trade_records())
        lines.append(f"✅ 交易记录: 追记 {stats['appended']} 条 (幂等)")
    except Exception as e:
        logger.warning(f"交易记录追记失败: {e}")
        lines.append(f"⚠️ 交易记录追记失败: {e}")

    return "\n".join(lines)
