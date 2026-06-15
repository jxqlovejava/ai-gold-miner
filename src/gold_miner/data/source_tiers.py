"""信息来源可信度分级 — T0/T1/T2/T3/unknown.

基于 CLAUDE.md 信息验证协议中的来源可信度层级。
"""

from __future__ import annotations

# T0: 一手官方源
T0_DOMAINS: set[str] = {
    # 美国监管机构
    "federalreserve.gov", "treasury.gov", "bls.gov", "sec.gov",
    # 国际组织
    "imf.org", "worldbank.org", "bis.org", "gold.org",
    # 央行
    "pbc.gov.cn", "ecb.europa.eu", "boj.or.jp", "bankofengland.co.uk",
    "centralbank.ie", "nb.gov.pl", "tcmb.gov.tr",
    # 交易所 / 官方市场数据
    "nasdaq.com", "nyse.com", "londonstockexchange.com",
    "sse.com.cn", "szse.cn", "sge.com.cn", "cmegroup.com",
    # 世界黄金协会
    "worldgoldcouncil.org", "wgc.org",
}

T0_SOURCE_NAMES: set[str] = {
    "federal reserve", "fed", "fomc", "bureau of labor statistics", "bls",
    "treasury", "sec", "imf", "world bank", "bis",
    "people's bank of china", "pboc", "ecb", "boe", "boj",
    "shanghai gold exchange", "sge", "上金所",
    "world gold council", "wgc", "世界黄金协会",
    "nasdaq", "nyse", "cme group", "cmegroup",
}

# T1: 官方授权数据终端 / 主要市场数据
T1_DOMAINS: set[str] = {
    "bloomberg.com", "reuters.com", "wsj.com", "ft.com",
    "marketwatch.com", "investing.com", "cnbc.com",
    "wind.com.cn", "eastmoney.com", "10jqka.com.cn",
    "xueqiu.com", "fx168.com", "kitco.com",
    "tradingview.com", "forexfactory.com",
}

T1_SOURCE_NAMES: set[str] = {
    "bloomberg", "reuters", "wsj", "wall street journal",
    "financial times", "ft", "cnbc", "marketwatch",
    "wind", "东方财富", "同花顺", "雪球",
    "kitco", "fx168", "tradingview",
}

# T2: 权威媒体原创
T2_DOMAINS: set[str] = {
    "caixin.com", "anadoluagency.com", "aa.com.tr",
    "apnews.com", "theguardian.com", "nytimes.com",
    "bbc.com", "cnn.com", "aljazeera.com",
    "scmp.com", "nikkei.com", "asahi.com",
    "people.com.cn", "xinhuanet.com", "chinadaily.com.cn",
    "caijing.com.cn", "21jingji.com", "yicai.com",
    "thepaper.cn", "jfdaily.com",
    "spglobal.com", "moodys.com", "fitchratings.com",
}

T2_SOURCE_NAMES: set[str] = {
    "caixin", "财新", "anadolu agency", "ap news", "associated press",
    "the guardian", "new york times", "nytimes", "bbc", "cnn",
    "al jazeera", "scmp", "south china morning post",
    "nikkei", "asahi", "people's daily", "xinhua", "china daily",
    "caijing", "21jingji", "yicai", "thepaper", "jfdaily",
    "s&p global", "moody's", "fitch ratings",
}

# T3: 聚合 / 自媒体 / 论坛 / 搜索引擎
T3_DOMAINS: set[str] = {
    "anysearch", "duckduckgo.com", "bing.com", "google.com",
    "sina.com.cn", "weixin.qq.com", "zhihu.com",
    "cngold.org", "163.com", "jrj.com.cn",
    "sohu.com", "ifeng.com", "qq.com",
    "toutiao.com", "weibo.com", "douyin.com",
    "reddit.com", "twitter.com", "x.com",
    "youtube.com", "bilibili.com",
}

T3_SOURCE_NAMES: set[str] = {
    "anysearch", "duckduckgo", "bing", "google",
    "sina", "weixin", "zhihu", "cngold", "netease", "jrj",
    "sohu", "ifeng", "qq", "toutiao", "weibo", "douyin",
    "reddit", "twitter", "x", "youtube", "bilibili",
}


def _normalize_domain(url: str) -> str:
    """从 URL 提取规范化域名（去掉协议、路径、www）。"""
    domain = url.lower()
    if "://" in domain:
        domain = domain.split("://", 1)[1]
    domain = domain.split("/")[0]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def _domain_matches(domain: str, tier_domain: str) -> bool:
    """精确匹配域名或其后缀子域.

    例如: domain="news.bloomberg.com" 匹配 tier_domain="bloomberg.com"；
          domain="fakebloomberg.com" 不匹配.
    """
    return domain == tier_domain or domain.endswith("." + tier_domain)


def _source_name_matches(source: str, name: str) -> bool:
    """来源名称整词匹配，避免子串误匹配.

    例如: name="fed" 不匹配 "federal"；name="bloomberg" 匹配 "Bloomberg News"。
    """
    source_lower = source.lower().strip()
    name_lower = name.lower().strip()
    if source_lower == name_lower:
        return True
    # 作为独立词出现：至少一侧有分隔符，不允许纯子串匹配
    separators = (" ", "|", "-", "[", "]", "/", "(", ")", ":", ",", ".")
    for prefix in ("",) + separators:
        for suffix in ("",) + separators:
            if prefix == "" and suffix == "":
                continue
            if prefix + name_lower + suffix in source_lower:
                return True
    return False


def get_source_tier(source: str, url: str = "") -> str:
    """根据来源域名或名称返回可信度层级.

    Returns: "T0" | "T1" | "T2" | "T3" | "unknown"
    """
    domain = _normalize_domain(url)

    # 1. 域名匹配
    if domain:
        for d in T0_DOMAINS:
            if _domain_matches(domain, d):
                return "T0"
        for d in T1_DOMAINS:
            if _domain_matches(domain, d):
                return "T1"
        for d in T2_DOMAINS:
            if _domain_matches(domain, d):
                return "T2"
        for d in T3_DOMAINS:
            if _domain_matches(domain, d):
                return "T3"

    # 2. 来源名称匹配
    for name in T0_SOURCE_NAMES:
        if _source_name_matches(source, name):
            return "T0"
    for name in T1_SOURCE_NAMES:
        if _source_name_matches(source, name):
            return "T1"
    for name in T2_SOURCE_NAMES:
        if _source_name_matches(source, name):
            return "T2"
    for name in T3_SOURCE_NAMES:
        if _source_name_matches(source, name):
            return "T3"

    return "unknown"
