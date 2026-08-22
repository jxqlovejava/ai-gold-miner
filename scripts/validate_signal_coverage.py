#!/usr/bin/env python3
"""信号维度覆盖完整性校验.

2026-08-17 系统性修复: 分析曾绕过 gold-miner scan 主路径, 手工构建 SignalBundle
导致遗漏 oil / jd_blogger / jd_fund_bomb / hype_bias 等维度信号,
得出「油价上涨利多」的错误 oil 结论 (真实 OilSignalGenerator 输出 rate_relief 利多)。

2026-08-22 MECE 维度重构: oil 并入 fundamental, hype_bias 并入 sentiment (情绪面质量门),
不再作为独立必检维度; oil 信号在 fundamental 维度内以 channel metadata 识别。

用法:
    PYTHONPATH=src python3 scripts/validate_signal_coverage.py            # 校验默认维度清单
    PYTHONPATH=src python3 scripts/validate_signal_coverage.py --bundle  # 校验 stdin 传入的 bundle JSON

校验规则:
    1. 核心维度 (technical/fundamental/news/sentiment/event/smart_money/jd_*) 必须都有信号
    2. 油价派生信号 (fundamental 维度, name 含「油价」或 metadata 含 channel) 必须来自
       OilSignalGenerator 标准 metadata (wti/channel), 禁止手工臆造
    3. 预测市场/概率类信号 fact_type 必须为 projection (非事实)
退出码: 0=通过, 1=缺失维度, 2=oil 信号非标准, 3=预测数据标为事实
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# 核心维度清单 (对应 DimensionWeights + 信号生成器; 2026-08-22 MECE 重构后)
CORE_DIMENSIONS = [
    "technical",      # TechnicalAnalyzer + CandlestickPatternDetector + ChanlunSignalGenerator
    "fundamental",    # FundamentalAnalyzer + OilSignalGenerator (油价并入基本面)
    "news",           # NewsSignalGenerator
    "sentiment",      # SentimentAnalyzer + HypeBiasSignalGenerator (反带节奏质量门并入)
    "event",          # EventDriven + RecentEvents + EconomicCalendar + MacroPivot (事件类合一)
    "smart_money",    # CotSignalGenerator / EtfFlowSignalGenerator / InstitutionalSignalGenerator / JdFundBombSignalGenerator
    "jd_blogger",     # JdBloggerSentimentSignalGenerator (大V加仓榜情绪, 归属 sentiment 维度)
    "jd_fund_bomb",   # JdFundBombSignalGenerator (资金炸弹多空占比, 归属 smart_money 维度)
]

# 含 source/channel metadata 的 oil 信号才视为标准 (来自 OilSignalGenerator)
OIL_METADATA_KEYS = {"wti", "channel", "chg_1d_pct", "chg_5d_pct", "chg_20d_pct"}

# 预测市场/概率类关键词 (与 analysis._classify_fact_types 对齐)
PROJECTION_KEYWORDS = [
    "预测市场", "Polymarket", "Kalshi", "FedWatch", "CME Fed",
    "加息概率", "降息概率", "隐含概率", "概率为", "概率达",
]


def check_bundle(bundle: dict) -> list[str]:
    """校验 bundle 的维度覆盖与 oil/预测标注.

    bundle: {"signals": [{"dimension","name","direction","fact_type","description","metadata"}...]}
    """
    warnings: list[str] = []
    signals = bundle.get("signals", [])

    dims = {s.get("dimension") for s in signals}
    for dim in CORE_DIMENSIONS:
        if dim not in dims:
            warnings.append(f"[coverage] 缺失维度信号: {dim}")

    # oil 信号来源检查 (2026-08-22 起 oil 并入 fundamental 维度, 以 name「油价」或 metadata channel 识别)
    oil_sigs = [
        s for s in signals
        if s.get("dimension") == "fundamental"
        and ("channel" in (s.get("metadata") or {}) or "油价" in s.get("name", ""))
    ]
    for s in oil_sigs:
        meta = s.get("metadata") or {}
        has_std_meta = any(k in meta for k in OIL_METADATA_KEYS)
        if not has_std_meta:
            warnings.append(
                f"[oil] 信号「{s.get('name')}」缺 OilSignalGenerator 标准 metadata "
                f"(wti/channel), 疑似手工臆造 — 须用 OilSignalGenerator().generate_signals()"
            )

    # 预测市场数据 → 必须为 projection
    for s in signals:
        text = f"{s.get('name','')} {s.get('description','')}"
        if any(k.lower() in text.lower() for k in PROJECTION_KEYWORDS):
            ft = s.get("fact_type")
            if ft not in ("projection", "opinion"):
                warnings.append(
                    f"[fact_type] 「{s.get('name')}」含预测市场/概率表述但 fact_type={ft}, "
                    f"预测数据不得标为事实 (须 projection/opinion)"
                )

    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", action="store_true",
                        help="从 stdin 读取 bundle JSON 校验 (默认仅打印维度清单要求)")
    args = parser.parse_args()

    if args.bundle:
        try:
            bundle = json.load(sys.stdin)
        except json.JSONDecodeError as e:
            print(f"❌ bundle JSON 解析失败: {e}")
            return 1
        warnings = check_bundle(bundle)
    else:
        # 无 bundle 时: 只打印核心维度清单, 提示分析输出前手动核对
        print("=== 核心信号维度清单 (分析输出前必须逐项核对) ===")
        for dim in CORE_DIMENSIONS:
            print(f"  - {dim}")
        print()
        print("❓ 若要校验实际 bundle, 请用 --bundle 传入 JSON")
        return 0

    if warnings:
        print(f"❌ 发现 {len(warnings)} 项问题:")
        for w in warnings:
            print(f"  {w}")
        return 1
    print("✅ 维度覆盖与标注校验通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
