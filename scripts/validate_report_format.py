#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""金价报告格式校验 — 程序化强制「板块间禁止独立 --- 分隔线，仅空行分隔」。

背景（2026-08-22 用户要求「不要靠记忆约束，内化到程序里」）：
    `---` 分隔线规则此前只写在 memory / SKILL.md 文本里，靠模型自觉执行不可靠
    （2026-08-22 曾复发）。本脚本把规则内化为程序强制：
      1. PostToolUse hook（绑 Write）：Write 报告文件时自动校验，违规 → exit 2 → hook fail 拦截；
      2. 手动 / 渲染前自检：`python3 scripts/validate_report_format.py --file <path>`
      3. render_report_html.py 渲染前调用，违规打印警告。

规则：
    - 报告正文中**不允许**出现独立成行的 `---`（markdown 水平线分隔）；
    - **允许**：表格表头分隔行 `|---|...|`（含 `|`，不匹配）、YAML frontmatter 首尾 `---`；
    - 判定为报告文件：路径含 data/output/ 且为 .md，或文件名含 金价分析/scan_report/analysis。

exit code: 0=通过  |  2=存在违规
"""
import json
import re
import sys

SEP_RE = re.compile(r'^---+[ \t]*$')  # 独立 --- 行；表格行 |---| 含 | 不匹配，frontmatter 单行 --- 匹配但单独排除

REPORT_PATH_MARKERS = ('/data/output/',)
REPORT_NAME_MARKERS = ('金价分析', 'scan_report', 'analysis_')


def is_report_file(path: str) -> bool:
    """是否是需要强制格式的报告文件。"""
    if not path:
        return False
    name = path.replace('\\', '/')
    if name.endswith('.md'):
        for marker in REPORT_PATH_MARKERS:
            if marker in name:
                return True
        base = name.rsplit('/', 1)[-1].lower()
        for marker in REPORT_NAME_MARKERS:
            if marker in base:
                return True
    return False


def find_violations(text: str):
    """返回违规 (行号, 行内容) 列表。独立 --- 行（非表格、非 frontmatter）。"""
    lines = text.split('\n')
    n = len(lines)
    skip_ranges = []
    # YAML frontmatter：首行是 ---，则跳到配对的第二个 --- 之前
    if n >= 1 and SEP_RE.match(lines[0]):
        for i in range(1, n):
            if SEP_RE.match(lines[i]):
                skip_ranges.append((0, i))  # 闭区间 [0, i] 内的 --- 不算违规
                break
    violations = []
    for idx, line in enumerate(lines, start=1):
        if not SEP_RE.match(line):
            continue
        if any(start <= idx - 1 <= end for start, end in skip_ranges):
            continue
        violations.append((idx, line.rstrip()))
    return violations


def _check(text: str, path: str = '') -> int:
    violations = find_violations(text)
    if violations:
        # 失败信息走 stderr：PostToolUse hook 靠「exit 非 0 + stderr」向模型呈现拦截原因（stdout 不会被 hook 捕获）
        err = [f'❌ 报告格式校验失败（{path or "输出"}）: 板块间禁止独立 "---" 分隔线, 仅用空行分隔。发现 {len(violations)} 处:']
        for line_no, content in violations:
            err.append(f'   第 {line_no} 行: {content}')
        err.append('修复: 删除这些独立 "---" 行, 用空行分隔板块。表格表头 |---|---| 属 markdown 语法, 不受影响。')
        print('\n'.join(err), file=sys.stderr)
        return 2
    print(f'✅ 报告格式校验通过（{path or "输出"}）: 无独立 "---" 分隔线')
    return 0


def main() -> int:
    args = sys.argv[1:]
    path = None
    text = None

    if '--file' in args:
        idx = args.index('--file')
        if idx + 1 < len(args):
            path = args[idx + 1]
        if not path:
            print('用法: python3 scripts/validate_report_format.py --file <报告.md>', file=sys.stderr)
            return 2
        with open(path, encoding='utf-8') as fh:
            text = fh.read()
        # --file 手动模式: 无条件校验（显式指定即要检查）；hook 模式才做报告文件过滤
        return _check(text, path)

    # hook 模式：stdin 是 PostToolUse JSON
    if not sys.stdin.isatty():
        try:
            payload = json.load(sys.stdin)
        except Exception:
            return 0  # 非 hook JSON，放行
        ti = payload.get('tool_input') or {}
        path = ti.get('file_path') or ''
        content = ti.get('content')
        if content is None:
            if path and is_report_file(path):
                try:
                    with open(path, encoding='utf-8') as fh:
                        content = fh.read()
                except OSError:
                    return 0
            else:
                return 0
        if not is_report_file(path):
            return 0
        return _check(content, path)

    print(__doc__)
    return 0


if __name__ == '__main__':
    sys.exit(main())
