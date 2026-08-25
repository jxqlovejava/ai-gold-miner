"""消息面信号 — 事件检测+NLP摘要."""
from __future__ import annotations

import math
import re
from collections import Counter

from loguru import logger

from gold_miner.data.fact_checker import FactChecker, apply_fact_checks, format_verification_tag
from gold_miner.data.news import NewsFetcher, NewsItem
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength

# 地缘风险关键词 — 用于识别传统事实核查难以覆盖的突发性地缘事件
# 使用 tuple 保证不可变；使用词边界匹配避免 "war" 匹配 "forward" 等误触发
_GEOPOLITICAL_TRIGGERS: dict[str, tuple[str, ...]] = {
    "primary": (
        r"\biran\b", r"\bisrael\b", r"\bgaza\b", r"\bpalestine\b",
        r"\bhamas\b", r"\bhezbollah\b", r"\bhouthi\b", r"\byemen\b",
        r"\bukraine\b", r"\brussia\b", r"\bputin\b", r"\btaiwan\b",
        r"\bchina\b", r"\bnorth korea\b", r"\bhormuz\b",
        r"\bstrait of hormuz\b", r"\bmiddle east\b", r"\bgulf\b",
        r"\bsaudi\b", r"\bopec\b", r"\bgeopolitical\b",
        r"\bwar\b", r"\bconflict\b", r"\bmilitary attack\b",
        r"\bairstrike\b", r"\bceasefire\b", r"\bnegotiation\b",
        # 2026-08-25 补: 贸易战/制裁 (美加谈判破裂、美对伊经济制裁类事件)
        r"\bsanction\b", r"\bsanctions\b", r"\btariff\b", r"\btariffs\b",
        r"\btrade war\b", r"\btrade deal\b", r"\bcanada\b", r"\bmexico\b",
    ),
    "oil_link": (
        r"\boil\b", r"\bcrude\b", r"\bpetroleum\b", r"\bopec\b",
        r"\bhormuz\b", r"\bshipping\b",
    ),
    "safe_haven_link": (
        r"\bgold\b", r"\bsafe haven\b", r"\bsafe-haven\b",
        r"\btreasury\b", r"\bdollar\b",
    ),
    # 印度黄金需求关键词 (2026-07-22) — WGC 年中报告列为下半年金价变量
    "india_gold": (
        r"\bindia gold\b", r"\bindian gold\b", r"\bindia import tariff\b",
        r"\bindia gold duty\b", r"\bindia gold demand\b",
        r"\brupee gold\b", r"\bindia gold smuggling\b",
        r"\bindian wedding season gold\b",
    ),
}


# 金价追踪站点域名/来源名 — 这些不是新闻，是价格页面，但其内容含地缘关键词会被误判
_PRICE_TRACKER_DOMAINS: tuple[str, ...] = (
    "kitco", "bullionvault", "goldprice", "jmbullion", "goldcore",
    "gold-eagle", "tradingview", "fxstreet", "fxempire", "investing.com",
    "dailyfx", "tradingpedia", "invezz",
)


def _news_text(news: NewsItem) -> str:
    """返回用于关键词匹配的新闻文本."""
    return f"{news.title} {news.summary}".lower()


def _text_contains(text: str, patterns: tuple[str, ...]) -> bool:
    """使用词边界正则匹配一组关键词."""
    return any(re.search(pattern, text) for pattern in patterns)


def _is_price_tracker(news: NewsItem) -> bool:
    """判断是否为金价追踪站点（非新闻源）."""
    check = f"{news.source} {news.url} {news.title}".lower()
    return any(domain in check for domain in _PRICE_TRACKER_DOMAINS)


def _is_markdown_noise(news: NewsItem) -> bool:
    """判断是否为 Markdown 模板行/非实质内容条目."""
    title = (news.title or "").strip()
    title_lower = title.lower()
    summary = (news.summary or "").strip()

    # 明显模板头部 — 无论摘要多长都排除
    if "search results" in title_lower or "search result" in title_lower:
        return True
    if "page " in title_lower and "of " in title_lower:  # "Page 1 of 3"
        return True

    # 含实质正文的条目保留（anysearch 把正文塞在 summary/description）
    if len(summary) > 150:
        return False

    # 标题过短或无标题
    if not title or len(title) < 20:
        return True
    if title.startswith("http"):
        return True
    # Markdown 结构行
    if re.match(r"^#{1,6}\s", title):  # ## / ###
        return True
    if re.match(r"^[-*]\s", title):  # - item / * item
        return True
    # URL label 行: "- **URL**: https://..."
    return bool(re.match(r"^-?\s*\*?\*?URL\*?\*?:?\s*https?:", title))


def _is_geopolitical(news: NewsItem) -> bool:
    """判断新闻是否涉及地缘风险 — 排除纯价格追踪页面."""
    if _is_price_tracker(news):
        return False
    text = _news_text(news)
    return _text_contains(text, _GEOPOLITICAL_TRIGGERS["primary"])


def _geopolitical_boost(news: NewsItem) -> float:
    """根据地缘相关性和油价/避险联动性计算加分.

    Returns:
        0.0 ~ 0.4 的额外得分加成。
        纯地缘新闻 +0.2；同时涉及油价/霍尔木兹 +0.1；同时涉及黄金/避险 +0.1。
    """
    if not _is_geopolitical(news):
        return 0.0

    text = _news_text(news)
    boost = 0.2

    # 与油价/供应中断直接相关 → 通胀预期/加息概率传导更强
    if _text_contains(text, _GEOPOLITICAL_TRIGGERS["oil_link"]):
        boost += 0.1

    # 与黄金/避险资产直接相关
    if _text_contains(text, _GEOPOLITICAL_TRIGGERS["safe_haven_link"]):
        boost += 0.1

    return min(boost, 0.4)


def _aggregate_tier_tag(pool: list[NewsItem]) -> str:
    """聚合信号的可信度标签.

    不拿单条新闻的 tier 代表整体，而是按池子里来源层级分布给出 mixed 标签。
    """
    if not pool:
        return "[unverified]"
    tiers = [it.metadata.get("source_tier", "unknown") for it in pool]
    counts = Counter(tiers)
    dominant, count = counts.most_common(1)[0]
    ratio = count / len(tiers)
    if dominant == "unknown" or ratio < 0.4:
        return "[mixed]"
    if ratio >= 0.6:
        return f"[mixed: {dominant}]"
    return "[mixed]"


# ── 标题本地化格式化 ──

# 地缘核心主题 → 🇨🇳中文标签映射
_GEO_TOPIC_LABELS: dict[str, str] = {
    "iran": "🇮🇷伊朗",
    "tehran": "🇮🇷伊朗",
    "israel": "🇮🇱以色列",
    "houthi": "胡塞武装",
    "yemen": "🇾🇪也门",
    "gaza": "加沙",
    "hamas": "哈马斯",
    "hezbollah": "真主党",
    "saudi": "🇸🇦沙特",
    "ukraine": "🇺🇦乌克兰",
    "russia": "🇷🇺俄罗斯",
    "taiwan": "🇹🇼台湾",
    "china": "🇨🇳中国",
    "north korea": "🇰🇵朝鲜",
    "hormuz": "霍尔木兹海峡",
    "strait of hormuz": "霍尔木兹海峡",
    "middle east": "中东",
    "ceasefire": "停火",
    "truce": "停火",
    "peace talk": "和谈",
    "negotiation": "谈判",
    "diplomatic": "外交斡旋",
    "attack": "攻击",
    "strike": "空袭",
    "airstrike": "空袭",
    "military": "军事行动",
    "war": "战争",
    "conflict": "冲突",
    "escalation": "升级",
    "oil": "石油",
    "blockade": "封锁",
    "sanction": "制裁",
    "tariff": "关税",
    "sanctions": "制裁",
    "tariffs": "关税",
    "trade war": "贸易战",
    "trade deal": "贸易协定",
    "trade talks": "贸易谈判",
    "canada": "🇨🇦加拿大",
    "mexico": "🇲🇽墨西哥",
    "nuclear": "核",
    "missile": "导弹",
    "drone": "无人机",
    # 调停方
    "pakistan": "🇵🇰巴基斯坦",
    "qatar": "🇶🇦卡塔尔",
    "oman": "🇴🇲阿曼",
    "egypt": "🇪🇬埃及",
    "turkey": "🇹🇷土耳其",
}

# 参与方立场模板 — 按关键词匹配，产出"<参与方>: <动作>"片段
# 格式: (关键词匹配模式, 动作描述)
_STANCE_PATTERNS: list[tuple[str, str]] = [
    # ── 停火/谈判相关 ──
    ("iran.{0,60}(?:confirm|receiv|review|study).{0,40}proposal", "🇮🇷伊确认收到方案+正审查"),
    ("iran.{0,60}(?:accept|agree|approve|支持).{0,30}(?:ceasefire|truce|proposal)", "🇮🇷伊接受停火方案"),
    ("iran.{0,60}(?:reject|refuse|dismiss).{0,30}(?:ceasefire|truce|proposal)", "🇮🇷伊拒绝停火方案"),
    ("iran.{0,60}(?:push|initiat|propos).{0,40}(?:ceasefire|truce|10.day)", "🇮🇷伊主动推动停火"),
    ("iran.{0,60}(?:commit).{0,20}diplomacy", "🇮🇷伊重申外交承诺"),
    ("(?:trump|us|united states|white house|washington).{0,60}(?:reject|refuse|not willing|no longer).{0,40}(?:negotiat|talk|ceasefire|truce)", "🇺🇸美拒绝谈判+威胁升级"),
    ("(?:trump|us|united states).{0,40}(?:pay|price|punish|end.{0,5}civilization)", "🇺🇸美要求伊付出代价"),
    ("(?:trump|us|united states|white house).{0,60}(?:accept|agree|approve|extend).{0,30}(?:ceasefire|truce)", "🇺🇸美接受/延长停火"),
    ("(?:trump|us|united states).{0,40}(?:threat|pledge|vow|intensify).{0,30}(?:attack|strike|retaliat|escalat)", "🇺🇸美威胁升级攻击"),
    ("(?:trump|us|united states).{0,40}(?:desperate|want).{0,20}(?:talk|negotiat)", "🇺🇸美称伊求谈判"),
    ("(?:pakistan|qatar|oman|egypt|turkey).{0,80}(?:propos|offer|mediat|broker).{0,40}(?:ceasefire|truce|de-escalat)", "巴卡调停方提出停火方案"),
    ("(?:pakistan|qatar|mediator).{0,40}(?:two.week|10.day|temporary).{0,20}(?:ceasefire|truce)", "巴卡方案含2周停火提议"),
    ("(?:pakistan|qatar|mediator).{0,60}(?:return|back).{0,20}(?:pre.july|july 9)", "方案含退回7/9前状态"),
    ("reopen.{0,20}(?:strait of hormuz|hormuz)", "方案含重开霍尔木兹海峡"),
    ("end.{0,10}(?:blockade|sanction).{0,20}(?:iranian|iran)", "方案含解除封锁/制裁"),
    # ── 军事行动相关 ──
    (r"(?:us|united states|american).{0,40}(?:launch|carry out|conduct).{0,20}(?:\d+th|strike|attack)", "🇺🇸美军连续空袭伊朗"),
    ("(?:us|united states).{0,40}(?:strike|attack|hit).{0,20}iran", "🇺🇸美军打击伊朗"),
    ("iran.{0,60}(?:launch|fire).{0,40}(?:missile|drone|attack|strike)", "🇮🇷伊发射导弹/无人机反击"),
    ("iran.{0,40}(?:target|hit|destroy).{0,40}(?:kuwait|bahrain|jordan|uae|saudi)", "🇮🇷伊攻击海湾国家"),
    ("irgc.{0,40}(?:vow|threat|warn).{0,40}(?:respond|retaliat|punish)", "🇮🇷革命卫队誓言反击"),
    ("(?:houthi|houthis).{0,60}(?:blockade|threat|warn|attack)", "胡塞武装威胁/封锁"),
    ("(?:houthi|houthis).{0,60}(?:red sea|bab.el-mandeb|saudi)", "胡塞武装红海/曼德海峡"),
    ("(?:israel).{0,40}(?:strike|attack|hit|operation).{0,30}(?:lebanon|gaza|iran|houthi)", "🇮🇱以色列军事行动"),
    ("(?:service member|soldier|troop).{0,40}(?:kill|die|death)", "美军人员伤亡"),
    # ── 经济/制裁相关 ──
    ("(?:usa?|united states|trump).{0,40}(?:blockade|sanction).{0,20}(?:iranian|iran)", "🇺🇸美封锁/制裁伊朗"),
    ("(?:strait of hormuz|hormuz).{0,40}(?:close|block|shut|halt|stop|remain closed)", "霍尔木兹海峡仍关闭"),
    (r"oil.{0,20}(?:price|surge|spike|rise).{0,20}(?:\$|dollar)", "油价上涨"),
    (r"(?:brent|wti|crude).{0,20}(?:\$|dollar).{0,10}(?:\d{2,3})", "油价持续高位"),
    # ── 谈判状态 ──
    ("(?:talk|negotiation|diplomacy).{0,40}(?:stall|deadlock|collapse|break down)", "谈判陷入僵局"),
    ("(?:talk|negotiation).{0,40}(?:resum|restart|continue)", "谈判将恢复"),
    ("(?:talk|negotiation).{0,40}(?:islamabad|doha|geneva)", "谈判地点确认"),
]

# 通用动作兜底 (在 _format_geo_headline 主逻辑匹配不到时使用)
_GENERIC_ACTIONS: list[tuple[list[str], str]] = [
    (["ceasefire", "truce", "peace deal", "peace agreement"], "停火动态"),
    (["attack", "strike", "airstrike", "military operation"], "军事行动"),
    (["blockade", "封锁"], "封锁动态"),
    (["sanction", "制裁"], "制裁动态"),
    (["escalat", "升级"], "局势升级"),
    (["negotiat", "talk", "diplomatic", "外交"], "外交斡旋"),
    (["threat", "警告", "威胁"], "威胁/警告"),
]


def _extract_geo_topics(text: str, max_topics: int = 3) -> list[str]:
    """从新闻文本中提取地缘主题关键词（中文化）."""
    text_lower = text.lower()
    found: list[tuple[int, str]] = []  # (priority, label)
    for keyword, label in _GEO_TOPIC_LABELS.items():
        if keyword in text_lower:
            # 更长的关键词优先（更具体）
            priority = len(keyword)
            found.append((priority, label))
    # 按优先级降序（最具体的在前），去重后取前 N
    found.sort(key=lambda x: -x[0])
    seen: set[str] = set()
    result: list[str] = []
    for _, label in found:
        if label not in seen:
            seen.add(label)
            result.append(label)
            if len(result) >= max_topics:
                break
    return result


def _format_geo_headline(news: NewsItem) -> str:
    """多参与方立场提炼 — 输出结构化中文摘要.

    从新闻标题+摘要中匹配各方立场，产出如:
      "🇮🇷伊确认收到方案+正审查 | 🇺🇸美拒绝谈判+威胁升级 | 巴卡提出2周停火 [Anadolu]"
      "🇺🇸美军连续10夜空袭 | 🇮🇷革命卫队导弹反击科威特基地 [Al Jazeera]"
    无精确匹配时回退到通用动作+主题模式:
      "🇮🇷伊朗🇮🇱以色列停火动态 [Reuters]"
    """
    text = _news_text(news)

    # ── Step 1: 尝试精准立场匹配 ──
    stances: list[str] = []
    seen_actors: set[str] = set()

    def _stance_actor(label: str) -> str:
        """提取立场标签中的参与方标识 — 按国旗 emoji 去重."""
        # 匹配开头的国旗 emoji (U+1F1E6-U+1F1FF 两个为一组) 或中文标签
        m = re.match(
            r"^([\U0001F1E0-\U0001F1FF]{1,2}"  # 🇮🇷 / 🇺🇸 (1或2个国旗码点)
            r"|[一-鿿]{2,4})"           # 或 "胡塞武装"/"巴卡"
            r"[^一-鿿]{0,2}",           # 后跟 0-2 个非中文字符 (如 "伊"/"美")
            label
        )
        if m:
            return m.group(1)
        # 无国旗无中文标记 → 取前 6 字符
        return label[:8]

    for pattern, stance_label in _STANCE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            actor = _stance_actor(stance_label)
            if actor not in seen_actors:
                seen_actors.add(actor)
                stances.append(stance_label)

    if stances:
        headline = " | ".join(stances[:4])
    else:
        # ── Step 2: 回退 — 通用主题+动作 ──
        topics = _extract_geo_topics(text, max_topics=2)
        text_lower = text.lower()
        action = "相关动态"
        for keywords, label in _GENERIC_ACTIONS:
            if any(w in text_lower for w in keywords):
                action = label
                break
        headline = f"{'·'.join(topics)}→{action}" if topics else news.title.strip()[:50]

    # ── Step 3: 附来源 ──
    source_short = (news.source or "")[:12]
    if source_short:
        headline += f" [{source_short}]"

    return headline


def _format_breaking_desc(news: NewsItem) -> str:
    """格式化重大事件描述 — 结构化、中文友好."""
    parts = []
    title = news.title.strip()[:100]
    parts.append(f"📰 {title}")
    if news.source:
        parts.append(f"📍来源: {news.source}")
    return "\n       ".join(parts)


def _format_news_item_line(it: NewsItem) -> str | None:
    """格式化单条新闻为一行摘要 — 中文友好。返回 None 表示该项应跳过。"""
    title = _clean_title(it)
    # 跳过清洗后仍然无意义的条目
    if not title or len(title) < 10:
        return None
    if title.startswith("http"):
        return None
    if title.lower().startswith("search result"):
        return None
    s = it.sentiment
    e = "📈" if s > 0.1 else "📉" if s < -0.1 else "➖"
    source = (it.source or "")[:15]
    return f"{e} {title} | {source}"


def _clean_title(it: NewsItem) -> str:
    """清洗 anysearch Markdown 标题，提取可读摘要.

    '### 1. Gold Price Today — Live Gold...' → 'Gold Price Today — Live Gold...'
    '- Gold Spot Prices | Silver Prices...' → 'Gold Spot Prices...'
    'https://www.apmex.com/gold-price' → 'APMEX Gold Price'
    """
    title = (it.title or "").strip()
    # URL → 提取域名关键词
    if title.startswith("http"):
        try:
            from urllib.parse import urlparse
            domain = urlparse(title).netloc.replace("www.", "")
            title = domain.split(".")[0].title()
        except Exception:
            title = ""
    # 掉 Markdown 标题前缀: "### 1. " → ""
    title = re.sub(r"^#{1,6}\s*\d*\.?\s*", "", title)
    # 掉列表符号: "- " → ""
    title = re.sub(r"^[-*]\s+", "", title)
    # 掉粗体标记: **text** → text
    title = re.sub(r"\*\*", "", title)
    # 掉 URL: 前缀
    title = re.sub(r"^-?\s*URL:?\s*", "", title)
    # 限制长度
    if len(title) > 70:
        title = title[:67] + "..."
    return title.strip()


class NewsSignalGenerator:
    """消息面信号生成器 — 集成事实核查."""

    def __init__(self) -> None:
        self.fetcher = NewsFetcher()
        self.fact_checker = FactChecker(min_cross_sources=2)

    def analyze(self, items: list[NewsItem]) -> list[Signal]:
        """分析新闻列表，生成信号 — 集成事实核查."""
        signals: list[Signal] = []

        if not items:
            return signals

        # === 预过滤1：排除金价追踪页面 ===
        real_news = [it for it in items if not _is_price_tracker(it)]
        price_tracker_count = len(items) - len(real_news)
        if price_tracker_count > 0:
            logger.debug(f"过滤 {price_tracker_count} 条金价追踪页面")

        # === 预过滤2：排除 Markdown 模板行 & URL-only 条目 ===
        items = [it for it in real_news if not _is_markdown_noise(it)]

        # 全部被过滤 → 降级使用原始条目（标题清洗后展示，标注低质量）
        if not items:
            logger.debug(f"{len(real_news)}条均为噪音条目，使用清洗后的原始数据")
            items = real_news
            items_low_quality = True  # noqa: F841 — 下游逻辑待接入
        else:
            items_low_quality = False  # noqa: F841

        # === 事实核查 ===
        check_results = self.fact_checker.check_batch(items)
        items = apply_fact_checks(items, check_results)

        # 过滤掉可信度极低的新闻
        items = [it for it in items
                 if it.metadata.get("verification_status") != "false"]

        # 统计核查结果
        confirmed = [it for it in items
                     if it.metadata.get("verification_status") == "confirmed"]
        unverified = [it for it in items
                      if it.metadata.get("verification_status") == "unverified"]
        disputed = [it for it in items
                    if it.metadata.get("verification_status") == "disputed"]

        # 计算平均情感得分（优先使用已确认新闻）
        sentiment_pool = confirmed if len(confirmed) >= 3 else items
        if not sentiment_pool:
            return signals  # 过滤后无可用新闻
        avg_sentiment = sum(n.sentiment for n in sentiment_pool) / len(sentiment_pool)
        bull_count = sum(1 for n in sentiment_pool if n.sentiment > 0)

        # 检测重大事件 (仅保留与黄金相关的, 优先已确认)
        gold_impact_words = [
            "gold", "silver", "metal", "precious", "rate", "fed", "inflation",
            "payroll", "nfp", "nonfarm", "job", "cpi", "dollar", "treasury",
            "iran", "middle east", "israel", "war", "oil", "geopolitical",
            "hormuz", "strait of hormuz", "attack", "strike", "ceasefire",
            "central bank", "stimulus", "recession", "safe haven",
            # 2026-08-25 补: 贸易战/制裁主题 (美加谈判破裂50%关税类事件此前会被漏检)
            "tariff", "sanction", "trade war", "trade deal", "trade talks",
            "canada", "usmca", "negotiation",
        ]

        # 先尝试已确认的重大新闻
        breaking_confirmed = [
            n for n in confirmed
            if n.is_breaking and any(
                w in (n.title + " " + n.summary).lower()
                for w in gold_impact_words
            )
        ]
        # 补充未确认但可能是重大的
        breaking_all = breaking_confirmed + [
            n for n in items
            if n.is_breaking and n not in breaking_confirmed and any(
                w in (n.title + " " + n.summary).lower()
                for w in gold_impact_words
            )
        ]
        breaking = breaking_all[:5]

        if breaking:
            for news in breaking:
                v_status = news.metadata.get("verification_status", "unverified")
                v_conf = news.metadata.get("verification_confidence", 0.0)
                v_tier = news.metadata.get("source_tier", "unknown")

                # 已确认新闻 → 信号更强; disputed → 大幅降权
                if v_status == "confirmed":
                    score_multiplier = 1.2
                elif v_status == "disputed":
                    score_multiplier = 0.4
                elif _is_geopolitical(news) and news.is_breaking:
                    # 突发性地缘新闻往往只有 1 家媒体先报，传统事实核查会系统性降权。
                    # 这里允许未确认但具备地缘相关性的突发新闻使用接近确认的乘数。
                    score_multiplier = 1.0
                else:
                    score_multiplier = 0.8

                # 地缘风险加分：把中性报道的潜在方向性体现出来
                geo_boost = _geopolitical_boost(news)
                base_score = news.sentiment if abs(news.sentiment) > 0.05 else 0.0
                adjusted_score = max(-1.0, min(1.0, base_score * score_multiplier + geo_boost))

                # 方向由最终 adjusted_score 决定，避免情感与加分方向矛盾
                if adjusted_score > 0.05:
                    direction = SignalDirection.BULLISH
                    strength = SignalStrength.STRONG if adjusted_score > 0.5 else SignalStrength.MODERATE
                elif adjusted_score < -0.05:
                    direction = SignalDirection.BEARISH
                    strength = SignalStrength.STRONG if adjusted_score < -0.5 else SignalStrength.MODERATE
                else:
                    direction = SignalDirection.NEUTRAL
                    strength = SignalStrength.WEAK

                tag = format_verification_tag(news)

                # 格式化描述 — 结构化中文友好
                title_short = news.title.strip()[:100]
                desc_parts = [f"📰 {title_short}"]
                if news.source:
                    desc_parts.append(f"📍{news.source}")
                description = " | ".join(desc_parts)

                signals.append(Signal(
                    name=f"重大事件{tag}: {_format_geo_headline(news) if _is_geopolitical(news) else title_short[:60]}",
                    dimension="news",
                    direction=direction,
                    strength=strength,
                    score=round(adjusted_score, 2),
                    description=description[:250],
                    metadata={
                        "source": news.source,
                        "url": news.url,
                        "verification_status": v_status,
                        "verification_confidence": v_conf,
                        "source_tier": v_tier,
                        "geopolitical": _is_geopolitical(news),
                        "geopolitical_boost": round(geo_boost, 2),
                        "full_title": news.title,
                        "full_summary": news.summary,
                    },
                ))

        # 事实核查降级警告（如果大量新闻无法验证）
        if len(items) >= 5 and len(unverified) / len(items) > 0.6:
            # 统计主导层级
            tier_counts: dict[str, int] = {}
            for it in items:
                t = it.metadata.get("source_tier", "unknown")
                tier_counts[t] = tier_counts.get(t, 0) + 1
            dominant_tier = max(tier_counts.items(), key=lambda kv: kv[1])[0] if tier_counts else "unknown"

            signals.append(Signal(
                name="新闻可信度低警告",
                dimension="news",
                direction=SignalDirection.NEUTRAL,
                strength=SignalStrength.WEAK,
                score=0.0,
                description=f"{len(unverified)}/{len(items)}条新闻无法交叉验证，主导层级 {dominant_tier}，新闻面信号置信度下降",
                metadata={
                    "source": "fact_checker",
                    "unverified_ratio": len(unverified) / len(items),
                    "dominant_tier": dominant_tier,
                    "tier_breakdown": tier_counts,
                },
            ))

        # 汇总具体资讯列表 — 中文友好格式化
        if items:
            top_items = items[:8]
            news_lines = [
                line for it in top_items
                if (line := _format_news_item_line(it)) is not None
            ]
            if news_lines:
                signals.append(Signal(
                    name="最近新闻资讯",
                    dimension="news",
                    direction=SignalDirection.NEUTRAL,
                    strength=SignalStrength.WEAK,
                    score=0.0,
                    description="\n       ".join(news_lines[:5]),
                metadata={
                    "news_list": [
                        {
                            "title": it.title,
                            "source": it.source,
                            "url": it.url,
                            "sentiment": it.sentiment,
                            "verification_status": it.metadata.get("verification_status", "unverified"),
                            "published_at": (
                                it.published_at.isoformat()
                                if it.published_at is not None and hasattr(it.published_at, "isoformat")
                                else ""
                            ),
                        }
                        for it in top_items
                    ],
                },
            ))

        # 整体情感倾向（至少3条新闻，阈值 0.10）
        if len(sentiment_pool) >= 3 and abs(avg_sentiment) > 0.10:
            direction = (
                SignalDirection.BULLISH if avg_sentiment > 0
                else SignalDirection.BEARISH
            )
            strength = SignalStrength.MODERATE if abs(avg_sentiment) > 0.4 else SignalStrength.WEAK
            # verified_ratio 加权: 已确认比例越高，信号越强
            verified_ratio = len(confirmed) / len(items) if items else 0
            score_multiplier = 0.5 + 0.5 * verified_ratio
            adjusted_score = avg_sentiment * score_multiplier

            signals.append(Signal(
                name=f"新闻情感倾向{_aggregate_tier_tag(sentiment_pool)}",
                dimension="news",
                direction=direction,
                strength=strength,
                score=round(adjusted_score, 2),
                description=f"最近24h {len(items)}条新闻({len(confirmed)}确认) 平均情感 {avg_sentiment:+.2f}",
                metadata={
                    "verified_ratio": round(verified_ratio, 2),
                    "confirmed_count": len(confirmed),
                    "unverified_count": len(unverified),
                    "disputed_count": len(disputed),
                    "aggregate_tier": _aggregate_tier_tag(sentiment_pool),
                },
            ))

        # 新闻活跃度（≥5条相关新闻说明市场关注度高）
        if len(sentiment_pool) >= 5:
            bull_ratio = bull_count / len(sentiment_pool) if sentiment_pool else 0.5
            score = (bull_ratio - 0.5) * 0.4  # 最多 ±0.2
            # 如果 confirmed 为 0 且 mostly unverified，降低幅度 50%
            if len(confirmed) == 0 and len(unverified) / len(items) > 0.6:
                score *= 0.5

            signals.append(Signal(
                name=f"新闻活跃度{_aggregate_tier_tag(sentiment_pool)}",
                dimension="news",
                direction=SignalDirection.BULLISH if bull_ratio > 0.5 else SignalDirection.BEARISH,
                strength=SignalStrength.WEAK,
                score=round(score, 2),
                description=f"24h内{len(items)}条相关新闻({len(confirmed)}确认), 看涨占比{bull_ratio:.0%}",
                metadata={
                    "confirmed_count": len(confirmed),
                    "unverified_count": len(unverified),
                    "aggregate_tier": _aggregate_tier_tag(sentiment_pool),
                },
            ))

        # === 地缘风险溢价信号 ===
        # 传统 NLP 情感分析对中性报道的地缘新闻会给出接近 0 的分数，导致重大事件被忽略。
        # 这里把池子中所有地缘新闻聚合起来，单独生成一个风险溢价信号。
        geo_items = [it for it in items if _is_geopolitical(it)]
        if geo_items:
            total_geo_boost = sum(_geopolitical_boost(it) for it in geo_items)
            # 单条最高 0.4；多条按 sqrt 衰减聚合，避免重复计数
            aggregate_boost = min(0.6, total_geo_boost / math.sqrt(len(geo_items)))

            # 判断方向：默认地缘风险利好黄金；若明确谈判取得突破/停火延期则偏空
            text_all = " ".join(_news_text(it) for it in geo_items)
            de_escalation_patterns = (
                r"\bpeace deal\b", r"\breach a deal\b", r"\bdeal reached\b",
                r"\bpeace agreement\b", r"\bceasefire extended\b",
                r"\bceasefire deal\b", r"\btalks make progress\b",
                r"\bdiplomatic breakthrough\b",
            )
            escalation_patterns = (
                r"\battack\b", r"\bstrike\b", r"\bwar\b", r"\binvasion\b",
                r"\btension\b", r"\bthreat\b", r"\bhormuz\b",
                r"\bmilitary action\b", r"\bconflict escalat",
            )
            de_count = sum(1 for p in de_escalation_patterns if re.search(p, text_all))
            es_count = sum(1 for p in escalation_patterns if re.search(p, text_all))

            # 只有当缓和信号明显多于升级信号时才判定为 bearish，避免单一词汇误导
            if de_count >= 2 and de_count > es_count:
                geo_direction = SignalDirection.BEARISH
                geo_description = "地缘谈判取得进展/停火延续，避险溢价下降"
            else:
                geo_direction = SignalDirection.BULLISH
                geo_description = "地缘风险升温，推升黄金避险溢价"

            # 聚合具体标题列表 — 使用中文本地化摘要
            geo_headlines = [_format_geo_headline(it) for it in geo_items]
            geo_titles_str = "\n       ".join(
                f"{i+1}. {h}" for i, h in enumerate(geo_headlines[:8])
            )

            signals.append(Signal(
                name="地缘风险溢价",
                dimension="news",
                direction=geo_direction,
                strength=SignalStrength.MODERATE if aggregate_boost > 0.3 else SignalStrength.WEAK,
                score=round(aggregate_boost if geo_direction == SignalDirection.BULLISH else -aggregate_boost, 2),
                description=(
                    f"{len(geo_items)}条地缘新闻聚合：{geo_description}\n"
                    f"       {geo_titles_str}"
                ),
                metadata={
                    "geo_news_count": len(geo_items),
                    "aggregate_boost": round(aggregate_boost, 2),
                    "escalation_keywords": es_count,
                    "de_escalation_keywords": de_count,
                    "geo_news_titles": [it.title for it in geo_items],
                },
            ))

        return signals

    def fetch_and_analyze(self, hours: int = 24) -> list[Signal]:
        """抓取并分析新闻 — 含事实核查."""
        items = self.fetcher.fetch_latest(hours=hours)
        items = self.fetcher.analyze_sentiment(items)
        return self.analyze(items)
