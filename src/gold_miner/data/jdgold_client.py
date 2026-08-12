"""jdgold Skill 免登录/需登录数据封装 — 数据层唯一入口.

集成背景: docs/analysis/jdgold-integration-analysis-2026-08-12.md
替代 9+ 处复制粘贴的 H5 端点 (getFirstRelatedProductInfo / latestPrice), H5 降为兜底。

架构约束:
  - jdgold skill 脚本 cwd 必须在 `.claude/skills/jdgold/scripts/` (jdjr_config.py 同目录),
    本模块一律 subprocess(cwd=scripts_dir) 调用, 不直接 import skill 脚本。
  - 免登录脚本 (query_gold_analysis / jdjr_query_stock / jdjr_query_news / query_blogger_trend)
    输出 JSON → 直接解析; 需登录脚本 (jos / query_conditional_orders / holdings_entry /
    query_trade_records / query_sim_contest) 仅交互/登录窗口用, 不进 cron。
  - 脚本缺失 (如未部署) → 免登录数据返回 None, 调用方落回 H5/既有源, 不抛异常。

本模块顶层只依赖标准库 (subprocess/json/os/sys/datetime), pandas/httpx 均惰性导入,
避免 8 个监控脚本仅取价也被迫加载重型依赖。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# 报告引用标注 (CLAUDE.md 信息验证协议): 京东金融公开数据, 由官方网关直接提供
SOURCE_TIER = "T1"
SOURCE_NAME = "jdgold"

# 客户端类型上报 (x-claw 头), 默认本项目代号, 可用环境变量覆盖
_CLAW_ENV = "GOLD_MINER_CLAW"
_DEFAULT_CLAW = "gold-miner"

# 各脚本默认超时 (秒)
_QUOTE_TIMEOUT = 15.0
_SCRIPT_TIMEOUT = 25.0

# jdgold 仅支持民生/浙商积存金实时价; 其余银行 (ZX/GS/GF/XY) 不支持 → 调用方落 H5
_BANK_TO_QUERY: dict[str, str] = {
    "MS": "民生积存金实时价",
    "ZS": "浙商积存金实时价",
}

# 8 脚本 + sentinel/quotes.py 现存的 latestPrice H5 兜底 (从各脚本集中迁移至此)
_H5_JD_URL = "https://ms.jr.jd.com/gw/generic/hj/h5/m/latestPrice"
_H5_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
    "Referer": "https://m.jd.com/",
}
_H5_TIMEOUT = 8.0


def _repo_root() -> Path:
    """仓库根目录 (优先 GOLD_MINER_ROOT 环境变量, 兼容服务器/本地)."""
    env = os.environ.get("GOLD_MINER_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3]


def _scripts_dir() -> Path | None:
    """jdgold skill scripts 目录; 未部署返回 None (调用方落 H5)."""
    candidate = _repo_root() / ".claude" / "skills" / "jdgold" / "scripts"
    return candidate if candidate.is_dir() else None


def _claw_arg() -> list[str]:
    claw = os.environ.get(_CLAW_ENV, _DEFAULT_CLAW).strip()
    return ["--claw", claw] if claw else []


def scripts_available() -> bool:
    """jdgold 脚本是否可用 (用于测试/日志判断主源生效与否)."""
    return _scripts_dir() is not None


def _run_script(script: str, args: list[str], timeout: float = _SCRIPT_TIMEOUT) -> dict | None:
    """subprocess 调用 jdgold 脚本, 解析 stdout JSON; 任何失败返回 None.

    约定: 退出码 0 + {"success": true} 才算成功。
    """
    scripts = _scripts_dir()
    if scripts is None:
        return None
    cmd = [sys.executable, str(scripts / script), *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(scripts),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout or "{}")
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("success") is False:
        return None
    return data


def _pct_float(value: Any) -> float | None:
    """"+0.04%" / "+0.04" → 0.04; 无法解析返回 None."""
    if value is None:
        return None
    try:
        text = str(value).strip().replace("%", "").replace("+", "").replace("，", "")
        return float(text) if text not in ("", "null") else None
    except (ValueError, TypeError):
        return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        text = str(value).strip()
        return float(text) if text not in ("", "null", "None") else None
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════════
# 免登录 — 积存金实时价
# ═══════════════════════════════════════════════════════════════

def fetch_accumulation_price(bank: str = "MS") -> dict | None:
    """jdgold 积存金实时价 (query_gold_analysis 免登录).

    仅支持 MS(民生)/ZS(浙商); 其余银行返回 None。
    返回: {name, price, change_pct, change_amount, prev_close, unit, source}
    """
    query = _BANK_TO_QUERY.get(str(bank).upper())
    if not query:
        return None
    data = _run_script("query_gold_analysis.py", [query, *_claw_arg()], timeout=_QUOTE_TIMEOUT)
    if not data:
        return None
    quotes = ((data.get("data") or {}).get("quotes")) or []
    if not quotes:
        return None
    q = quotes[0]
    price = _to_float(q.get("lastPrice"))
    if price is None or price <= 0:
        return None
    change_amount = _to_float(q.get("raise"))
    return {
        "name": q.get("name") or "京东积存金",
        "price": price,
        "change_pct": str(q.get("raisePercent") or ""),
        "change_amount": change_amount,
        "prev_close": round(price - change_amount, 2) if change_amount is not None else None,
        "unit": q.get("unit") or "元/克",
        "source": SOURCE_NAME,
    }


def _h5_latest_price_fallback() -> dict | None:
    """latestPrice H5 兜底 (从 8 脚本/ quotes.py 集中迁移). 返回 {price, prev_close, change_pct, source}."""
    import httpx

    try:
        resp = httpx.get(_H5_JD_URL, headers=_H5_HEADERS, timeout=_H5_TIMEOUT)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not data.get("success"):
            return None
        datas = (data.get("resultData") or {}).get("datas") or {}
        price = _to_float(datas.get("price"))
        yesterday = _to_float(datas.get("yesterdayPrice"))
        if price is None or price <= 0:
            return None
        change_pct = round((price - yesterday) / yesterday * 100, 2) if yesterday and yesterday > 0 else 0.0
        return {
            "price": round(price, 2),
            "prev_close": round(yesterday, 2) if yesterday else None,
            "change_pct": change_pct,
            "source": "京东金融",
        }
    except Exception:
        return None


def fetch_accumulation_quote(bank: str = "MS") -> dict | None:
    """积存金当前价, 兼容 8 脚本/ quotes.py 的 {price, prev_close, change_pct, source} 形状.

    jdgold 主源 → latestPrice H5 兜底; 全部失败返回 None。
    """
    info = fetch_accumulation_price(bank)
    if info:
        pct = _pct_float(info["change_pct"])
        return {
            "price": round(info["price"], 2),
            "prev_close": info["prev_close"] if info["prev_close"] is not None else round(info["price"], 2),
            "change_pct": round(pct, 2) if pct is not None else 0.0,
            "source": info["source"],
        }
    return _h5_latest_price_fallback()


# ═══════════════════════════════════════════════════════════════
# 免登录 — SGE 实时 + 历史 K 线
# ═══════════════════════════════════════════════════════════════

_SGE_CODE = "SGE-Au99.99"
_SILVER_CODE = "SGE-Ag99.99"


def fetch_sge_quote() -> dict | None:
    """SGE Au99.99 实时 OHLC (jdjr_query_stock quote 免登录).

    返回: {price, open, high, low, prev_close, change_pct(%), change_price, volume, name}
    """
    data = _run_script("jdjr_query_stock.py", ["quote", _SGE_CODE, *_claw_arg()], timeout=_QUOTE_TIMEOUT)
    if not data:
        return None
    d = data.get("data") or {}
    price = _to_float(d.get("currentPrice"))
    if price is None or price <= 0:
        return None
    change_ratio = _to_float(d.get("changeRatio"))
    return {
        "price": price,
        "open": _to_float(d.get("open")),
        "high": _to_float(d.get("maxPrice")),
        "low": _to_float(d.get("minPrice")),
        "prev_close": _to_float(d.get("closedYesterday")),
        "change_pct": round(change_ratio * 100, 2) if change_ratio is not None else None,
        "change_price": _to_float(d.get("changePrice")),
        "volume": d.get("volume"),
        "name": d.get("stockName") or "黄金9999",
        "source": SOURCE_NAME,
    }


def fetch_sge_kline(k_type: str = "day", code: str = _SGE_CODE) -> Any | None:
    """SGE 官方日/周/月 K 线 (jdjr_query_stock kline 免登录, ~1年).

    返回标准化 OHLCV DataFrame (timestamp/open/high/low/close/volume); 失败返回 None。
    pandas 惰性导入, 避免仅取价路径加载重依赖。
    """
    import pandas as pd

    data = _run_script("jdjr_query_stock.py", ["kline", code, "--k-type", k_type, *_claw_arg()])
    if not data:
        return None
    rows = ((data.get("data") or {}).get("kLineDtoList")) or []
    if not rows:
        return None
    df = pd.DataFrame(
        [
            {
                "timestamp": pd.to_datetime(str(r.get("date"))),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r.get("volume") or 0),
            }
            for r in rows
            if r.get("date") and r.get("close") is not None
        ]
    )
    if df.empty:
        return None
    return df.sort_values("timestamp").reset_index(drop=True)


def fetch_silver_quote() -> dict | None:
    """SGE Ag99.99 实时报价 (金银比联动, E8)."""
    data = _run_script("jdjr_query_stock.py", ["quote", _SILVER_CODE, *_claw_arg()], timeout=_QUOTE_TIMEOUT)
    if not data:
        return None
    d = data.get("data") or {}
    price = _to_float(d.get("currentPrice"))
    if price is None or price <= 0:
        return None
    return {
        "price": price,
        "open": _to_float(d.get("open")),
        "high": _to_float(d.get("maxPrice")),
        "low": _to_float(d.get("minPrice")),
        "prev_close": _to_float(d.get("closedYesterday")),
        "volume": d.get("volume"),
        "source": SOURCE_NAME,
    }


# ═══════════════════════════════════════════════════════════════
# 免登录 — 资讯 / 大V排行 / 资金炸弹 (P1/P3)
# ═══════════════════════════════════════════════════════════════

def fetch_news(keyword: str = "黄金", size: int = 5) -> list[dict] | None:
    """京东金融黄金资讯/快讯 (jdjr_query_news --no-flash 免登录, 合并去重).

    返回 [{time, title, content, url}]; 失败返回 None。
    """
    data = _run_script(
        "jdjr_query_news.py", [keyword, str(size), "--no-flash", *_claw_arg()]
    )
    if not data:
        return None
    news = ((data.get("data") or {}).get("news")) or []
    return [n for n in news if isinstance(n, dict)]


def fetch_blogger_trend(query: str = "黄金大V持仓排行") -> dict | None:
    """黄金大V排行 (query_blogger_trend 免登录, 加仓/持仓榜作散户情绪代理).

    返回 data: {rankMode, rankings: [{title, items: [{rank, userName, holdGram, ...}]}]}
    """
    data = _run_script("query_blogger_trend.py", [query, *_claw_arg()])
    if not data:
        return None
    return data.get("data")


def fetch_bomb(mode: str = "latest") -> dict | None:
    """资金炸弹/大单资金流 (query_gold_analysis 免登录).

    mode='latest' → data.items (大单列表); mode='history' → data.groups (历史分组)。
    """
    query = "资金炸弹历史" if mode == "history" else "资金炸弹"
    data = _run_script("query_gold_analysis.py", [query, *_claw_arg()])
    if not data:
        return None
    return data.get("data")


# ═══════════════════════════════════════════════════════════════
# 需登录 — 账户对账 (P2: gold_cmd sync)
# ═══════════════════════════════════════════════════════════════

def check_login() -> tuple[bool, dict]:
    """检查 jdgold 登录态 (jos.py token --json). 返回 (logged_in, info).

    info: {logged_in, remaining_sec, remaining_human, reason}
    """
    scripts = _scripts_dir()
    if scripts is None:
        return False, {"logged_in": False, "reason": "jdgold 脚本未部署"}
    try:
        proc = subprocess.run(
            [sys.executable, str(scripts / "jos.py"), "token", "--json", *_claw_arg()],
            cwd=str(scripts),
            capture_output=True,
            text=True,
            timeout=10.0,
        )
        info = json.loads(proc.stdout or "{}")
        logged = bool(info.get("logged_in", proc.returncode == 0))
        return logged, info
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return False, {"logged_in": False, "reason": "检查失败"}


def fetch_conditional_orders(status: str = "all") -> dict | None:
    """条件单 (query_conditional_orders --status all --json, 需登录).

    返回 {bank_code: raw BFF response}; 失败返回 None。
    """
    data = _run_script(
        "query_conditional_orders.py", ["--status", status, "--json", *_claw_arg()]
    )
    if not data:
        return None
    return data


def fetch_holdings() -> dict | None:
    """持仓/收益 (holdings_entry --intent holdings --json, 需登录).

    holdings_entry 必须传 --parse (用户原文) 否则打印帮助并退出。
    返回 data: {holdingList: [{bankCode, bankName, totalGram, avgCostPrice, totalIncome}],
                 totalGramAll, avgCostPrice}
    """
    data = _run_script(
        "holdings_entry.py",
        ["--parse", "查询我的黄金持仓", "--intent", "holdings", "--json", *_claw_arg()],
    )
    if not data:
        return None
    # _run_script 返回 {"view", "intent", "session_pin", "data": {持仓 dict}}
    return data.get("data") or {}


def fetch_trade_records() -> dict | None:
    """交易记录 (query_trade_records --json, 需登录).

    返回 {"sum": {type: {number, amount}}, "list": [raw BFF order]}
    """
    data = _run_script("query_trade_records.py", ["--json", *_claw_arg()])
    if not data:
        return None
    return data


# ═══════════════════════════════════════════════════════════════
# 需登录 — 模拟盘 (P3: sim 沙盒, 本机交互用)
# ═══════════════════════════════════════════════════════════════

def _run_sim(args: list[str], timeout: float = _SCRIPT_TIMEOUT) -> dict | None:
    # --json / --claw 是 query_sim_contest 全局参数, 须在子命令之前
    # query_sim_contest --json 输出扁平 dict (无 success/data 包装), 直接返回
    data = _run_script("query_sim_contest.py", ["--json", *_claw_arg(), *args], timeout=timeout)
    if not data:
        return None
    return data


def fetch_sim_account(account_type: int = 1) -> dict | None:
    """模拟账户 (可用额度/持仓/总资产)."""
    return _run_sim(["account", "--account-type", str(account_type)])


def fetch_sim_kline(unique_code: str = "WG-JDAU", k_type: str = "day", nums: int = 30) -> dict | None:
    """模拟盘 K 线."""
    return _run_sim(["kline", "--unique-code", unique_code, "--k-type", k_type, "--nums", str(nums)])


def fetch_sim_records(account_type: int = 1) -> dict | None:
    """模拟交易记录."""
    return _run_sim(["records", "--account-type", str(account_type)])


def sim_buy(trade_unit: int, bus_id: str, trade_amount: float | None = None, trade_gram: float | None = None) -> dict | None:
    """模拟买入 (幂等 bus_id)."""
    args = ["buy", "--trade-unit", str(trade_unit), "--bus-id", bus_id]
    if trade_amount is not None:
        args += ["--trade-amount", str(trade_amount)]
    if trade_gram is not None:
        args += ["--trade-gram", str(trade_gram)]
    return _run_sim(args)


def sim_sell(trade_unit: int, bus_id: str, trade_gram: float | None = None, trade_ratio: float | None = None) -> dict | None:
    """模拟卖出 (幂等 bus_id)."""
    args = ["sell", "--trade-unit", str(trade_unit), "--bus-id", bus_id]
    if trade_gram is not None:
        args += ["--trade-gram", str(trade_gram)]
    if trade_ratio is not None:
        args += ["--trade-ratio", str(trade_ratio)]
    return _run_sim(args)
