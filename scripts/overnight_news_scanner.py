#!/usr/bin/env python3
"""盘前隔夜新闻扫描 — 北京时间 7:30 运行 → 扫描隔夜重大事件 → Hermes → 个人微信.

Hermes 约定:
  - 有发现: stdout 打印人话卡片, exit 0
  - 无重大发现: stdout 为空, exit 0 (静默, 不打扰)
  - 错误: stderr 打印, exit 1

覆盖:
  1. 国际新闻 (anysearch API 直调, 覆盖 P0 主题)
  2. 国内新闻 (sentinel news_monitor)
  3. 日历事件 (overnight 新增 geo/policy_shift + 今日即将发生)
  4. 当前报价

用法:
  PYTHONPATH=src python3 scripts/overnight_news_scanner.py

cron (北京时间 7:30, Mon-Fri):
  30 7 * * 1-5 cd /path/to/ai-gold-miner && PYTHONPATH=src python3 scripts/overnight_news_scanner.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from gold_miner.sentinel.models import symbol_cn

BEIJING = timezone(timedelta(hours=8))


def _send_hermes(message: str) -> bool:
    """发送 macOS 桌面通知 (通过 osascript).

    Hermes gateway weixin 需额外配置，当前使用 macOS 原生通知作为可靠 fallback。
    """
    try:
        lines = message.strip().split("\n")
        title = lines[0][:100] if lines else "黄金早报"
        body = "\n".join(lines[1:5])[:200] if len(lines) > 1 else ""
        title_clean = title.replace('"', "'").replace("\\", "")
        body_clean = body.replace('"', "'").replace("\\", "")
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{body_clean}" with title "{title_clean}" sound name "Glass"'],
            capture_output=True, timeout=10,
        )
        return True
    except Exception:
        return False


# ── AI 判断 (优先用本地 DeepSeek Proxy, 直连 fallback) ──
_DS_PROXY_URL = "http://127.0.0.1:15800/v1/chat/completions"
_DS_DIRECT_URL = "https://api.deepseek.com/v1/chat/completions"
_DS_API_KEY = os.environ.get(
    "DEEPSEEK_API_KEY",
    "sk-7a3d662abcb443baa2d9d17a4d7bfd2b",
)
AI_MODEL = "deepseek-v4-flash"  # 分类任务用 flash, 快且便宜
AI_JUDGE_TIMEOUT = 30  # 秒

# ── 搜索配置 ──
ANYSEARCH_CLI = Path(os.path.expanduser(
    "~/.claude/skills/anysearch/scripts/anysearch_cli.py"
))
LAST30DAYS_CLI = Path(os.path.expanduser(
    "~/.claude/skills/last30days-cn/scripts/last30days.py"
))
ANYSEARCH_API_URL = "https://api.anysearch.com/v1/search"
ANYSEARCH_TIMEOUT = 15  # 秒
LAST30DAYS_TIMEOUT = 30  # 秒 (爬虫慢)
DDG_TIMEOUT = 10  # 秒
MAX_NEWS_PER_TOPIC = 3   # 每个主题最多展示条数

# P0 搜索主题 (与 deep_news.py _SEARCH_TOPICS 保持同步)
# queries = anysearch 英文查询, cn_keywords = last30days-cn 中文关键词 (fallback)
P0_QUERIES: list[dict] = [
    # ── 地缘冲突 ──
    {
        "label": "美伊冲突",
        "emoji": "🇺🇸🇮🇷",
        "queries": [
            "US Iran war military strikes ceasefire latest",
            "Iran Strait of Hormuz oil tanker attack",
        ],
        "cn_keywords": "美伊冲突 霍尔木兹 空袭",
    },
    {
        "label": "以色列-胡塞-红海",
        "emoji": "🇮🇱🇾🇪",
        "queries": [
            "Israel Houthi Yemen strike Red Sea blockade",
        ],
        "cn_keywords": "以色列 胡塞 也门 红海 封锁",
    },
    {
        "label": "停火与外交",
        "emoji": "🤝",
        "queries": [
            "US Iran ceasefire truce peace talks Pakistan Qatar mediation",
        ],
        "cn_keywords": "美伊 停火 和谈 调停 巴基斯坦",
    },
    # ── 宏观 ──
    {
        "label": "美联储政策",
        "emoji": "🏛️",
        "queries": [
            "Federal Reserve FOMC interest rate decision outlook",
        ],
        "cn_keywords": "美联储 FOMC 利率 加息 降息",
    },
    # ── 市场 ──
    {
        "label": "黄金市场",
        "emoji": "🥇",
        "queries": [
            "gold price XAUUSD surge drop forecast",
        ],
        "cn_keywords": "金价 XAUUSD 黄金ETF 央行购金",
    },
    {
        "label": "能源与油价",
        "emoji": "🛢️",
        "queries": [
            "crude oil Brent WTI price surge disruption",
        ],
        "cn_keywords": "原油 油价 布伦特 霍尔木兹",
    },
]


def _now() -> datetime:
    return datetime.now(BEIJING)


def _today_str() -> str:
    return _now().strftime("%Y-%m-%d")


def _fetch_price_jd() -> dict | None:
    """获取积存金当前价."""
    try:
        from gold_miner.sentinel.quotes import _fetch_jd_gold
        return _fetch_jd_gold()
    except Exception:
        return None


def _fetch_price_xauusd() -> dict | None:
    """获取 XAUUSD 价格 (新浪财经)."""
    try:
        resp = httpx.get(
            "https://hq.sinajs.cn/list=hf_XAU",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn/",
            },
            timeout=8.0,
        )
        if resp.status_code != 200:
            return None
        match = re.search(r'"([^"]+)"', resp.text)
        if not match:
            return None
        fields = match.group(1).split(",")
        if len(fields) < 8:
            return None
        price = float(fields[0])
        prev = float(fields[7]) if fields[7] and float(fields[7]) > 0 else float(fields[1])
        if price <= 0 or prev <= 0:
            return None
        return {
            "price": round(price, 2),
            "change_pct": round((price - prev) / prev * 100, 2),
        }
    except Exception:
        return None


_QUOTA_ERROR_KEYWORDS = [
    "daily_free_quota_exhausted", "free quota is exhausted",
    "quota exceeded", "rate limit",
]


def _is_quota_error(text: str) -> bool:
    """检测 anysearch 免费额度耗尽."""
    return any(kw in text.lower() for kw in _QUOTA_ERROR_KEYWORDS)


def _clean_title(text: str) -> str:
    """清理 anysearch 输出的标题——去掉 markdown 标记和 URL 噪声."""
    # 去掉 markdown 链接格式: [text](url) → text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # 去掉裸 URL
    text = re.sub(r'https?://\S+', '', text)
    # 去掉 markdown 标记
    text = re.sub(r'\*\*|__|#{1,6}\s*', '', text)
    text = re.sub(r'^[-*]\s+', '', text)
    # 去掉常见噪声
    for noise in ["Sign in", "Subscribe", "Advertisement", "SKIP ADVERTISEMENT",
                  "The New York Times", "Home Page", "Newsletters", "Cookie Settings",
                  "Accessibility", "Skip to content", "Newsletters", "TRENDING:"]:
        text = text.replace(noise, "")
    # 合并多余空格
    text = " ".join(text.split())
    return text.strip()


def _parse_anysearch_output(stdout: str) -> list[str]:
    """解析 anysearch CLI 输出 → 干净的「标题 — 摘要」列表."""
    results: list[str] = []
    for line in stdout.strip().split("\n"):
        line = line.strip()
        if not line or len(line) < 20:
            continue
        if _is_quota_error(line):
            continue
        if line.startswith("#") or line.startswith("---") or line.startswith("==="):
            continue
        # 跳过元数据行
        if any(kw in line.lower() for kw in [
            "search results", "ms)", "exhausted", "quota",
            "daily_free", "free quota",
        ]):
            continue

        # 尝试 JSON (anysearch v2 格式)
        try:
            item = json.loads(line)
            title = _clean_title(item.get("title", ""))
            snippet = item.get("snippet", item.get("description", ""))
            snippet = _clean_title(snippet)
            if title and not _is_quota_error(title):
                results.append(f"{title} — {snippet}"[:200])
            continue
        except (json.JSONDecodeError, TypeError):
            pass

        # 纯文本: 尝试提取 标题 — URL  — 摘要 结构
        cleaned = _clean_title(line)
        if cleaned and len(cleaned) > 15:
            results.append(cleaned[:200])

    return results


def _search_anysearch(query: str) -> list[str]:
    """调用 anysearch API (匿名, 限国际新闻)."""
    results: list[str] = []

    # 方式1: anysearch CLI (优先)
    if ANYSEARCH_CLI.exists():
        try:
            cp = subprocess.run(
                [
                    sys.executable, str(ANYSEARCH_CLI),
                    "search", query,
                    "--freshness", "day",
                    "--content_types", "news",
                    "--max_results", "5",
                ],
                capture_output=True, text=True, timeout=ANYSEARCH_TIMEOUT,
            )
            if cp.returncode == 0 and cp.stdout.strip():
                results = _parse_anysearch_output(cp.stdout)
        except (subprocess.TimeoutExpired, OSError):
            pass

    # 方式2: 直接 HTTP 调用 anysearch API
    if not results:
        try:
            params = urllib.parse.urlencode({
                "q": query,
                "freshness": "day",
                "content_types": "news",
                "max_results": 5,
            })
            resp = httpx.get(
                f"{ANYSEARCH_API_URL}?{params}",
                headers={"Accept": "application/json"},
                timeout=ANYSEARCH_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("results", data.get("data", []))[:5]:
                    title = item.get("title", "")
                    snippet = item.get("snippet", item.get("description", ""))
                    if title and not _is_quota_error(title):
                        results.append(f"{title} — {snippet}"[:200])
            elif resp.status_code == 429:
                pass  # 额度耗尽, 静默
        except Exception:
            pass

    return results[:MAX_NEWS_PER_TOPIC]


def _search_last30days(keyword: str) -> list[str]:
    """调用 last30days-cn (中文平台搜索), 作为 anysearch 额度耗尽时的 fallback."""
    results: list[str] = []
    if not LAST30DAYS_CLI.exists():
        return results
    try:
        cp = subprocess.run(
            [
                sys.executable, str(LAST30DAYS_CLI),
                keyword,
                "--emit", "compact",
                "--days", "2",
                "--quick",
            ],
            capture_output=True, text=True,
            timeout=LAST30DAYS_TIMEOUT,
            cwd=str(LAST30DAYS_CLI.parent),
        )
        if cp.returncode != 0 or not cp.stdout.strip():
            return results
        # 提取实质性新闻行 (过滤评分/分隔线/空行)
        for line in cp.stdout.strip().split("\n"):
            line = line.strip()
            if not line or len(line) < 20:
                continue
            if line.startswith("===") or line.startswith("---"):
                continue
            if line.startswith("正在") or line.startswith("完成"):
                continue
            if "条结果" in line or "日期范围" in line:
                continue
            # 保留有内容的中文行
            results.append(line[:200])
    except (subprocess.TimeoutExpired, OSError):
        pass
    return results[:MAX_NEWS_PER_TOPIC]


def _search_duckduckgo(query: str) -> list[str]:
    """DuckDuckGo 免费搜索 (无需 API Key), 作为 anysearch 额度耗尽时的国际新闻 fallback.

    使用 DDG Lite 版本 — 无 JS, 纯 HTML, 稳定可靠.
    """
    results: list[str] = []
    try:
        resp = httpx.get(
            "https://lite.duckduckgo.com/lite/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (compatible; GoldMiner/1.0)"},
            timeout=DDG_TIMEOUT,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return results
        # DDG Lite 返回 <a> 标签包含标题, <span class="link-text"> 包含 URL
        from html.parser import HTMLParser

        class DDGParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.results: list[str] = []
                self._in_link = False
                self._current_title = ""
                self._current_desc = ""
                self._in_desc = False
                self._desc_buf = ""
                self._seen_header = False

            def handle_starttag(self, tag, attrs):
                attrs_dict = dict(attrs)
                if tag == "a" and "result-link" in attrs_dict.get("class", ""):
                    self._in_link = True
                    self._current_title = ""
                    self._current_desc = ""
                cls = attrs_dict.get("class", "")
                if "result-snippet" in cls:
                    self._in_desc = True
                    self._desc_buf = ""

            def handle_data(self, data):
                if self._in_link:
                    self._current_title += data
                if self._in_desc:
                    self._desc_buf += data

            def handle_endtag(self, tag):
                if tag == "a" and self._in_link:
                    self._in_link = False
                if tag == "span" and self._in_desc:
                    self._in_desc = False
                    self._current_desc = self._desc_buf.strip()
                    if self._current_title.strip():
                        title = self._current_title.strip()
                        desc = self._current_desc[:120]
                        self.results.append(
                            f"{title} — {desc}" if desc else title
                        )

        parser = DDGParser()
        parser.feed(resp.text)
        results = parser.results

        # 正则 fallback: 如果 HTML 解析失败, 用简单正则
        if not results:
            import re as _re
            link_pattern = _re.compile(
                r'<a[^>]*result-link[^>]*>(.+?)</a>.*?<span[^>]*result-snippet[^>]*>(.+?)</span>',
                _re.DOTALL,
            )
            for m in link_pattern.finditer(resp.text):
                title = _re.sub(r'<[^>]+>', '', m.group(1)).strip()
                snippet = _re.sub(r'<[^>]+>', '', m.group(2)).strip()
                if title:
                    results.append(f"{title} — {snippet}"[:200])

    except Exception:
        pass
    return results[:MAX_NEWS_PER_TOPIC]


def _anysearch_quota_exhausted(findings: list[dict]) -> bool:
    """判断 anysearch 是否额度耗尽 (所有 P0 主题均无结果)."""
    if not findings:
        return True
    total_results = sum(len(f.get("results", [])) for f in findings)
    return total_results == 0


def _scan_international_news() -> list[dict]:
    """扫描 P0 国际新闻 — 三级 fallback.

    anysearch (国际, API) → 额度耗尽 → DuckDuckGo (国际, 免费) + last30days-cn (国内中文)
    """
    now = _now()
    date_filter = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    findings: list[dict] = []
    anysearch_dry = True  # 假设 anysearch 可能没额度

    # 第一轮: anysearch 国际新闻
    for topic in P0_QUERIES:
        all_results: list[str] = []
        for q in topic["queries"]:
            query_with_date = f"{q} after:{date_filter}"
            results = _search_anysearch(query_with_date)
            all_results.extend(results)
            if len(all_results) >= MAX_NEWS_PER_TOPIC:
                break

        if all_results:
            anysearch_dry = False
            findings.append({
                "label": topic["label"],
                "emoji": topic["emoji"],
                "results": all_results[:MAX_NEWS_PER_TOPIC],
                "source": "anysearch",
            })

    # anysearch 有结果 → 直接返回, 不走 fallback
    if not anysearch_dry and findings:
        return findings

    # 第二轮: anysearch 额度耗尽 → DuckDuckGo (国际英文) + last30days-cn (国内中文)
    ddg_findings: list[dict] = []
    cn_findings: list[dict] = []

    for topic in P0_QUERIES:
        # DuckDuckGo 搜英文关键词
        ddg_results: list[str] = []
        for q in topic["queries"]:
            ddg_results.extend(_search_duckduckgo(q))
            if len(ddg_results) >= MAX_NEWS_PER_TOPIC:
                break
        if ddg_results:
            ddg_findings.append({
                "label": f"{topic['label']} (DDG)",
                "emoji": topic["emoji"],
                "results": ddg_results[:MAX_NEWS_PER_TOPIC],
                "source": "DuckDuckGo",
            })

        # last30days-cn 搜中文关键词
        cn_keyword = topic.get("cn_keywords", "")
        if cn_keyword:
            cn_results = _search_last30days(cn_keyword)
            if cn_results:
                cn_findings.append({
                    "label": f"{topic['label']} (国内源)",
                    "emoji": topic["emoji"],
                    "results": cn_results[:MAX_NEWS_PER_TOPIC],
                    "source": "last30days-cn",
                })

    # 合并 DuckDuckGo + last30days-cn
    if ddg_findings or cn_findings:
        return ddg_findings + cn_findings

    return []


def _scan_chinese_news() -> list[str]:
    """扫描中文突发新闻 (sentinel news_monitor)."""
    try:
        from gold_miner.sentinel.news_monitor import run_news_check
        result = run_news_check()
        if not result or not result.strip():
            return []
        # 提取关键行
        raw_lines = result.split("\n")
        lines = [line.strip() for line in raw_lines
                 if line.strip() and not line.startswith("#")]
        return lines[:10]
    except Exception:
        return []


def _check_calendar_overnight() -> list[dict]:
    """检查日历 — overnight 新增事件 + 今日即将发生."""
    events: list[dict] = []
    try:
        from gold_miner.data.calendar import EventCalendar
        cal = EventCalendar()

        # 近期无结果的事件
        pending = cal.get_recently_published_without_result(lookback_days=2)
        for e in pending:
            events.append({
                "type": "pending_result",
                "name": e.name,
                "scheduled": e.scheduled_at.isoformat() if e.scheduled_at else "?",
                "impact": e.impact.value if hasattr(e.impact, 'value') else str(e.impact),
            })

        # 今日事件
        today = cal.get_today()
        for e in today:
            events.append({
                "type": "today",
                "name": e.name,
                "scheduled": e.scheduled_at.isoformat() if e.scheduled_at else "?",
                "impact": e.impact.value if hasattr(e.impact, 'value') else str(e.impact),
            })
    except Exception:
        pass
    return events


def _judge_with_ai(findings: list[dict]) -> list[dict]:
    """用 DeepSeek AI 判断每条新闻对金价的影响方向.

    两级端点: 本地 proxy (127.0.0.1:15800) → 直连 (API Key).
    AI 不可用时降级为主题感知关键词.
    """
    if not findings:
        return findings

    # 构建 prompt
    items_text = []
    for f in findings:
        snippets = " | ".join(f["results"][:2])
        for noise in [
            "Sign in", "Subscribe", "Advertisement", "SKIP ADVERTISEMENT",
            "Home Page", "Cookie Settings", "Accessibility", "Skip to content",
            "The New York Times", "Newsletters", "TRENDING:",
        ]:
            snippets = snippets.replace(noise, "")
        snippets = re.sub(r"https?://\S+", "", snippets)
        snippets = re.sub(r"[-*]{2,}", "", snippets)
        snippets = " ".join(snippets.split())[:250]
        items_text.append(f"【{f['label']}】{snippets}")

    prompt = (
        "你是黄金分析师。判断以下隔夜新闻对金价(XAUUSD)的影响方向。\n\n"
        "规则:\n"
        " bullish(看多): 地缘升级、战争扩大、央行鸽派/降息、经济衰退、避险↑\n"
        " bearish(看空): 停火/外交突破、美联储鹰派/加息、美元走强、避险↓\n"
        " neutral(中性): 信息不明确、多空抵消、纯报道无方向\n"
        " 二阶效应: 油价暴涨虽短期利多,但推高通胀→美联储难降息→中长期利空\n"
        " 外交/停火类: 提出方案/谈判/调停=缓和信号→看空或中性,不是看多\n\n"
        "新闻:\n"
        + "\n".join(items_text) + "\n\n"
        '返回纯JSON(无markdown代码块):\n'
        '{"judgments":[{"label":"主题","dir":"bullish|bearish|neutral","conf":0.5,"why":"<20字中文"}]}'
    )

    try:
        # 两级: 本地 proxy → 直连
        endpoints = [
            (_DS_PROXY_URL, {}),
            (_DS_DIRECT_URL, {"Authorization": f"Bearer {_DS_API_KEY}"}),
        ]
        resp = None
        last_error = ""
        for url, extra_headers in endpoints:
            try:
                headers = {"Content-Type": "application/json", **extra_headers}
                resp = httpx.post(
                    url, headers=headers,
                    json={
                        "model": AI_MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.0,
                        "max_tokens": 2048,
                        "thinking": {"type": "disabled"},
                    },
                    timeout=AI_JUDGE_TIMEOUT,
                )
                if resp.status_code == 200:
                    break
                last_error = f"{url} → {resp.status_code}"
            except Exception as exc:
                last_error = f"{url} → {exc}"
                continue

        if not resp or resp.status_code != 200:
            raise RuntimeError(last_error or "no endpoint")

        content = resp.json()["choices"][0]["message"]["content"]
        # DeepSeek 思考模式下 content 可能为空, 从 reasoning_content 提取
        if not content:
            content = resp.json()["choices"][0]["message"].get("reasoning_content", "")
        if not content:
            raise RuntimeError("empty response from AI")
        content = re.sub(r"```json\s*", "", content)
        content = re.sub(r"```\s*", "", content).strip()
        result = json.loads(content)

        dir_map = {"bullish": "🟢 看多", "bearish": "🔴 看空", "neutral": "🟡 中性"}
        for f in findings:
            label = f["label"]
            match = None
            for j in result.get("judgments", []):
                if j.get("label", "") in label:
                    match = j
                    break
            if match:
                f["gold_impact"] = dir_map.get(match.get("dir", ""), "⚪ 不确定")
                f["confidence"] = float(match.get("conf", 0.5))
                f["ai_reason"] = str(match.get("why", ""))[:40]
            else:
                f["gold_impact"] = "⚪ 不确定"
                f["confidence"] = 0.0
                f["ai_reason"] = ""
    except Exception as e:
        print(f"[warn] AI judge failed: {e}, using fallback", file=sys.stderr, flush=True)
        _fallback_impact(findings)

    return findings


def _fallback_impact(findings: list[dict]) -> None:
    """AI 不可用时的降级判断——主题感知关键词 (比纯关键词准一点)."""
    # 主题级默认方向偏差
    topic_bias: dict[str, str] = {
        "停火": "neutral",  # 停火外交类涉及谈判→偏中性
        "美联储": "neutral",  # 政策类大多模糊
        "黄金": "neutral",
    }

    bullish_kw = [
        "war", "strike", "attack", "escalat", "tension", "blockade",
        "nuclear", "sanction", "dovish", "rate cut", "recession",
        "冲突", "空袭", "战争", "升级", "封锁",
    ]
    bearish_kw = [
        "ceasefire reached", "ceasefire agreed", "peace deal", "de-escalat",
        "hawkish", "rate hike", "tightening",
        "停火达成", "停火协议", "和平协议",
    ]
    # 这些词在停火/外交主题下代表缓和→偏看空
    deescalation_kw = [
        "propose", "proposal", "mediation", "mediator", "talk", "negotiation",
        "resume", "diploma", "方案", "谈判", "调停", "斡旋", "外交",
    ]

    for f in findings:
        label = f["label"]
        all_text = " ".join(f["results"]).lower()

        bull = sum(1 for kw in bullish_kw if kw.lower() in all_text)
        bear = sum(1 for kw in bearish_kw if kw.lower() in all_text)
        deesc = sum(1 for kw in deescalation_kw if kw.lower() in all_text)

        # 主题偏差
        bias = topic_bias.get(label[:2], "")
        if bias == "neutral" and deesc > 0:
            bear += deesc  # 外交缓和信号→偏看空

        if bull > bear + 2:
            f["gold_impact"] = "🟢 看多"
        elif bear > bull + 1:
            f["gold_impact"] = "🔴 看空"
        elif bull > 0 or bear > 0:
            f["gold_impact"] = "🟡 中性"
        else:
            f["gold_impact"] = "⚪ 不确定"
        f["confidence"] = 0.4
        f["ai_reason"] = "关键词判断(AI不可用)"


# ── 隔夜美盘传导卡 ──
# 背景 (2026-07-24): 隔夜美盘暴跌 2.4% 直接传导为次日积存金低开，
# 盘前 7:30 需要的不只是「发生了什么」，而是「性质 + 对我仓位意味着什么」。

_TRANSMISSION_THRESHOLD = 1.0  # XAUUSD 隔夜 |涨跌幅| ≥ 1% 才触发

# 驱动渠道分类: (渠道ID, 关键词, 渠道标签, 下跌启示, 上涨启示)
_CHANNEL_RULES = [
    (
        ["原油", "油价", "wti", "布伦特", "brent", "霍尔木兹", "hormuz",
         "油轮", "opec", "crude", "tanker"],
        "油价冲击 → 通胀预期→加息预期",
        "一次性脉冲+支撑区=洗盘概率高，关注低吸；若油价持续高位则滞胀逻辑接管",
        "加息压力缓和，涨势更可持续",
    ),
    (
        ["初请", "非农", "cpi", "pce", "加息", "降息", "fed", "fomc",
         "鲍威尔", "warsh", "powell", "rate hike", "rate cut", "payroll", "jobless"],
        "利率预期直接驱动",
        "警惕趋势性压制，勿急于抄底，等事件落地",
        "降息预期升温，趋势性利多",
    ),
    (
        ["伊朗", "iran", "胡塞", "houthi", "红海", "red sea", "核", "nuclear",
         "trump", "特朗普", "停火", "ceasefire", "袭击", "attack"],
        "地缘避险驱动",
        "避险逻辑未变，回落是低吸机会；若局势缓和则回吐",
        "避险需求升温，但追高风险大，等回落",
    ),
]


def _classify_channel(intl_findings: list[dict]) -> tuple[str, str, str]:
    """从隔夜新闻 findings 分类驱动渠道. 返回 (标签, 下跌启示, 上涨启示)."""
    text = " ".join(
        f.get("label", "") + " " + " ".join(f.get("results", [])[:3])
        for f in intl_findings
    ).lower()
    for keywords, label, down_hint, up_hint in _CHANNEL_RULES:
        if any(kw in text for kw in keywords):
            return label, down_hint, up_hint
    return "未明确归因 (无匹配渠道)", "性质不明时按观望处理", "性质不明时勿追高"


def _orders_in_range(est_price: float, pct: float = 1.0) -> list[str]:
    """读取 active 条件单, 返回预计开盘价 ±pct% 射程内的条目描述."""
    path = Path("data/private/conditional_orders.jsonl")
    if not path.exists():
        return []
    hits: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if o.get("status") != "active":
            continue
        targets: list[tuple[str, float]] = []
        if o.get("type") == "limit_buy" and o.get("trigger_price"):
            targets.append(("买入", float(o["trigger_price"])))
        oco = o.get("oco") or {}
        for key, lbl in [("take_profit", "止盈"), ("stop_loss", "止损")]:
            price = (oco.get(key) or {}).get("price")
            if price:
                targets.append((lbl, float(price)))
        for lbl, tp in targets:
            dist = (est_price - tp) / tp * 100
            if abs(dist) <= pct:
                hits.append(f"{o.get('id', '?')} {lbl}¥{tp:.0f} (距离{dist:+.1f}%)")
    return hits


def _portfolio_line(est_price: float) -> str | None:
    """持仓成本 vs 预计开盘价."""
    try:
        import yaml

        data = yaml.safe_load(
            Path("data/private/portfolio.yaml").read_text(encoding="utf-8")
        )
        cost = float(data["positions"]["gold_jd"]["avg_cost"])
        pnl = (est_price - cost) / cost * 100
        return f"持仓: 成本¥{cost:.0f} | 预计开盘浮动盈亏 {pnl:+.1f}%"
    except Exception:
        return None


def _build_transmission_card(
    xauusd: dict | None,
    jd_price: dict | None,
    intl_findings: list[dict],
) -> list[str]:
    """隔夜美盘 → 次日积存金开盘传导卡. |涨跌幅| < 阈值时不触发."""
    if not xauusd:
        return []
    chg = xauusd["change_pct"]
    if abs(chg) < _TRANSMISSION_THRESHOLD:
        return []

    direction_txt = "低开" if chg < 0 else "高开"
    channel_label, down_hint, up_hint = _classify_channel(intl_findings)
    hint = down_hint if chg < 0 else up_hint

    direction_word = "上涨" if chg >= 0 else "下跌"
    lines = [
        "━━━ 🌊 隔夜美盘传导 ━━━",
        f"{symbol_cn('XAUUSD')}隔夜{direction_word} {abs(chg):.2f}%（现 {xauusd['price']:.0f} 美元）",
    ]
    est_open: float | None = None
    if jd_price and jd_price.get("price"):
        est_open = round(jd_price["price"] * (1 + chg / 100), 2)
        lines.append(
            f"{symbol_cn('积存金(MS)')}预计{direction_txt}: {jd_price['price']:.2f} → ~{est_open:.2f}元/克"
        )
    lines.append(f"驱动渠道: {channel_label}")
    if est_open:
        hits = _orders_in_range(est_open)
        if hits:
            lines.append("⚠️ 条件单射程 (±1%):")
            for h in hits:
                lines.append(f"  • {h}")
        port = _portfolio_line(est_open)
        if port:
            lines.append(port)
    lines.append(f"💬 {hint}")
    lines.append("")
    return lines


def _format_report(
    intl_findings: list[dict],
    cn_news: list[str],
    calendar_events: list[dict],
    jd_price: dict | None,
    xauusd: dict | None,
) -> str:
    """格式化为盘前简报."""
    now = _now()
    lines = [
        f"🌅 盘前扫描 | {now.strftime('%Y-%m-%d %H:%M')} 北京时间",
        "",
    ]

    # ── 行情快照 ──
    if xauusd or jd_price:
        lines.append("━━━ 💰 当前报价 ━━━")
        if xauusd:
            emoji = "🔴" if xauusd["change_pct"] < 0 else "🟢"
            lines.append(
                f"{symbol_cn('XAUUSD')}: {xauusd['price']:.0f} 美元 "
                f"({xauusd['change_pct']:+.2f}%) {emoji}"
            )
        if jd_price:
            lines.append(
                f"{symbol_cn('积存金(MS)')}: {jd_price['price']:.2f}元/克"
            )
        lines.append("")

    # ── 隔夜美盘传导卡 (|chg|≥1% 才出现) ──
    lines.extend(_build_transmission_card(xauusd, jd_price, intl_findings))

    # ── 国际重大新闻 ──
    if intl_findings:
        source_tag = intl_findings[0].get("source", "anysearch")
        source_icon = {
            "anysearch": "🌐", "DuckDuckGo": "🦆", "last30days-cn": "🇨🇳",
        }.get(source_tag, "🌐")
        lines.append(f"━━━━ 🔴 隔夜重大事件 ({source_icon} {source_tag}) ━━━━")
        for f in intl_findings:
            conf = f.get("confidence", 0)
            conf_str = f" {int(conf*100)}%" if conf > 0 else ""
            reason = f.get("ai_reason", "")
            lines.append(f"{f['emoji']} {f['label']} → {f.get('gold_impact', '⚪')}{conf_str}")
            if reason:
                lines.append(f"  💬 {reason}")
            for r in f["results"][:2]:
                # 清理: 去 URL, 去 markdown, 截断
                clean = re.sub(r'https?://\S+', '', r)
                clean = re.sub(r'[-*]{2,}', '', clean)
                clean = " ".join(clean.split())
                lines.append(f"  📰 {clean[:140]}")
            lines.append("")

    # ── 国内突发新闻 ──
    if cn_news:
        lines.append("━━━━ 🟡 国内快讯 ━━━━")
        for n in cn_news[:5]:
            lines.append(f"  • {n[:150]}")
        lines.append("")

    # ── 日历事件 ──
    today_events = [e for e in calendar_events if e["type"] == "today"]
    pending_events = [e for e in calendar_events if e["type"] == "pending_result"]

    if today_events or pending_events:
        lines.append("━━━━ 📅 今日事件 ━━━━")
        for e in today_events[:5]:
            lines.append(f"  • {e['name']} | 影响: {e['impact']}")
        if pending_events:
            lines.append("")
            lines.append("⚠️ 待查事件 (overnight 新增):")
            for e in pending_events[:3]:
                lines.append(f"  • {e['name']}")
        lines.append("")

    # ── 建议 ──
    lines.append("━━━━ 💡 操作提醒 ━━━━")
    lines.append("• 检查条件单是否需要更新")
    lines.append("• 如有重大事件, 设置开盘条件单")
    lines.append("• 今日 ECB 前注意仓位管理")
    lines.append("")
    lines.append(f"🤖 自动扫描 · {now.strftime('%H:%M')}")

    return "\n".join(lines)


def main() -> int:
    has_findings = False

    # 1. 行情
    xauusd = _fetch_price_xauusd()
    jd_price = _fetch_price_jd()

    # 2. 国际新闻
    intl_findings = _scan_international_news()
    if intl_findings:
        _judge_with_ai(intl_findings)
        has_findings = True

    # 3. 国内新闻
    cn_news = _scan_chinese_news()
    if cn_news:
        has_findings = True

    # 4. 日历
    calendar_events = _check_calendar_overnight()

    # 只有实际发现才推送 (日历事件有 today 也算; 隔夜美盘大幅传导也算)
    today_has_events = any(e["type"] == "today" for e in calendar_events)
    transmission = bool(
        xauusd and abs(xauusd["change_pct"]) >= _TRANSMISSION_THRESHOLD
    )
    if not has_findings and not today_has_events and not transmission:
        return 0  # 静默

    report = _format_report(intl_findings, cn_news, calendar_events, jd_price, xauusd)
    print(report, flush=True)

    # ── 发送到微信 ──
    _send_hermes(report)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"❌ 盘前扫描异常: {e}", file=sys.stderr)
        sys.exit(1)
