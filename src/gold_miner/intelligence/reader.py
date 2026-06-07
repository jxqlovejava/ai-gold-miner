"""文章读取器 — URL 抓取 + 文本提取."""

import re

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from gold_miner.proxy import get_proxied_client

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class ArticleReader:
    """文章读取器 — 支持 URL 抓取和纯文本."""

    @staticmethod
    def from_url(url: str, timeout: int = 20) -> str | None:
        """从 URL 抓取文章，提取正文文本."""
        try:
            with get_proxied_client(timeout=timeout) as client:
                resp = client.get(url, headers=HEADERS, follow_redirects=True)
                resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning(f"文章抓取失败 ({url}): {e}")
            return None

        # 编码检测
        content = resp.content
        for encoding in [resp.encoding, "utf-8", "gbk", "gb2312"]:
            if encoding is None:
                continue
            try:
                html = content.decode(encoding)
                if any("一" <= c <= "鿿" for c in html[:2000]):
                    break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            html = content.decode("utf-8", errors="replace")

        return ArticleReader._extract_text(html, url)

    @staticmethod
    def from_text(text: str) -> str:
        """直接接受文本输入."""
        return text.strip()

    @staticmethod
    def _extract_text(html: str, url: str = "") -> str:
        """从 HTML 中提取正文文本."""
        soup = BeautifulSoup(html, "html.parser")

        # 移除无关标签
        for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        # 尝试提取 <article> 或主内容区
        main = soup.find("article") or soup.find("main") or soup.find("body")
        if not main:
            return ""

        text = main.get_text(separator="\n")
        # 清理多余空行
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        # 过滤过短的非内容行
        lines = [l for l in lines if len(l) > 10 or any("一" <= c <= "鿿" for c in l)]

        result = "\n".join(lines)
        if len(result) < 100:
            logger.warning(f"文章正文提取过短 ({len(result)} 字符), 可能抓取失败")

        return result
