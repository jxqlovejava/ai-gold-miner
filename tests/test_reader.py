"""测试文章读取器."""

from gold_miner.intelligence.reader import ArticleReader


class TestArticleReader:
    def test_from_text(self):
        text = "这是一篇黄金分析文章。"
        result = ArticleReader.from_text(text)
        assert result == text

    def test_from_text_strips_whitespace(self):
        result = ArticleReader.from_text("  黄金将大涨  ")
        assert result == "黄金将大涨"

    def test_extract_text_from_html(self):
        html = """<!DOCTYPE html>
<html><head><title>Test</title></head>
<body>
<article>
<h1>黄金分析报告</h1>
<p>黄金价格有望在2026年下半年突破5000美元关口。</p>
<p>美联储降息预期升温，美元走弱支撑金价。</p>
</article>
</body></html>"""
        result = ArticleReader._extract_text(html)
        assert "黄金分析报告" in result
        assert "5000美元" in result
        assert "美联储降息" in result

    def test_extract_text_removes_scripts(self):
        html = """<!DOCTYPE html>
<html><body>
<script>console.log('ad')</script>
<article><p>重要分析内容在这里。</p></article>
</body></html>"""
        result = ArticleReader._extract_text(html)
        assert "console.log" not in result
        assert "重要分析内容在这里" in result

    def test_extract_text_fallback_to_body(self):
        html = """<!DOCTYPE html>
<html><body>
<p>没有article标签的内容。</p>
<p>但body里也有正文。</p>
</body></html>"""
        result = ArticleReader._extract_text(html)
        assert "没有article标签的内容" in result

    def test_extract_text_filters_short_lines(self):
        html = """<!DOCTYPE html>
<html><body>
<article>
<p>1</p>
<p>这是一段足够长的正文内容需要被保留下来。</p>
</article>
</body></html>"""
        result = ArticleReader._extract_text(html)
        assert "这是一段足够长的正文内容需要被保留下来" in result
