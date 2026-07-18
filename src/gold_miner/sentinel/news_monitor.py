# -*- coding: utf-8 -*-
"""突发新闻监控 — 检测金价相关 breaking news.

策略:
  1. 抓取新浪财经黄金频道头条
  2. 关键词匹配 (地缘/美联储/宏观数据)
  3. 去重: 同标题 4h 内不重复推送
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx

BEIJING = timezone(timedelta(hours=8))
_DEDUP_FILE = Path("/tmp/gold_news_dedup.json")
_DEDUP_TTL = 14400  # 4 小时去重

# 高优先级关键词 → 金价影响方向
_HIGH_IMPACT_KEYWORDS: dict[str, str] = {
    # 地缘冲突 (短期避险利多, 但油价通道可能压制)
    "美伊": "地缘升级→短期避险利多, 但油价↑→加息预期↑→净影响复杂",
    "伊朗": "地缘",
    "霍尔木兹": "原油供应危机→油价↑→利空金价(加息逻辑)",
    "空袭": "军事冲突升级",
    "战争": "地缘风险",
    "袭击": "军事行动",
    "封锁": "供应中断",
    # 美联储 (直接影响加息预期)
    "美联储": "政策信号→直接影响金价",
    "加息": "利空金价",
    "降息": "利多金价",
    "利率": "政策方向",
    "FOMC": "政策会议",
    "沃什": "美联储主席",
    "Warsh": "Fed Chair",
    # 宏观数据 (影响加息概率)
    "非农": "就业数据→影响加息预期",
    "CPI": "通胀数据→影响加息预期",
    "PCE": "通胀数据→影响加息预期",
    "通胀": "宏观数据",
    # 央行购金
    "央行购金": "结构性利多",
    "黄金储备": "央行动向",
    # 极端行情
    "金价暴跌": "极端行情",
    "金价暴涨": "极端行情",
    "崩盘": "极端行情",
    # 油价 (通过通胀预期影响金价)
    "油价": "影响通胀预期→间接影响金价",
    "原油": "能源价格",
}

# 中等优先级 (仅作背景)
_MED_IMPACT_KEYWORDS: list[str] = [
    "黄金", "金价", "gold", "贵金属",
    "美元", "DXY", "dollar",
    "美债", "收益率", "yield",
    "ETF", "GLD", "SPDR",
]


def _load_dedup() -> dict[str, float]:
    """加载去重缓存."""
    if not _DEDUP_FILE.exists():
        return {}
    try:
        data = json.loads(_DEDUP_FILE.read_text())
        # 清理过期条目
        now = time.time()
        return {k: v for k, v in data.items() if now - v < _DEDUP_TTL}
    except Exception:
        return {}


def _save_dedup(cache: dict[str, float]) -> None:
    """保存去重缓存."""
    try:
        _DEDUP_FILE.write_text(json.dumps(cache))
    except Exception:
        pass


def _title_hash(title: str) -> str:
    """标题哈希用于去重."""
    return hashlib.md5(title.strip().encode()).hexdigest()[:12]


def fetch_gold_headlines() -> list[dict]:
    """抓取黄金相关头条新闻.

    数据源: 新浪财经黄金频道 + 7x24 快讯.
    """
    headlines: list[dict] = []

    # 1. 新浪财经黄金新闻
    try:
        resp = httpx.get(
            "https://feed.mix.sina.com.cn/api/roll/get"
            "?pageid=153&lid=2516&k=&num=15&page=1",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
            timeout=8.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get("result", {}).get("data", []):
                title = item.get("title", "")
                if title:
                    headlines.append({
                        "title": title.strip(),
                        "time": item.get("ctime", ""),
                        "source": "新浪黄金",
                        "url": item.get("url", ""),
                    })
    except Exception:
        pass

    # 2. 7x24 快讯 (新浪)
    try:
        resp = httpx.get(
            "https://feed.mix.sina.com.cn/api/roll/get"
            "?pageid=154&lid=2637&k=&num=20&page=1",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
            timeout=8.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get("result", {}).get("data", []):
                title = item.get("title", "")
                if title:
                    headlines.append({
                        "title": title.strip(),
                        "time": item.get("ctime", ""),
                        "source": "7×24快讯",
                        "url": item.get("url", ""),
                    })
    except Exception:
        pass

    # 3. 尝试东方财富黄金资讯
    try:
        resp = httpx.get(
            "https://push2.eastmoney.com/api/qt/clist/get"
            "?np=1&fltt=2&fields=f13,f14&fid=f13&fs=m:116&pn=1&pz=10",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get("data", {}).get("diff", []):
                title = item.get("f14", "")
                if title:
                    headlines.append({
                        "title": title.strip(),
                        "time": "",
                        "source": "东方财富",
                        "url": "",
                    })
    except Exception:
        pass

    return headlines


def analyze_headlines(headlines: list[dict]) -> list[dict]:
    """分析头条, 返回需推送的突发新闻.

    返回: [{"title":..., "impact":..., "reason":..., "level":"P0"/"P1"}]
    """
    dedup = _load_dedup()
    now = time.time()
    alerts: list[dict] = []

    for h in headlines:
        title = h.get("title", "")
        thash = _title_hash(title)
        if thash in dedup:
            continue  # 已推送过

        # 检查关键词
        for kw, impact_reason in _HIGH_IMPACT_KEYWORDS.items():
            if kw in title:
                level = "P0" if kw in ("美伊", "伊朗", "霍尔木兹", "美联储", "加息", "降息",
                                        "空袭", "金价暴跌", "金价暴涨", "崩盘", "战争") else "P1"
                alerts.append({
                    "title": title,
                    "impact": impact_reason,
                    "reason": f"关键词: {kw}",
                    "level": level,
                    "source": h.get("source", ""),
                    "hash": thash,
                })
                dedup[thash] = now
                break
        else:
            # 中等关键词 (仅 P2, 限 5 条)
            for kw in _MED_IMPACT_KEYWORDS:
                if kw.lower() in title.lower():
                    if sum(1 for a in alerts if a["level"] == "P2") < 5:
                        alerts.append({
                            "title": title,
                            "impact": "黄金相关新闻",
                            "reason": f"关键词: {kw}",
                            "level": "P2",
                            "source": h.get("source", ""),
                            "hash": thash,
                        })
                        dedup[thash] = now
                    break

    _save_dedup(dedup)
    return alerts


def format_news_alerts(alerts: list[dict]) -> str:
    """格式化新闻告警为微信卡片."""
    if not alerts:
        return ""

    p0 = [a for a in alerts if a["level"] == "P0"]
    p1 = [a for a in alerts if a["level"] == "P1"]

    if not p0 and not p1:
        return ""  # 仅 P2 不推送

    lines = ["📰 突发新闻预警", ""]

    if p0:
        lines.append("🚨 重大突发:")
        for a in p0:
            lines.append(f"  • {a['title']}")
            lines.append(f"    💡 {a['impact']}")

    if p1:
        lines.append("")
        lines.append("⚠️ 关注:")
        for a in p1:
            lines.append(f"  • {a['title']}")

    lines.append("")
    lines.append(f"📡 来源: {', '.join(set(a['source'] for a in p0 + p1))}")
    return "\n".join(lines)


def run_news_check() -> str:
    """执行一次新闻检查, 返回需推送的消息 (空=无异动)."""
    headlines = fetch_gold_headlines()
    alerts = analyze_headlines(headlines)
    return format_news_alerts(alerts)
