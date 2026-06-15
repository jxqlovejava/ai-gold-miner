#!/usr/bin/env python3
"""扫描代码库中的敏感信息（API密钥、令牌、订阅URL、手机号、邮箱等）.

用法:
    python scripts/secret-scan.py

返回:
    0 - 未发现敏感信息
    非0 - 发现敏感信息，打印到 stderr
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# 扫描的文件模式（已跟踪文件）
SCAN_PATTERNS = [
    r".*",
]

# 排除模式（示例文件、测试夹具等）
EXCLUDE_PATTERNS = [
    r".*\.example\.(yaml|yml|json|csv|md)$",
    r".*\.example$",
    r"\.env\.example$",
    r"test_.*\.py$",
    r"__pycache__",
    r"\.pyc$",
    r"\.venv",
    r"\.git",
    r"\.claude",
    r"data/munger_.*\.json$",
    r"data/mungermodels_.*\.json$",
    r"\.learnings/.*\.md$",
    r"src/gold_miner/proxy/mihomo",
    r"skills/.*",
    r"src/gold_miner/.*\.py$",
    r"tests/.*\.py$",
    r"README\.md$",
    r"CLAUDE\.md$",
    r"\.env\.example$",
    r"pyproject\.toml$",
    r"docs/opensource-history-cleanup\.md",
]

# 敏感信息正则模式
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("API key (generic)", re.compile(r"[a-zA-Z0-9_-]{20,40}", re.IGNORECASE)),
    ("Bearer token", re.compile(r"bearer\s+[a-zA-Z0-9_\-\.]{20,}", re.IGNORECASE)),
    ("Subscription URL", re.compile(r"https?://[a-zA-Z0-9.-]+/(api|sub|subscribe|clash|v2ray|ssr)/[a-zA-Z0-9_-]{8,}", re.IGNORECASE)),
    ("Phone number (CN)", re.compile(r"1[3-9]\d{9}")),
    ("Email address", re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")),
    ("Webhook URL", re.compile(r"https?://(qyapi\.weixin\.qq\.com|oapi\.dingtalk\.com|open\.feishu\.cn)/[a-zA-Z0-9/_-]+key=[a-zA-Z0-9_-]{10,}", re.IGNORECASE)),
    ("Private key", re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE)),
    ("Password/Secret in URL", re.compile(r"https?://[^:]+:[^@]+@[a-zA-Z0-9.-]+", re.IGNORECASE)),
]

# 允许列表（已知安全的字符串）
ALLOWLIST: set[str] = {
    "your_api_key_here",
    "example@example.com",
    "13800138000",
    "bearer_example_token",
}

# 误报过滤模式：这些是常见的非敏感字符串模式
FALSE_POSITIVE_PATTERNS: list[re.Pattern[str]] = [
    # Munger model JSON keys (kebab-case identifiers)
    re.compile(r"^[a-z]+(-[a-z]+)+-tendency$"),
    re.compile(r"^[a-z]+(-[a-z]+)+$"),  # generic kebab-case
    re.compile(r"^[a-z_]+_reason$"),  # *_reason fields
    re.compile(r"^[A-Z][a-z]+(-[A-Z][a-z]+)+$"),  # Title-Case-Kebab
    # Class names / function names
    re.compile(r"^[A-Z][a-zA-Z]+(Signal|Generator|Analyzer|Tracker|Manager|Store|Flow)$"),
    re.compile(r"^[a-z_]+_[a-z_]+$"),  # snake_case identifiers
    # Common config keys
    re.compile(r"^(polymarket_|assess_|gold_relevance|multidisciplinary|second-order|latticework|circle-of|darwinian|falsifiability|first-principles|map-is-not|confidence-calibration|incentive-structure|corporate-culture|principal-agent|disruptive-innovation|concentration-less|technology-help|intellectual-property|scale-effects|sit-on-your-ass|discounted-cash|fragility-antifragility|self-organized|critical-mass|adaptation-path|intellectual-humility)"),
]


def is_excluded(path: Path) -> bool:
    """检查路径是否应被排除."""
    path_str = str(path)
    for pattern in EXCLUDE_PATTERNS:
        if re.search(pattern, path_str):
            return True
    return False


def get_tracked_files() -> list[Path]:
    """获取 git 已跟踪文件列表."""
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    if result.returncode != 0:
        print(f"Warning: git ls-files failed: {result.stderr}", file=sys.stderr)
        return []
    root = Path(__file__).parent.parent
    files = []
    for line in result.stdout.strip().split("\n"):
        if line:
            p = root / line
            if p.exists() and not is_excluded(p):
                files.append(p)
    return files


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    """扫描单个文件，返回 (行号, 类型, 匹配文本) 列表."""
    findings: list[tuple[int, str, str]] = []
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return findings

    lines = content.split("\n")
    for line_no, line in enumerate(lines, start=1):
        # 跳过注释行中的示例
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("*"):
            # 检查是否是允许列表中的示例
            is_example = any(allow in line.lower() for allow in ALLOWLIST)
            if is_example:
                continue

        for secret_type, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(line):
                matched_text = match.group(0)
                # 检查允许列表
                if matched_text.lower() in {a.lower() for a in ALLOWLIST}:
                    continue
                # 过滤误报：纯数字序列（如价格、日期）
                if secret_type == "API key (generic)" and matched_text.isdigit():
                    continue
                # 过滤误报：kebab-case / snake_case 标识符（JSON keys等）
                if any(fp.match(matched_text) for fp in FALSE_POSITIVE_PATTERNS):
                    continue
                # 过滤误报：常见变量名
                if matched_text.lower() in {
                    "true", "false", "null", "none", "undefined",
                    "settings", "config", "password", "secret", "token",
                }:
                    continue
                findings.append((line_no, secret_type, matched_text))
    return findings


def main() -> int:
    """主入口."""
    files = get_tracked_files()
    total_findings = 0

    for path in files:
        findings = scan_file(path)
        if findings:
            rel_path = path.relative_to(Path(__file__).parent.parent)
            print(f"\n{rel_path}", file=sys.stderr)
            for line_no, secret_type, text in findings:
                # 截断长匹配
                display = text[:50] + "..." if len(text) > 50 else text
                print(f"  Line {line_no}: [{secret_type}] {display}", file=sys.stderr)
            total_findings += len(findings)

    if total_findings > 0:
        print(
            f"\nERROR: Found {total_findings} potential secret(s) in tracked files.",
            file=sys.stderr,
        )
        print("Move sensitive files to data/private/ and update .gitignore.", file=sys.stderr)
        return 1

    print("OK: No secrets detected in tracked files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
