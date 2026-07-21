"""突发新闻监控 — 智能检测金价相关 breaking news.

v2 改进:
  1. NLP 否定句过滤 — "不会加息" ≠ 加息信号
  2. 时间过滤 — 仅处理 2h 内新闻，排除旧闻重推
  3. 市场联动 — 结合金价涨跌判断新闻是否已被定价
  4. 语义去重 — MD5 + 关键词重合度双重去重，6h TTL
  5. 扩展信源 — 新浪黄金+7×24+东财+金十数据
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

BEIJING = timezone(timedelta(hours=8))
_DEDUP_FILE = Path("/tmp/gold_news_dedup.json")
_DEDUP_TTL = 21600  # 6 小时去重
_NEWS_MAX_AGE = 7200  # 仅 2h 内新闻
_MIN_KEYWORD_OVERLAP = 0.6  # 去重: 关键词重合度阈值

# ── 否定句模式 (命中则跳过, 避免方向误判) ──
_NEGATION_PATTERNS: list[str] = [
    r"(不会|没有|否认|排除|暂不|未必|难以|不太可能|推迟|搁置|取消|叫停)"
    r"\s*({kw})",
    r"({kw}).*(不会|没有|否认|排除|暂缓|暂停)",
    r"(降溫|緩和|降溫|缓和|停火|协议达成|谈判取得)",
]

# ── 高优先级关键词 → (方向, 置信度, 类别) ──
_HIGH_IMPACT_RULES: list[tuple[str, str, str, str]] = [
    # (关键词正则, 金价影响说明, 类别, 级别)
    # ─ 地缘冲突 ─
    (r"美伊|美.*伊朗|伊朗.*美", "地缘升级→短期避险利多, 但油价↑→加息预期↑→净影响需判断", "geopolitical", "P0"),
    (r"霍尔木兹|海峡.*封锁|油轮.*爆炸|油轮.*触雷", "原油供应危机→油价↑→通胀↑→利空金价", "energy", "P0"),
    (r"空袭|导弹.*袭击|无人机.*攻击", "军事冲突升级→避险情绪↑", "geopolitical", "P0"),
    (r"宣战|全面.*战争|军事.*打击", "战争升级→极端避险→金价剧烈波动", "geopolitical", "P0"),
    (r"美军.*增派|航母.*部署|部队.*调动", "军事部署升级→地缘风险↑", "geopolitical", "P1"),
    (r"科威特|巴林|卡塔尔|约旦|阿联酋|沙特.*遭.*袭击", "冲突外溢→区域不稳定↑", "geopolitical", "P1"),
    # ─ 美联储 ─
    (r"美联储.*加息|Fed.*hike|FOMC.*加息", "加息→实际利率↑→强烈利空金价", "fed", "P0"),
    (r"美联储.*降息|Fed.*cut|FOMC.*降息", "降息→实际利率↓→强烈利多金价", "fed", "P0"),
    (r"美联储.*维持|按兵不动|hold.*rate", "维持利率→短期中性, 关注点阵图", "fed", "P1"),
    (r"沃什|Warsh|美联储主席", "联储主席讲话→政策信号→影响加息预期", "fed", "P0"),
    (r"点阵图|dot.plot|利率.*预测", "利率路径预期→中长期金价方向", "fed", "P1"),
    # ─ 宏观数据 ─
    (r"非农.*大幅|就业.*崩|失业.*飙升", "劳动力恶化→经济衰退→利多金价", "macro", "P0"),
    (r"CPI.*超预期|通胀.*超预期|PCE.*超预期", "通胀超预期→加息压力↑→利空金价", "macro", "P0"),
    (r"CPI.*低于预期|通胀.*放缓|PCE.*低于", "通胀回落→加息压力↓→利多金价", "macro", "P0"),
    # ─ 极端行情 ─
    (r"金价.*暴跌.*[5-9]%|黄金.*大跌.*[5-9]%|gold.*crash", "极端行情→恐慌抛售→关注止损", "market", "P0"),
    (r"金价.*暴涨.*[3-9]%|黄金.*大涨.*[3-9]%|gold.*surge", "极端行情→快速拉升→关注止盈", "market", "P0"),
    (r"金价.*跌破.*[34]\d{3}|gold.*below.*[34]\d{3}", "跌破关键位→技术面恶化", "market", "P0"),
    # ─ 央行 ─
    (r"央行.*购金.*[1-9]\d{2}吨|央行.*增持.*黄金", "央行购金→结构性利多→长期支撑", "central_bank", "P1"),
    (r"去美元化|de-dollarization|外汇储备.*黄金", "货币体系变化→长期利多金价", "central_bank", "P1"),
    # ─ 油价 ─
    (r"油价.*飙|原油.*暴涨|Brent.*[89]\d|WTI.*[89]\d", "能源危机→通胀预期↑→加息→利空金价", "energy", "P1"),
    (r"油价.*崩|原油.*暴跌|Brent.*跌破.*[56]\d", "能源降价→通胀降温→利多金价", "energy", "P1"),
    # ─ 贸易/制裁 ─
    (r"关税.*加征|trade.*war|贸易.*战|制裁.*升级", "贸易战升级→避险利多但通胀复杂", "trade", "P1"),
    (r"资本.*管制|外汇.*管制|资金.*封锁", "流动性危机→短期利空一切资产", "policy", "P1"),
    # ─ 印度黄金需求 (2026-07-22新增) ─
    (r"印度.*关税|India.*tariff|India.*duty.*gold", "印度关税调整→需求变化→边际影响金价", "india_gold", "P2"),
    (r"印度.*黄金.*进口|India.*gold.*import", "印度进口数据→全球第二大需求国动向", "india_gold", "P2"),
    # ─ 美国选举 (2026-07-22新增) ─
    (r"中期选举|midterm.*election|美国.*选举", "政策不确定性→避险买盘→利多金价", "election", "P1"),
    (r"民调|poll.*congress|选举.*民调", "选举民调→不确定性定价→影响金价", "election", "P1"),
]

# 中等优先级 — 仅做背景，不推送
_MED_IMPACT_PATTERNS: list[str] = [
    r"黄金ETF|GLD|SPDR|gold.*etf",
    r"COMEX.*持仓|CFTC.*黄金|投机.*仓位",
    r"美元指数|DXY|dollar.*index",
    r"美债.*收益|Treasury.*yield",
    r"金银比|gold.silver",
    r"技术分析|technical.*analysis.*gold",
]


def _make_negation_re(keyword: str) -> str:
    """构造否定句正则: '不会{keyword}' 等."""
    escaped = re.escape(keyword)
    patterns = [
        rf"(不|没有|否认|排除|暂不|未必|难以|不太可能|推迟|搁置|取消|叫停)\s*{escaped}",
    ]
    return "|".join(patterns)


def _is_negated(title: str, keyword: str) -> bool:
    """检查标题中关键词是否被否定."""
    patterns = [
        rf"(不|没有|否认|排除|暂不|未必|难以|不太可能|推迟|搁置|取消|叫停)\s*{re.escape(keyword)}",
        rf"{re.escape(keyword)}.*(?:不会|没有|否认|排除|暂缓|暂停|取消)",
    ]
    for pat in patterns:
        if re.search(pat, title):
            return True
    # 和谈/停火类标题 → 冲突降级, 不算地缘升级
    return bool(keyword in ("美伊", "伊朗", "空袭", "袭击", "战争", "封锁") and re.search(r"降溫|緩和|缓和|停火|协议.*达成|谈判.*取得.*进展|和谈|peace.*talk|ceasefire", title))


def _parse_sina_time(ctime_str: str) -> float | None:
    """解析新浪 ctime 为 Unix 时间戳."""
    if not ctime_str:
        return None
    try:
        ts = int(ctime_str)
        if ts > 1_000_000_000_000:
            ts = ts // 1000  # 毫秒 → 秒
        return float(ts)
    except (ValueError, TypeError):
        pass
    # 尝试解析常见日期格式
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%m-%d %H:%M:%S"]:
        try:
            dt = datetime.strptime(ctime_str, fmt)
            return dt.replace(tzinfo=BEIJING).timestamp()
        except ValueError:
            continue
    return None


def _keyword_overlap(title1: str, title2: str) -> float:
    """两条标题的关键词重合度 (0-1)."""
    # 简单分词: 取 2-gram 字符
    def _ngrams(s: str, n: int = 2) -> set[str]:
        s = re.sub(r"[^一-鿿\w]", "", s)
        return {s[i : i + n] for i in range(len(s) - n + 1)}

    g1 = _ngrams(title1)
    g2 = _ngrams(title2)
    if not g1 or not g2:
        return 0.0
    return len(g1 & g2) / min(len(g1), len(g2))


def fetch_gold_headlines() -> list[dict]:
    """多源抓取黄金相关头条 (国内可访问).

    数据源: 新浪黄金 + 7×24快讯 + 东方财富 + 金十数据
    """
    headlines: list[dict] = []
    now = time.time()

    def _fetch(url: str, source: str, parser, headers=None) -> int:
        try:
            h = headers or {"User-Agent": "Mozilla/5.0",
                           "Referer": "https://finance.sina.com.cn/"}
            resp = httpx.get(url, headers=h, timeout=8.0)
            if resp.status_code == 200:
                return parser(resp, source)
        except Exception:
            pass
        return 0

    # 1. 新浪黄金频道
    def _parse_sina(resp, src):
        added = 0
        for item in resp.json().get("result", {}).get("data", []):
            title = item.get("title", "").strip()
            ctime = item.get("ctime", "")
            ts = _parse_sina_time(ctime)
            if title and (ts is None or now - ts < _NEWS_MAX_AGE):
                headlines.append({
                    "title": title, "time": ctime, "ts": ts,
                    "source": src, "url": item.get("url", ""),
                })
                added += 1
        return added

    _fetch(
        "https://feed.mix.sina.com.cn/api/roll/get"
        "?pageid=153&lid=2516&k=&num=20&page=1",
        "新浪黄金", _parse_sina,
    )

    # 2. 7×24 快讯
    _fetch(
        "https://feed.mix.sina.com.cn/api/roll/get"
        "?pageid=154&lid=2637&k=&num=30&page=1",
        "7×24快讯", _parse_sina,
    )

    # 3. 东方财富黄金
    def _parse_em(resp, src):
        added = 0
        for item in resp.json().get("data", {}).get("diff", []):
            title = item.get("f14", "").strip()
            if title:
                headlines.append({
                    "title": title, "time": "", "ts": None,
                    "source": src, "url": "",
                })
                added += 1
        return added

    _fetch(
        "https://push2.eastmoney.com/api/qt/clist/get"
        "?np=1&fltt=2&fields=f13,f14&fid=f13&fs=m:116&pn=1&pz=15",
        "东方财富", _parse_em,
    )

    # 4. 金十数据 (flash news)
    def _parse_jin10(resp, src):
        added = 0
        try:
            data = resp.json()
            # jin10 API 格式: {data: [{content: "...", time: "..."}]}
            items = data if isinstance(data, list) else data.get("data", [])
            for item in items[:20]:
                title = (item.get("content") or item.get("title") or "").strip()
                if title:
                    headlines.append({
                        "title": title, "time": item.get("time", ""), "ts": None,
                        "source": src, "url": "",
                    })
                    added += 1
        except Exception:
            pass
        return added

    _fetch(
        "https://flash-api.jin10.com/get_flash_list?channel=-8200&vip=1&_="
        + str(int(now * 1000)),
        "金十数据", _parse_jin10,
    )

    return headlines


def analyze_headlines(
    headlines: list[dict],
    gold_change_pct: float = 0.0,
) -> list[dict]:
    """智能分析头条: 否定句过滤 + 时间过滤 + 语义去重 + 市场联动.

    Args:
        headlines: 新闻列表
        gold_change_pct: 当前金价日内涨跌幅 (用于市场联动)

    Returns:
        [{"title":..., "impact":..., "level":"P0"/"P1", "matched_kw":..., "category":...}]
    """
    dedup_cache = _load_dedup()
    now = time.time()
    alerts: list[dict] = []

    for h in headlines:
        title = h.get("title", "")
        if not title or len(title) < 6:
            continue

        # ── 时间过滤 ──
        ts = h.get("ts")
        if ts is not None and now - ts > _NEWS_MAX_AGE:
            continue

        # ── MD5 去重 ──
        thash = _title_hash(title)
        if thash in dedup_cache:
            continue

        # ── 关键词匹配 + 否定句过滤 ──
        for kw_regex, impact, category, level in _HIGH_IMPACT_RULES:
            try:
                match = re.search(kw_regex, title)
                if not match:
                    continue

                matched_text = match.group(0)
                # 否定句检查
                if _is_negated(title, matched_text):
                    continue

                # 去重: 检查与已有告警的关键词重合度
                is_dup = False
                for existing in alerts:
                    if _keyword_overlap(title, existing["title"]) > _MIN_KEYWORD_OVERLAP:
                        is_dup = True
                        break
                if is_dup:
                    continue

                # ── 市场联动: 调整优先级 ──
                final_level = level
                context = impact
                if abs(gold_change_pct) > 1.5:
                    if gold_change_pct < 0 and "利多" in impact:
                        context += f" (金价已跌{gold_change_pct:.1f}%, 可能尚未定价)"
                        final_level = "P0"  # 升级: 利多新闻+金价已跌=可能错杀
                    elif gold_change_pct > 0 and "利空" in impact:
                        context += f" (金价反涨{gold_change_pct:.1f}%, 市场可能已预期)"
                        final_level = "P1"  # 降级: 利空新闻+金价反涨=已被定价

                alerts.append({
                    "title": title,
                    "impact": context,
                    "level": final_level,
                    "category": category,
                    "matched_kw": matched_text,
                    "source": h.get("source", ""),
                    "hash": thash,
                })
                dedup_cache[thash] = now
                break
            except re.error:
                continue

    # 清理过期去重条目
    dedup_cache = {k: v for k, v in dedup_cache.items() if now - v < _DEDUP_TTL}
    _save_dedup(dedup_cache)

    # 排序: P0 在前, 同类归组
    alerts.sort(key=lambda a: (0 if a["level"] == "P0" else 1, a.get("category", "")))
    return alerts


def _load_dedup() -> dict[str, float]:
    if not _DEDUP_FILE.exists():
        return {}
    try:
        data = json.loads(_DEDUP_FILE.read_text())
        now = time.time()
        return {k: v for k, v in data.items() if now - v < _DEDUP_TTL}
    except Exception:
        return {}


def _save_dedup(cache: dict[str, float]) -> None:
    with contextlib.suppress(Exception):
        _DEDUP_FILE.write_text(json.dumps(cache))


def _title_hash(title: str) -> str:
    return hashlib.md5(title.strip().encode()).hexdigest()[:12]


def format_news_alerts(alerts: list[dict], gold_price: float = 0,
                       gold_change: float = 0) -> str:
    """格式化新闻告警为微信卡片."""
    if not alerts:
        return ""

    p0 = [a for a in alerts if a["level"] == "P0"]
    p1 = [a for a in alerts if a["level"] == "P1"]

    if not p0 and not p1:
        return ""

    lines = ["📰 突发新闻预警", ""]

    # 行情背景
    if gold_price > 0:
        emoji = "🔴" if gold_change < 0 else "🟢"
        lines.append(f"当前 XAUUSD: ${gold_price:.0f} ({emoji} {gold_change:+.1f}%)")

    categories = {"geopolitical": "地缘冲突", "fed": "美联储", "macro": "宏观数据",
                   "market": "极端行情", "energy": "能源危机", "central_bank": "央行动向",
                   "trade": "贸易制裁", "policy": "政策变动"}

    if p0:
        lines.append("")
        lines.append("🚨 重大突发:")
        for a in p0:
            cat = categories.get(a.get("category", ""), "")
            lines.append(f"  • [{cat}] {a['title'][:80]}")
            lines.append(f"    💡 {a['impact']}")

    if p1:
        lines.append("")
        lines.append("⚠️ 关注:")
        for a in p1:
            cat = categories.get(a.get("category", ""), "")
            lines.append(f"  • [{cat}] {a['title'][:80]}")

    lines.append("")
    sources = {a.get("source", "") for a in p0 + p1}
    lines.append(f"📡 {', '.join(sources)} | 🕐 自动监控")
    return "\n".join(lines)


def run_news_check() -> str:
    """执行一次新闻检查, 返回需推送的消息 (空=无异动).

    包含市场联动: 先获取金价, 再分析新闻.
    """
    # 获取当前金价 (用于市场联动)
    gold_price = 0.0
    gold_change = 0.0
    try:
        from .quotes import _from_sina
        xau = _from_sina()
        if xau:
            gold_price = xau["price"]
            gold_change = xau["change_pct"]
    except Exception:
        pass

    headlines = fetch_gold_headlines()
    alerts = analyze_headlines(headlines, gold_change_pct=gold_change)
    return format_news_alerts(alerts, gold_price=gold_price, gold_change=gold_change)
