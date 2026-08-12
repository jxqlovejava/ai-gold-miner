"""render_report_html 渲染器测试 — markdown → 本地 HTML."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import render_report_html as r  # noqa: E402

SAMPLE_MD = """# 🥇 金价完整分析 · 2026-08-11 11:15
> 积存金 **¥958.81** | 国际 $4408/oz

## 📌 决策: **持有** | 综合评分 +0.30 | 置信度 75%

## 维度信号
维度 | 方向 | 均分 | 说明
技术面 | 🟢 | +0.15 | 逼近整数关口950

## Agent 博弈
🐮 **BullAgent** (信心 70%)
  ✓ 央行购金结构牛
  ✓ 非农转负降息预期

## 军规自查
通过 30/32
⚠️ r011 警惕一边倒
"""


def test_md_to_html_table():
    blocks = r._md_to_html("维度 | 方向 | 均分 | 说明\n技术面 | 🟢 | +0.15 | 描述")
    html_out = "".join(blocks)
    assert "<table>" in html_out
    assert "<thead>" in html_out


def test_md_to_html_headers_and_bold():
    blocks = r._md_to_html("## 决策\n**持有**")
    html_out = "".join(blocks)
    assert "<h2>决策</h2>" in html_out
    assert "<strong>持有</strong>" in html_out


def test_render_contains_css_vars(tmp_path):
    out = tmp_path / "r.html"
    r.render(SAMPLE_MD, out)
    content = out.read_text(encoding="utf-8")
    assert "--gold:" in content
    assert "class=\"section\"" in content


def test_render_hero_and_status(tmp_path):
    out = tmp_path / "r.html"
    r.render(SAMPLE_MD, out)
    content = out.read_text(encoding="utf-8")
    assert "class=\"hero\"" in content
    assert "class=\"status-bar\"" in content
    # hero 标题来自首行 #
    assert "金价完整分析" in content


def test_render_writes_file(tmp_path):
    out = tmp_path / "r.html"
    p = r.render(SAMPLE_MD, out)
    assert p.exists()
    assert p.stat().st_size > 500


def test_render_prints_file_url(tmp_path, capsys):
    out = tmp_path / "r.html"
    rc = r.main(["--input", "/tmp/_nope_missing_2026.md", "--out", str(out)])
    assert rc == 1  # 缺失输入 → 非0
    captured = capsys.readouterr()
    assert "输入文件不存在" in captured.err


def test_render_main_success(tmp_path, capsys):
    md = tmp_path / "in.md"
    md.write_text(SAMPLE_MD, encoding="utf-8")
    out = tmp_path / "out.html"
    rc = r.main(["--input", str(md), "--out", str(out)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "file://" in captured.out
    assert out.exists()


def test_inline_status_icons_colored():
    out = r._inline("⚠️ 有风险 **bold** ✅ 通过")
    assert '<span class="warn">⚠️</span>' in out
    assert '<span class="ok">✅</span>' in out
    assert "<strong>bold</strong>" in out


def test_list_bull_bear_colored():
    blocks = r._md_to_html("  ✓ 利多论据\n  ✗ 利空论据")
    html_out = "".join(blocks)
    assert 'class="ok"' in html_out
    assert 'class="warn"' in html_out


def test_ordered_list_nested_block_keeps_numbering():
    """回归: 有序列表项内嵌缩进块(如缠论结构)不得拆散 <ol> 导致序号重置.

    旧 bug: 「1. 技术面」后跟缩进缠论块 + 空行 → 「2. 基本面」被拆成新 <ol>,
    渲染成 1 / 1,2,3... 序号全错乱. 修复后 1..N 同处一个 <ol> 连续编号.
    """
    md = (
        "1. **技术面**（+0.1）：布林带上轨\n"
        "\n"
        "   🀄 缠论结构：\n"
        "   - 结构：4 笔 + 1 中枢\n"
        "   - 买卖点：三买 @860\n"
        "\n"
        "2. **基本面**（+0.2）：央行购金\n"
        "3. **消息面**（+0.15）：地缘溢价\n"
        "4. **资金流**（-0.1）：COT 减仓\n"
        "5. **情绪面**（+0.1）：\n"
        "6. **近期事件**（+0.2）：\n"
    )
    blocks = r._md_to_html(md)
    html_out = "".join(blocks)
    # 6 项同处一个 <ol>, 不产生第二个独立 <ol>
    assert html_out.count("<ol>") == 1
    assert html_out.count("<li>") == 6
    # 缠论嵌套内容折叠进技术面 <li>
    assert "li-nested" in html_out
    assert "li-nest-item" in html_out
    # 基本面等后续项不在独立 <ol> 开头
    assert "</ol>" not in html_out.split("<strong>基本面</strong>")[0].split("<li>")[-1]


def test_doctrine_table_rendered(tmp_path):
    md = "## 军规自查\n通过 30/32\n| 规则 | 判定 |\n|------|------|\n| r001 单笔≤20% | ✅ |\n| r011 警惕一边倒 | ⚠️ |"
    out = tmp_path / "doctrine.html"
    r.render(md, out)
    content = out.read_text(encoding="utf-8")
    assert "<table>" in content
    assert '<span class="ok">✅</span>' in content
    assert '<span class="warn">⚠️</span>' in content


def test_pipe_list_not_parsed_as_table():
    """回归: '- x | y | z' 列表行不得被误判为表格 (避免缺角)."""
    blocks = r._md_to_html("- 🔴 COT | strong | -0.95 | 机构减多\n- 🟢 ETF | strong | +0.67 | 流入")
    html_out = "".join(blocks)
    assert "<table>" not in html_out
    assert html_out.count("<li>") == 2
    assert "COT" in html_out


def test_table_after_list_still_parses(tmp_path):
    """列表后接真表格, 两者都正确."""
    md = "- 项A\n- 项B\n\n| 列1 | 列2 |\n|-----|-----|\n| a | b |"
    out = tmp_path / "mix.html"
    r.render(md, out)
    content = out.read_text(encoding="utf-8")
    assert "<ul>" in content
    assert "<table>" in content


def test_bold_title_before_list_kept_as_paragraph():
    """回归: '**粗体标题**' 后跟列表 → 标题保留为段落, 列表独立换行."""
    blocks = r._md_to_html("**多空性质对比**\n- 多头论据\n- 空头论据")
    html_out = "".join(blocks)
    assert "<p><strong>多空性质对比</strong></p>" in html_out
    assert html_out.count("<li>") == 2


def test_bold_table_cell_not_excluded():
    """回归: 表格行含 **粗体** 单元格不得被列表正则误排除."""
    blocks = r._md_to_html("| 驱动 | 机制 |\n|------|------|\n| **非农** | 宽松 |")
    html_out = "".join(blocks)
    assert "<table>" in html_out
    assert "<strong>非农</strong>" in html_out


def test_decision_h2_rendered_as_card():
    """回归: '## 📌 决策:' 标题 + 建议仓位/止损/止盈 字段 → 决策卡片 (不再挤成段落)."""
    blocks = r._md_to_html(
        "## 📌 决策: **持有** | 综合评分 +0.26 | 置信度 72%\n"
        "建议仓位: 6%（维持现状，不追涨）\n"
        "止损: ATR 浮盈轨 911.64（未触发）\n"
        "止盈: —（核心池仅 ATR 移动止盈）\n\n"
        "**核心结论**：持有"
    )
    html_out = "".join(blocks)
    assert "decision-card" in html_out
    assert '<span class="decision-value">持有</span>' in html_out
    # meta 拆分
    assert "综合评分 +0.26" in html_out
    assert "置信度 72%" in html_out
    # 三个字段独立成 df-item, 不被段落合并
    assert html_out.count("df-item") == 3
    assert "建议仓位" in html_out and "止损" in html_out and "止盈" in html_out
    # 决策值不再出现在 h2 (裸 | 消失)
    assert "<h2>📌 决策:" not in html_out


def test_ascii_table_rendered_as_html_table():
    """回归: SignalBundle.format_dimension_table() 的 ASCII 框线表格 → <table>."""
    ascii_tbl = (
        "┌──────────┬────────┬──────┐\n"
        "│ 维度     │ 方向   │ 均分 │\n"
        "├──────────┼────────┼──────┤\n"
        "│ event    │ 🟢 看多│ +0.58 │\n"
        "│ technical│ 🔴 看空│ -0.12 │\n"
        "└──────────┴────────┴──────┘"
    )
    blocks = r._md_to_html(ascii_tbl)
    html_out = "".join(blocks)
    assert "<table>" in html_out
    assert "<th>维度</th>" in html_out
    assert "<td>event</td>" in html_out
    assert "<span class=\"ok\">🟢</span>" in html_out  # 图标仍彩色
    # ASCII 框线字符不进入输出
    assert "┌" not in html_out and "│" not in html_out


def test_ascii_table_in_fence_rendered_as_table():
    """回归: ``` 围栏内 ASCII 表格 → 表格而非 <pre>."""
    md = "```\n┌──┬──┐\n│ a│ b │\n└──┴──┘\n```"
    html_out = "".join(r._md_to_html(md))
    assert "<table>" in html_out
    assert "<pre>" not in html_out


def test_code_fence_non_table_rendered_as_pre():
    """普通代码围栏 → <pre><code>, 不被段落空格折叠."""
    md = "```\nPYTHONPATH=src python3 scan\n```"
    html_out = "".join(r._md_to_html(md))
    assert "<pre><code>" in html_out
    assert "PYTHONPATH=src" in html_out


def test_quote_after_blank_line_still_consumed_as_status(tmp_path):
    """回归: 标题与行情引文之间有空行时, 引文仍应消费为 status-bar, 不残留 blockquote."""
    md = (
        "# 🥇 金价完整分析 · 2026-08-11\n\n"
        "> 积存金 **¥947.26** | 国际 $4362.81/oz | 净保本 ¥894.38\n\n"
        "## 后续关注\n内容"
    )
    out = tmp_path / "r.html"
    r.render(md, out)
    content = out.read_text(encoding="utf-8")
    assert "class=\"status-bar\"" in content
    assert "¥947.26" in content
    assert "净保本 ¥894.38" in content
    # 引文不再作为 blockquote 重复出现在正文
    assert "<blockquote>" not in content


def test_key_block_rendered_for_label_paragraph():
    """回归: '**核心结论**：...' 定性段 → 金边高亮块, 不再是无层次的长段落."""
    blocks = r._md_to_html(
        "**核心结论**：今日金价**冲高回落**，950 关口得而复失。**结论：持有观望，不追涨。**"
    )
    html_out = "".join(blocks)
    assert "key-block" in html_out
    assert '<div class="key-tag">📌 核心结论</div>' in html_out
    assert "<p>" not in html_out  # 不再作为普通段落
    assert "<strong>冲高回落</strong>" in html_out
    assert "<strong>结论：持有观望，不追涨。</strong>" in html_out


def test_plain_bold_paragraph_not_key_block():
    """无冒号的加粗段落 (如 '**多空性质对比**') 不应误判为高亮块."""
    blocks = r._md_to_html("**多空性质对比**\n- 多头论据\n- 空头论据")
    html_out = "".join(blocks)
    assert "key-block" not in html_out
    assert "<strong>多空性质对比</strong>" in html_out


def test_key_block_body_split_into_sentences():
    """回归: 核心结论正文按句末标点分行, 粗体段 (含句号) 不被误切断."""
    blocks = r._md_to_html(
        "**核心结论**：今日金价**冲高回落**，950 关口得而复失。"
        "**结论：持有观望，不追涨。**"
    )
    html_out = "".join(blocks)
    assert "key-block" in html_out
    body = re.search(
        r'<div class="key-body">(.*?)</div></div>', html_out, re.S
    ).group(1)
    lines = body.split("<br>")
    assert len(lines) == 2
    assert lines[0].endswith("。")  # 第一行以句号收尾
    assert "持有观望" not in lines[0]  # 粗体结论句已独立成行
    assert "<strong>结论：持有观望，不追涨。</strong>" in lines[1]


def test_key_block_bold_spanning_sentence_boundary_not_split():
    """回归: '警示追涨。**结论：…。**' — 粗体前的句号正常分行, 粗体段整体保留."""
    blocks = r._md_to_html(
        "**核心结论**：r021 警示追涨。**结论：持有观望。**"
    )
    html_out = "".join(blocks)
    body = re.search(
        r'<div class="key-body">(.*?)</div></div>', html_out, re.S
    ).group(1)
    lines = body.split("<br>")
    assert len(lines) == 2
    assert "<strong>结论：持有观望。</strong>" in lines[1]


def test_blockquote_multiline_kept_separate():
    """回归: 多条 '>' 引用独立成行 + 句末标点分行, 不再合并成一堵长文本."""
    blocks = r._md_to_html(
        "> **推导逻辑**：看多情景 CPI 温和 → 站稳 950。回调以 ATR 911 为防线。\n"
        "> **概率参考**：方向偏多但短线超买，故看多 35%。"
    )
    html_out = "".join(blocks)
    assert "<blockquote>" in html_out
    lines = html_out.split("<br>")
    # 推导逻辑(2句) + 概率参考(1句) = 3 行, 标签各自成行
    assert len(lines) == 3
    assert "推导逻辑" in lines[0]
    assert "概率参考" in lines[2]
    assert "防线" in lines[1]
