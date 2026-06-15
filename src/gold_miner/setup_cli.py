"""交互式 Setup 向导 — 初始化新用户的项目环境."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from gold_miner.config import settings
from gold_miner.doctor import run_doctor

EXAMPLE_FILES = [
    "portfolio.example.yaml",
    "trade_log.example.md",
    "personal_rules.example.md",
    "investor_profile.example.md",
    "doctrine_state.example.json",
    "jd_ms_gold_history.example.csv",
]

RISK_PROFILES = {
    "aggressive": "激进型 — 高波动容忍，追求最大收益",
    "moderate": "平衡型 — 中等回撤容忍（10-20%），攻守兼备",
    "conservative": "保守型 — 低波动优先，本金保护",
}


def _prompt_choice(prompt: str, choices: list[str], default: str | None = None) -> str:
    """交互式选择提示."""
    print(f"\n{prompt}")
    for i, c in enumerate(choices, 1):
        marker = " (默认)" if c == default else ""
        print(f"  {i}. {c}{marker}")
    while True:
        try:
            inp = input("  选择 (数字或名称): ").strip()
            if not inp and default:
                return default
            if inp.isdigit():
                idx = int(inp) - 1
                if 0 <= idx < len(choices):
                    return choices[idx]
            if inp in choices:
                return inp
            print("  无效输入，请重试")
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)


def _prompt_yes_no(prompt: str, default: bool = True) -> bool:
    """Yes/No 提示."""
    suffix = " [Y/n]" if default else " [y/N]"
    while True:
        try:
            inp = input(f"{prompt}{suffix}: ").strip().lower()
            if not inp:
                return default
            if inp in ("y", "yes"):
                return True
            if inp in ("n", "no"):
                return False
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)


def _create_env_example() -> Path:
    """创建 .env.example 模板文件."""
    path = Path(".env.example")
    content = '''# API Keys (至少配置一个以启用对应功能)
FRED_API_KEY=your_fred_key_here
NEWS_API_KEY=your_newsapi_key_here
TAVILY_API_KEY=your_tavily_key_here
LLM_API_KEY=your_llm_key_here
LLM_API_BASE=https://api.deepseek.com/anthropic
LLM_MODEL=deepseek-v4-pro

# Yahoo Finance Symbols
YAHOO_SYMBOL_SPOT=XAUUSD=X
YAHOO_SYMBOL_GLD=GLD
YAHOO_SYMBOL_IAU=IAU
YAHOO_SYMBOL_DXY=DX-Y.NYB

# Trading Parameters
INITIAL_CAPITAL_USD=100000
MAX_POSITION_PCT=0.8
STOP_LOSS_PCT=0.03
TAKE_PROFIT_PCT=0.06

# Risk Profile: aggressive | moderate | conservative
RISK_PROFILE=moderate

# Notification
WECHAT_WEBHOOK_URL=
ENABLE_NOTIFICATION=false

# Self-Improvement
ENABLE_AUTO_TRACKING=true

# Polymarket
POLYMARKET_ENABLED=true
POLYMARKET_MIN_VOLUME=500
POLYMARKET_MAX_MARKETS=20

# Proxy (optional)
MIHOMO_SUB_URL=

# Agent Scheduler
AGENT_ENABLED=false
AGENT_TIMEZONE=Asia/Shanghai
AGENT_SCHEDULE_PRE_MARKET=08:00
AGENT_SCHEDULE_POST_OPEN=09:30
AGENT_SCHEDULE_CLOSING=14:30
AGENT_SCHEDULE_EVENT_SCAN=20:30
AGENT_SCHEDULE_WEEKLY=sun-21:00
'''
    path.write_text(content, encoding="utf-8")
    return path


def _copy_examples_to_private() -> list[Path]:
    """将 data/*.example.* 复制到 data/private/（如果不存在对应文件）."""
    data_dir = Path("data")
    private_dir = settings.private_data_path
    copied: list[Path] = []

    for example_name in EXAMPLE_FILES:
        example_path = data_dir / example_name
        if not example_path.exists():
            continue
        # 去掉 .example 后缀得到目标文件名
        stem = example_name.replace(".example", "")
        target = private_dir / stem
        if target.exists():
            continue
        shutil.copy2(example_path, target)
        copied.append(target)

    return copied


def run_setup(non_interactive: bool = False) -> int:
    """运行 setup 向导.

    Args:
        non_interactive: 非交互模式，仅复制示例文件并运行 doctor。

    Returns:
        退出码 0=成功, 1=失败。
    """
    print("=" * 60)
    print("  Gold Miner 初始化向导")
    print("=" * 60)
    print()
    print("  本向导将帮助您配置运行环境：")
    print("  1. 创建 .env 配置文件")
    print("  2. 复制示例数据文件到 data/private/")
    print("  3. 设置风险偏好")
    print("  4. 运行环境诊断")
    print()

    # Step 1: .env
    env_path = Path(".env")
    env_example = Path(".env.example")

    if not env_example.exists():
        env_example = _create_env_example()
        print(f"  已创建模板: {env_example}")

    if not env_path.exists():
        if non_interactive or _prompt_yes_no("创建 .env 配置文件？"):
            shutil.copy2(env_example, env_path)
            print(f"  ✅ 已创建: {env_path}")
            print("  ⚠️  请编辑 .env 填入您的 API Keys")
        else:
            print("  ⚠️  跳过 .env 创建（部分功能将不可用）")
    else:
        print("  ✅ .env 已存在，跳过创建")

    # Step 2: 复制示例数据
    copied = _copy_examples_to_private()
    if copied:
        print(f"  ✅ 已复制 {len(copied)} 个示例文件到 data/private/:")
        for p in copied:
            print(f"     - {p.name}")
        print("  ⚠️  请编辑这些文件填入您的实际数据")
    else:
        print("  ✅ data/private/ 已存在数据文件，跳过复制")

    # Step 3: 风险偏好（仅交互模式）
    if not non_interactive and env_path.exists():
        print()
        print("  设置风险偏好:")
        for k, v in RISK_PROFILES.items():
            print(f"    {k}: {v}")
        risk = _prompt_choice(
            "选择风险偏好",
            list(RISK_PROFILES.keys()),
            default="moderate",
        )
        # 更新 .env 中的 RISK_PROFILE
        env_lines = env_path.read_text(encoding="utf-8").splitlines()
        new_lines: list[str] = []
        updated = False
        for line in env_lines:
            if line.startswith("RISK_PROFILE="):
                new_lines.append(f"RISK_PROFILE={risk}")
                updated = True
            else:
                new_lines.append(line)
        if not updated:
            new_lines.append(f"\nRISK_PROFILE={risk}")
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        print(f"  ✅ 风险偏好已设置为: {risk}")

    # Step 4: 运行 doctor
    print()
    print("  正在运行环境诊断...")
    print()
    exit_code = run_doctor()

    if exit_code == 0:
        print()
        print("=" * 60)
        print("  🎉 初始化完成！")
        print("=" * 60)
        print()
        print("  下一步建议：")
        print("    gold-miner quote          # 获取实时金价")
        print("    gold-miner scan           # 运行完整分析扫描")
        print("    gold-miner doctrine --list # 查看投资军规")
        print()
        print("  编辑 data/private/portfolio.yaml 填入您的持仓信息")
        print("  编辑 .env 填入 API Keys 以启用全部功能")
        print()
    else:
        print()
        print("  诊断发现问题，请修复后重新运行 gold-miner setup")
        print()

    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(description="Gold Miner 初始化向导")
    parser.add_argument("--non-interactive", action="store_true", help="非交互模式，用于 CI/自动化")
    args = parser.parse_args()
    sys.exit(run_setup(non_interactive=args.non_interactive))


if __name__ == "__main__":
    main()
