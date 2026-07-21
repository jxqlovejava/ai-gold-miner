"""深度新闻搜索 — 接入 anysearch + last30days-cn.

此模块在本地分析 pipeline 中调用, 提供 Hermes 端无法实现的深度搜索能力:
  - anysearch: 多搜索引擎实时搜索 (Google/Bing/DuckDuckGo)
  - last30days-cn: 中文媒体 30 天内报道聚合

用法:
  # 生成搜索查询 (供 Claude Code agent 执行)
  python -m src.gold_miner.sentinel --mode deep-news-queries

输出: JSON 格式的搜索查询列表, Claude Code agent 读取后调用 anysearch + last30days-cn
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))

# ── 搜索主题配置 ──
# 设计原则 (2026-07-21 修订):
#   1. 地缘冲突是多极点的——不能只搜美伊, 必须覆盖以色列/胡塞/沙特/红海等所有活跃战线
#   2. 外交与军事是对称维度——有冲突升级查询就必须有停火/调停查询, 否则单向盲区
#   3. 每新增一个区域冲突参与方, 其对应的中文关键词也必须同步更新
_SEARCH_TOPICS: list[dict] = [
    {
        "id": "geopolitical",
        "label": "地缘冲突 (美伊+中东全域)",
        "priority": "P0",
        "anysearch_queries": [
            "US Iran war latest military strikes today",
            "Iran Strait of Hormuz oil tanker blockade latest",
            "Middle East war escalation Israel Houthi Saudi latest",
        ],
        "last30days_keywords": [
            "美伊冲突 霍尔木兹 空袭",
            "中东局势 原油 金价 战争",
            "伊朗 美国 以色列 胡塞",
        ],
    },
    {
        "id": "israel_houthi",
        "label": "以色列-胡塞武装-也门",
        "priority": "P0",
        "anysearch_queries": [
            "Israel strikes Houthi Yemen Hodeidah latest",
            "Houthi Red Sea Bab el-Mandeb blockade Saudi",
            "Israel military operation Middle East escalation",
        ],
        "last30days_keywords": [
            "以色列 空袭 胡塞 也门 荷台达",
            "胡塞武装 曼德海峡 红海 封锁",
            "以色列 伊朗 中东 战争升级",
        ],
    },
    {
        "id": "ceasefire_diplomacy",
        "label": "停火谈判与外交调停",
        "priority": "P0",
        "anysearch_queries": [
            "US Iran ceasefire truce peace talks Pakistan mediation latest",
            "Middle East peace negotiation diplomatic breakthrough",
            "Iran ceasefire proposal Qatar Pakistan broker",
        ],
        "last30days_keywords": [
            "美伊 停火 和谈 调停 巴基斯坦",
            "伊朗 美国 谈判 外交 斡旋",
            "中东 停火协议 和平谈判",
        ],
    },
    {
        "id": "fed_policy",
        "label": "美联储政策",
        "priority": "P0",
        "anysearch_queries": [
            "Federal Reserve interest rate decision latest",
            "Fed Chair speech inflation outlook today",
            "CME FedWatch probability update",
        ],
        "last30days_keywords": [
            "美联储 加息 降息",
            "FOMC 利率决议",
            "沃什 美联储主席",
        ],
    },
    {
        "id": "macro_data",
        "label": "宏观数据",
        "priority": "P1",
        "anysearch_queries": [
            "US CPI PPI data release this week",
            "US jobless claims nonfarm payrolls latest",
            "US PCE inflation data",
        ],
        "last30days_keywords": [
            "美国CPI PPI 非农",
            "核心PCE 通胀数据",
            "初请失业金",
        ],
    },
    {
        "id": "gold_market",
        "label": "黄金市场",
        "priority": "P1",
        "anysearch_queries": [
            "gold price XAUUSD forecast analysis today",
            "gold ETF GLD flow institutional",
            "central bank gold buying reserves",
        ],
        "last30days_keywords": [
            "黄金ETF 资金流向",
            "央行购金 黄金储备",
            "金价 分析 预测 XAUUSD",
        ],
    },
    {
        "id": "energy_oil",
        "label": "能源与油价",
        "priority": "P1",
        "anysearch_queries": [
            "crude oil Brent WTI price latest",
            "oil supply disruption Hormuz Red Sea",
            "OPEC+ production decision",
        ],
        "last30days_keywords": [
            "原油 油价 布伦特",
            "霍尔木兹 曼德海峡 石油 供应",
        ],
    },
    {
        "id": "trade_sanctions",
        "label": "贸易与制裁",
        "priority": "P2",
        "anysearch_queries": [
            "US sanctions tariffs latest",
            "trade war tariffs escalation",
        ],
        "last30days_keywords": [
            "美国 制裁 关税 贸易战",
            "资本管制 外汇管制",
        ],
    },
]


def generate_search_plan() -> list[dict]:
    """生成搜索计划 — 供 Claude Code agent 执行.

    返回按优先级排序的搜索主题列表,
    每个主题包含 anysearch 和 last30days-cn 的搜索查询.
    """
    now = datetime.now(BEIJING)
    today_str = now.strftime("%Y-%m-%d")

    plan = []
    for topic in _SEARCH_TOPICS:
        # 为每个查询追加时间约束
        anysearch_qs = [
            q + f" after:{ (now - timedelta(days=2)).strftime('%Y-%m-%d') }"
            for q in topic["anysearch_queries"]
        ]
        plan.append({
            "id": topic["id"],
            "label": topic["label"],
            "priority": topic["priority"],
            "anysearch_queries": anysearch_qs,
            "last30days_keywords": topic["last30days_keywords"],
            "search_date": today_str,
        })

    return sorted(plan, key=lambda t: ["P0", "P1", "P2"].index(t["priority"]))


def format_deep_news_report(
    anysearch_results: dict[str, list[str]],
    last30days_results: dict[str, list[str]],
) -> str:
    """格式化深度新闻搜索结果为人话简报.

    Args:
        anysearch_results: {topic_id: [result_summaries]}
        last30days_results: {topic_id: [result_summaries]}
    """
    lines = ["🔍 深度新闻扫描", f"时间: {datetime.now(BEIJING).strftime('%Y-%m-%d %H:%M')}", ""]

    for topic in _SEARCH_TOPICS:
        tid = topic["id"]
        any_results = anysearch_results.get(tid, [])
        cn_results = last30days_results.get(tid, [])

        if not any_results and not cn_results:
            continue

        lines.append(f"━━━ {topic['label']} [{topic['priority']}] ━━━")

        if any_results:
            lines.append("🌐 国际源 (anysearch):")
            for r in any_results[:3]:
                lines.append(f"  • {r}")

        if cn_results:
            lines.append("🇨🇳 国内源 (last30days):")
            for r in cn_results[:3]:
                lines.append(f"  • {r}")

        lines.append("")

    if len(lines) <= 3:
        lines.append("✅ 未发现新的重大新闻")
    else:
        lines.append("💡 以上新闻需结合金价实时走势综合判断")

    return "\n".join(lines)


def print_search_plan() -> None:
    """输出搜索计划 (JSON), 供 Claude Code agent 读取."""
    plan = generate_search_plan()
    print(json.dumps(plan, ensure_ascii=False, indent=2))
