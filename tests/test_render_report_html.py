"""render_report_html 渲染器测试 — markdown → 本地 HTML."""
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
