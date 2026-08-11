#!/usr/bin/env python3
"""金价分析 markdown 报告 → 本地 HTML 渲染器.

2026-08-11 Req3C: 每次分析输出 markdown 报告后, 运行本脚本生成
data/output/金价分析_YYYY-MM-DD.html (复用 v9-gold-plan.html 金色主题),
终端末尾打印 file:// 绝对路径, 复制即可浏览器打开.

纯代码拼字符串, 零外部依赖 (不依赖 .venv 的 markdown 库).
用法:
    python3 scripts/render_report_html.py                        # 自动选最新 data/output/金价分析_*.md
    python3 scripts/render_report_html.py --input <md路径>        # 指定输入
    python3 scripts/render_report_html.py --out <html路径>        # 指定输出
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"

# ═══════════════════════════════════════════════════════════════
# CSS — 复用 data/output/v9-gold-plan.html 金色主题 (只保留扫描报告所需组件)
# ═══════════════════════════════════════════════════════════════

CSS_TEMPLATE = """:root {
  --gold: #d4a017; --gold-dark: #8a6d00; --gold-light: #f4e9c8;
  --bg: #faf8f2; --card: #ffffff; --text: #2b2b2b; --muted: #6b6b6b;
  --green: #1a7f37; --green-bg: #e6f4ea; --red: #b3261e; --red-bg: #fdecea;
  --amber-bg: #fff8e6; --border: #e5e0d5; --radius: 12px; --shadow: 0 2px 8px rgba(0,0,0,0.06);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; background: var(--bg); color: var(--text); line-height: 1.7; padding: 24px 16px 60px; }
.page { max-width: 920px; margin: 0 auto; }
.hero { background: linear-gradient(135deg, #2b2110 0%, #4a3a15 60%, #6b551d 100%); color: #fff; border-radius: 16px; padding: 32px 28px; margin-bottom: 24px; box-shadow: var(--shadow); }
.hero h1 { font-size: 26px; margin-bottom: 8px; letter-spacing: 1px; }
.hero .sub { color: #e8d9a8; font-size: 14px; }
.hero .tag { display: inline-block; background: rgba(212,160,23,0.25); color: #f4e9c8; border: 1px solid rgba(212,160,23,0.5); border-radius: 20px; padding: 2px 12px; font-size: 12px; margin-top: 12px; }
.status-bar { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 24px; }
.status-item { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; box-shadow: var(--shadow); }
.status-item .label { font-size: 12px; color: var(--muted); }
.status-item .value { font-size: 18px; font-weight: 700; margin-top: 4px; }
.status-item .value.gold { color: var(--gold-dark); }
.status-item .value.green { color: var(--green); }
.status-item .value.red { color: var(--red); }
.section { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 24px; margin-bottom: 20px; box-shadow: var(--shadow); }
h2 { font-size: 19px; margin-bottom: 16px; padding-bottom: 8px; border-bottom: 2px solid var(--gold); color: var(--gold-dark); display: flex; align-items: center; gap: 8px; }
h3 { font-size: 15px; margin: 16px 0 8px; color: var(--text); }
table { border-collapse: collapse; width: 100%; margin: 8px 0 16px; }
th, td { border: 1px solid var(--border); padding: 8px 10px; text-align: left; font-size: 13px; vertical-align: top; }
th { background: var(--gold-light); font-weight: 600; color: var(--gold-dark); }
tr:nth-child(even) { background: #fcfbf7; }
blockquote { background: var(--amber-bg); border-left: 4px solid var(--gold); padding: 12px 16px; margin: 12px 0; border-radius: 0 8px 8px 0; font-size: 13px; }
.highlight { background: #fff8e6; border: 1px solid #ecd9a0; border-radius: 8px; padding: 10px 14px; margin: 12px 0; font-size: 13px; }
ul, ol { padding-left: 22px; margin: 8px 0 12px; }
li { margin: 4px 0; font-size: 13px; }
.ok { color: var(--green); font-weight: 600; }
.warn { color: var(--red); font-weight: 600; }
.amber { color: #9a6a00; font-weight: 600; }
hr { border: none; border-top: 1px solid var(--border); margin: 16px 0; }
pre { background: #f5f1e6; border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; overflow-x: auto; font-size: 12px; margin: 8px 0 16px; white-space: pre; }
.decision-card { background: linear-gradient(135deg, #faf6ec 0%, #f4e9c8 100%); border: 2px solid var(--gold); border-radius: var(--radius); padding: 18px 20px; }
.decision-main { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.decision-label { font-size: 13px; color: var(--gold-dark); font-weight: 600; letter-spacing: 1px; }
.decision-value { font-size: 30px; font-weight: 800; color: var(--gold-dark); line-height: 1.2; }
.decision-meta { display: flex; gap: 8px; flex-wrap: wrap; font-size: 13px; color: var(--muted); }
.decision-meta span { background: rgba(212,160,23,0.14); padding: 2px 10px; border-radius: 20px; }
.decision-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin-top: 14px; }
.df-item { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; }
.df-label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 2px; }
.df-value { font-size: 14px; font-weight: 600; }
footer { text-align: center; color: #aaa; font-size: 12px; margin-top: 32px; padding-top: 16px; border-top: 1px solid var(--border); }
@media (max-width: 600px) { .hero h1 { font-size: 21px; } .section { padding: 16px; } table { font-size: 12px; } th, td { padding: 6px 8px; } }
"""


# ═══════════════════════════════════════════════════════════════
# markdown → HTML (最小子集: 标题/表格/粗体/列表/块引用/分隔线/段落)
# ═══════════════════════════════════════════════════════════════

_INLINE_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


_STATUS_ICONS = {
    "⚠️": "warn",
    "🔴": "warn",
    "❌": "warn",
    "✅": "ok",
    "🟢": "ok",
    "🟡": "amber",
    "🔜": "amber",
}


def _inline(text: str) -> str:
    """行内: **bold** → <strong>; ⚠️/✅/🟢/🔴 图标 → 彩色 span."""
    # 转义 HTML 特殊字符, 但保留 ** 以便后续处理
    esc = html.escape(text, quote=False)
    # 状态图标 → 彩色 span (在 bold 处理之前, 图标本身不转义)
    for icon, cls in _STATUS_ICONS.items():
        esc = esc.replace(icon, f'<span class="{cls}">{icon}</span>')
    esc = esc.replace("**", "\x00B\x00")  # 临时占位
    # 用占位法: 按 ** 切分, 奇数段加 <strong>
    parts = esc.split("\x00B\x00")
    out = []
    for i, part in enumerate(parts):
        out.append(f"<strong>{part}</strong>" if i % 2 == 1 else part)
    return "".join(out)


def _table_block(lines: list[str]) -> str:
    """markdown 表格块 → <table>. lines 以表头行开始, 含 |---| 分隔行."""
    header_cells = [c.strip() for c in lines[0].strip().strip("|").split("|")]
    rows: list[list[str]] = []
    for ln in lines[1:]:
        row = [c.strip() for c in ln.strip().strip("|").split("|")]
        rows.append(row)

    thead = "".join(f"<th>{_inline(h)}</th>" for h in header_cells)
    body = ""
    for row in rows:
        body += "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>"

    return (
        f"<table><thead><tr>{thead}</tr></thead><tbody>{body}</tbody></table>"
    )


def _list_block(prefix: str, lines: list[str]) -> str:
    """无序列表 (-/✓/✗) 或有序列表 (N.) → <ul>/<ol>.

    ✓ 前缀的项 → 绿 (.ok); ✗ 前缀 → 红 (.warn).
    """
    tag = "ul" if prefix in ("-", "✓", "✗") else "ol"
    items = []
    for ln in lines:
        stripped = ln.strip()
        # 剥离前缀: "- " / "✓ " / "✗ " / "N. "
        content = re.sub(r"^([-*✓✗]|\d+\.)\s+", "", stripped)
        cls = ""
        if stripped.startswith("✓"):
            cls = ' class="ok"'
        elif stripped.startswith("✗"):
            cls = ' class="warn"'
        items.append(f"<li{cls}>{_inline(content)}</li>")
    return f"<{tag}>" + "".join(items) + f"</{tag}>"


_ASCII_BOX_CHARS = ("┌", "├", "└", "│")


def _ascii_table_block(lines: list[str]) -> str:
    """ASCII 框线表格 (┌│└ 字符画) → <table>.

    来源: SignalBundle.format_dimension_table() 等程序化输出.
    识别规则: 分隔行 (以 ┌/├/└ 开头) 跳过; 数据行 (以 │ 开头) 按 │ 切分.
    第一组数据行是表头 (分隔行 ├ 之前).
    """
    header: list[str] | None = None
    rows: list[list[str]] = []
    for ln in lines:
        s = ln.strip()
        if not s or not s.startswith("│"):
            continue  # 分隔行/空行跳过
        cells = [c.strip() for c in s.strip("│").split("│")]
        if header is None:
            header = cells
        else:
            rows.append(cells)
    if header is None:
        return "<pre><code>" + html.escape("\n".join(lines)) + "</code></pre>"

    thead = "".join(f"<th>{_inline(h)}</th>" for h in header)
    body = ""
    for row in rows:
        body += "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>"
    return f"<table><thead><tr>{thead}</tr></thead><tbody>{body}</tbody></table>"


_DECISION_H2_RE = re.compile(
    r"^##\s+📌\s*决策:\s*\*\*(.+?)\*\*\s*(?:\|\s*(.+?))?\s*$"
)


def _decision_card(value: str, meta: str, fields: list[tuple[str, str]]) -> str:
    """决策摘要卡片: 突出决策值, 附评分/置信度 + 建议仓位/止损/止盈字段."""
    meta_parts = [p.strip() for p in meta.split("|") if p.strip()]
    meta_html = "".join(f"<span>{_inline(p)}</span>" for p in meta_parts)
    fields_html = ""
    if fields:
        items = "".join(
            f'<div class="df-item"><span class="df-label">{_inline(lbl)}</span>'
            f'<span class="df-value">{_inline(val)}</span></div>'
            for lbl, val in fields
        )
        fields_html = f'<div class="decision-fields">{items}</div>'
    return (
        '<div class="decision-card"><div class="decision-main">'
        f'<span class="decision-label">📌 决策</span>'
        f'<span class="decision-value">{_inline(value)}</span>'
        f'<div class="decision-meta">{meta_html}</div></div>'
        f"{fields_html}</div>"
    )


def _md_to_html(md: str) -> list[str]:
    """把 markdown 逐块解析为 HTML 片段列表 (每个 section 一个元素).

    返回 list[str], 每个元素是纯 HTML (不含 .section 包裹), 由 render() 逐个包成 .section.
    """
    blocks: list[str] = []
    lines = md.splitlines()
    i = 0
    n = len(lines)

    def _heading(ln: str) -> str | None:
        m = re.match(r"^(#{1,3})\s+(.*)$", ln)
        if not m:
            return None
        # # → h1 (仅报告首行, 由 render() 消费为 hero; 此处保持映射一致)
        # ## → h2 (section 标题), ### → h3
        level = min(len(m.group(1)), 3)
        return f"<h{level}>{_inline(m.group(2))}</h{level}>"

    while i < n:
        ln = lines[i].rstrip()

        if not ln.strip():
            i += 1
            continue

        # 分隔线
        if re.match(r"^-{3,}$", ln.strip()):
            blocks.append("<hr>")
            i += 1
            continue

        # 块引用
        if ln.startswith(">"):
            quote_lines = []
            while i < n and lines[i].startswith(">"):
                quote_lines.append(lines[i][1:].strip())
                i += 1
            blocks.append(f"<blockquote>{_inline(' '.join(quote_lines))}</blockquote>")
            continue

        # 决策摘要卡片: "## 📌 决策: **持有** | 综合评分 +0.26 | 置信度 72%"
        # 解析决策值 + meta, 并吞掉后续 "建议仓位:/止损:/止盈:" 字段行 → 决策卡片
        dm = _DECISION_H2_RE.match(ln.strip())
        if dm:
            value = dm.group(1).strip()
            meta = dm.group(2) or ""
            i += 1
            fields: list[tuple[str, str]] = []
            while i < n:
                fld = lines[i].strip()
                if not fld:
                    break  # 空行结束字段区
                fm = re.match(r"^(.+?):\s+(.+)$", fld)
                if not fm:
                    break
                fields.append((fm.group(1).strip(), fm.group(2).strip()))
                i += 1
            blocks.append(_decision_card(value, meta, fields))
            continue

        # 标题
        h = _heading(ln)
        if h:
            blocks.append(h)
            i += 1
            continue

        # 列表: 支持缩进的无序/有序项 + Agent 博弈的 ✓/✗ 前缀
        # (必须在表格检测之前 — 防止 "- x | y" 列表行被误判为表格)
        if re.match(r"^\s*[-*]\s+", ln):
            list_lines = []
            while i < n and re.match(r"^\s*[-*]\s+", lines[i]):
                list_lines.append(lines[i])
                i += 1
            blocks.append(_list_block("-", list_lines))
            continue
        if re.match(r"^\s*\d+\.\s+", ln):
            list_lines = []
            while i < n and re.match(r"^\s*\d+\.\s+", lines[i]):
                list_lines.append(lines[i])
                i += 1
            blocks.append(_list_block(".", list_lines))
            continue
        if re.match(r"^\s*[✓✗]\s+", ln):
            list_lines = []
            while i < n and re.match(r"^\s*[✓✗]\s+", lines[i]):
                list_lines.append(lines[i])
                i += 1
            blocks.append(_list_block("✓", list_lines))
            continue

        # 表格: 有 | 分隔行 或 下一行也含 | → 视为表格
        # (行首不是列表前缀 "x | y" 这类列表误判; **粗体** | 不受影响)
        _is_list_line = re.compile(r"^\s*[-*✓✗]\s|^\s*\d+\.\s")
        if (
            "|" in ln
            and not _is_list_line.match(ln)
            and i + 1 < n
            and (
                re.match(r"^\s*\|?[\s:\-|]+\|?\s*$", lines[i + 1])
                or ("|" in lines[i + 1] and not _is_list_line.match(lines[i + 1]))
            )
        ):
            table_lines = [ln]
            i += 1  # 不跳过, 由 while 吞掉分隔行和后续数据行
            if re.match(r"^\s*\|?[\s:\-|]+\|?\s*$", lines[i].strip()):
                i += 1  # 跳过分隔行
            while (
                i < n
                and "|" in lines[i]
                and lines[i].strip()
                and not _is_list_line.match(lines[i])
            ):
                table_lines.append(lines[i])
                i += 1
            blocks.append(_table_block(table_lines))
            continue

        # 代码块: ``` 围栏 → ASCII 框线表格渲染为表格, 其余 <pre><code>
        if ln.strip().startswith("```"):
            code_lines = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # 跳过结束围栏
            if code_lines and code_lines[0].strip().startswith(_ASCII_BOX_CHARS):
                blocks.append(_ascii_table_block(code_lines))
            else:
                blocks.append(
                    f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>"
                )
            continue

        # 裸 ASCII 框线表格 (无 ``` 围栏)
        if ln.strip().startswith(_ASCII_BOX_CHARS):
            ascii_lines = []
            while i < n and lines[i].strip().startswith(_ASCII_BOX_CHARS):
                ascii_lines.append(lines[i])
                i += 1
            blocks.append(_ascii_table_block(ascii_lines))
            continue

        # 段落 (排除列表/标题/块引用/表格起始行, 避免吞并后续列表项)
        # 注意: 排除正则要求列表前缀后跟空白 ([-*]\s 或 ✓✗\s 或 数字.\s),
        # 避免把 **粗体** (以 * 开头但非 * 空格) 误排除
        para_lines = []
        while (
            i < n
            and lines[i].strip()
            and not lines[i].startswith(("#", ">", "|"))
            and not re.match(r"^\s*[-*✓✗]\s|^\s*\d+\.\s", lines[i])
        ):
            para_lines.append(lines[i])
            i += 1
        if para_lines:
            blocks.append(f"<p>{_inline(' '.join(p.strip() for p in para_lines))}</p>")
            continue

        i += 1

    return blocks


# ═══════════════════════════════════════════════════════════════
# 渲染
# ═══════════════════════════════════════════════════════════════

def _hero(title: str, subtitle: str) -> str:
    return (
        f'<div class="hero"><h1>{html.escape(title)}</h1>'
        f'<div class="sub">{html.escape(subtitle)}</div></div>'
    )


def _status_bar(items: list[tuple[str, str, str]]) -> str:
    """status bar: items = [(label, value, css_class), ...]"""
    inner = "".join(
        f'<div class="status-item"><div class="label">{html.escape(lbl)}</div>'
        f'<div class="value {cls}">{html.escape(val)}</div></div>'
        for lbl, val, cls in items
    )
    return f'<div class="status-bar">{inner}</div>'


def _section(html_inner: str) -> str:
    return f'<div class="section">{html_inner}</div>'


def render(md: str, out_path: Path) -> Path:
    """解析 md → 渲染 HTML → 写文件, 返回输出路径."""
    lines = md.splitlines()

    # 首行 # 标题 → hero; 紧跟 > 引文 → status-bar 首项
    title = "金价分析报告"
    subtitle = "AI Gold Miner · 青蚨"
    status_items: list[tuple[str, str, str]] = []
    body_start = 0

    for idx, ln in enumerate(lines):
        stripped = ln.strip()
        if not stripped:
            continue  # 跳过空行 (标题与引文之间可能有空行)
        if stripped.startswith("# ") and title == "金价分析报告":
            title = stripped[2:].strip()
        elif stripped.startswith(">"):
            quote = stripped[1:].strip()
            # 解析 "积存金 **¥958.81** | 国际 $4408/oz | 净保本 ¥894.38" → 拆成 status items
            parts = [p.strip() for p in quote.split("|")]
            for part in parts:
                if part.startswith("积存金"):
                    m = re.search(r"¥([\d,.]+)", part)
                    if m:
                        status_items.append(("积存金", f"¥{m.group(1)}", "gold"))
                elif part.startswith("国际"):
                    m = re.search(r"\$([\d,.]+)", part)
                    if m:
                        status_items.append(("XAUUSD", f"${m.group(1)}", "green"))
                else:
                    status_items.append(("行情", part, ""))
            body_start = idx + 1
            break
        else:
            body_start = idx
            break

    blocks = _md_to_html("\n".join(lines[body_start:]))

    # 每个 h2 开始新 section; 非 h2 块归入当前 section
    sections: list[str] = []
    current: list[str] = []
    for blk in blocks:
        if blk.startswith("<h2"):
            if current:
                sections.append(_section("\n".join(current)))
                current = []
            current.append(blk)
        else:
            current.append(blk)
    if current:
        sections.append(_section("\n".join(current)))

    page_html = "\n".join(sections)

    # 拼装完整 HTML
    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(title)}</title>
<style>
{CSS_TEMPLATE}
</style>
</head>
<body>
<div class="page">
{_hero(title, subtitle)}
{_status_bar(status_items) if status_items else ''}
{page_html}
<footer>AI Gold Miner · 青蚨 · 仅供个人投资决策参考</footer>
</div>
</body>
</html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")
    return out_path


def _latest_md() -> Path | None:
    """自动选最新 data/output/金价分析_*.md."""
    candidates = sorted(OUTPUT_DIR.glob("金价分析_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="金价分析 markdown → 本地 HTML")
    parser.add_argument("--input", help="输入 markdown 报告路径 (默认最新 data/output/金价分析_*.md)")
    parser.add_argument("--out", help="输出 HTML 路径 (默认 data/output/金价分析_YYYY-MM-DD.html)")
    args = parser.parse_args(argv)

    if args.input:
        md_path = Path(args.input)
    else:
        md_path = _latest_md()
        if md_path is None:
            print("未找到 data/output/金价分析_*.md, 请用 --input 指定", file=sys.stderr)
            return 1

    if not md_path.exists():
        print(f"输入文件不存在: {md_path}", file=sys.stderr)
        return 1

    md = md_path.read_text(encoding="utf-8")

    if args.out:
        out_path = Path(args.out)
    else:
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d")
        out_path = OUTPUT_DIR / f"金价分析_{ts}.html"

    render(md, out_path)
    abs_path = out_path.resolve()
    print(f"📄 HTML 报告: file://{abs_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
