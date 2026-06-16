"""Doctrine command handler."""

from __future__ import annotations

import argparse
import random

from loguru import logger

from gold_miner.doctrine import (
    ALL_MODELS,
    ALL_RULES,
    ALL_STRATEGIES,
    DoctrineChecker,
    DoctrineStore,
    get_model_by_id,
    get_rule_by_id,
    get_strategy_by_id,
)
from gold_miner.doctrine.munger_models import (
    ALL_MODELS as MUNGER_ALL,
)
from gold_miner.doctrine.munger_models import (
    GOLD_MODELS as MUNGER_GOLD,
)
from gold_miner.doctrine.munger_models import (
    get_by_discipline,
    list_disciplines,
)
from gold_miner.doctrine.munger_models import (
    search as search_munger,
)


def run_doctrine(args: argparse.Namespace) -> None:
    """投资军规审查与知识库浏览."""

    # --list: 列出规则/策略/思维模型
    if args.list:
        list_type = args.type or "all"
        if list_type in ("rules", "all"):
            print(f"\n{'='*70}")
            print(f"  投资军规 ({len(ALL_RULES)}条)")
            print(f"{'='*70}")
            for r in ALL_RULES:
                sev_icon = {"block": "■", "warn": "◆", "info": "○"}.get(r.severity, "?")
                enabled = "✓" if r.enabled else "✗"
                print(f"  {sev_icon} [{r.severity.upper()}] {r.id} {r.name}  [{enabled}]")
                print(f"     {r.description}")
            print()

        if list_type in ("strategies", "all"):
            print(f"{'='*70}")
            print(f"  投资策略 ({len(ALL_STRATEGIES)}个)")
            print(f"{'='*70}")
            for s in ALL_STRATEGIES:
                regime_cn = {
                    "trending": "趋势市", "ranging": "震荡市", "crisis": "危机市",
                    "recovery": "复苏市", "all": "通用",
                }
                regime = regime_cn.get(s.applicable_regime, s.applicable_regime)
                print(f"  ▸ {s.id} {s.name} [{regime}]")
                print(f"     {s.description[:100]}...")
                if s.mental_models:
                    print(f"     关联模型: {', '.join(s.mental_models)}")
            print()

        if list_type in ("models", "all"):
            print(f"{'='*70}")
            print(f"  思维模型 ({len(ALL_MODELS)}个)")
            print(f"{'='*70}")
            for m in ALL_MODELS:
                print(f"  ▸ {m.id} {m.name}")
                print(f"     {m.key_principle}")
            print()

            # Munger 模型库概览
            if MUNGER_ALL:
                print(f"{'='*70}")
                print(f"  Munger 多元思维模型库 ({len(MUNGER_ALL)}个, 黄金相关 {len(MUNGER_GOLD)}个)")
                print(f"{'='*70}")
                disc_counts = list_disciplines()
                for dslug, dname in [
                    ("invest", "投资学与金融学"), ("decision", "投资原则与品格"),
                    ("psych", "心理学"), ("econ", "微观经济学"), ("math", "数学与统计学"),
                    ("mgmt", "管理学与商业"), ("meta", "元认知与思维方法论"),
                    ("complex", "复杂系统"), ("bio", "生物学与进化论"),
                    ("physics", "物理学与化学"), ("eng", "工程学"),
                    ("law", "法学与政治学"), ("history", "历史学与哲学"),
                    ("accounting", "会计学"),
                ]:
                    if dname in disc_counts:
                        mark = " *" if dslug in ("invest", "decision", "complex", "math", "econ", "meta", "psych", "mgmt") else ""
                        print(f"  {dname}: {disc_counts[dname]}个{mark}")
                print("  (* 标注学科与黄金投资直接相关)")
                print()

        return

    # --search: 搜索 Munger 模型库
    if args.search:
        query = args.search
        results = search_munger(query)
        print(f"\n搜索 '{query}' — 找到 {len(results)} 个模型")
        for m in results[:20]:
            gold_mark = " [黄金相关]" if m.gold_applicable else ""
            print(f"  ▸ {m.name_cn} ({m.name_en}){gold_mark}")
            if m.description:
                print(f"     {m.description[:80]}...")
            print(f"     学科: {m.discipline} | {m.url}")
        if len(results) > 20:
            print(f"  ... 还有 {len(results) - 20} 个结果")
        return

    # --discipline: 按学科浏览 Munger 模型
    if args.discipline:
        disc = args.discipline
        models = get_by_discipline(disc)
        disc_name = models[0].discipline if models else disc
        print(f"\n{disc_name} — {len(models)} 个模型")
        for m in models:
            gold_mark = " [黄金相关]" if m.gold_applicable else ""
            print(f"  ▸ {m.name_cn} ({m.name_en}){gold_mark}")
            if m.description:
                print(f"     {m.description[:80]}...")
        return

    # --show <id>: 查看详情
    if args.show:
        item_id = args.show
        rule = get_rule_by_id(item_id)
        strategy = get_strategy_by_id(item_id) if not rule else None
        model = get_model_by_id(item_id) if not rule and not strategy else None

        if rule:
            _print_rule_detail(rule)
        elif strategy:
            _print_strategy_detail(strategy)
        elif model:
            _print_model_detail(model)
        else:
            logger.error(f"未找到: {item_id}")
        return

    # --toggle <rule_id>: 启用/禁用规则
    if args.toggle:
        store = DoctrineStore()
        new_state = store.toggle(args.toggle)
        status = "启用" if new_state else "禁用"
        logger.info(f"规则 {args.toggle} 已{status}")
        return

    # --check: 对当前决策运行军规审查
    if args.check:
        _run_doctrine_check(args)
        return

    # 默认: 显示概览
    print("\n投资军规系统 — 概览")
    print(f"  军规: {len(ALL_RULES)}条")
    print(f"  策略: {len(ALL_STRATEGIES)}个")
    print(f"  思维模型: {len(ALL_MODELS)}个")
    print("\n使用 gold-miner doctrine --list 查看全部")
    print("使用 gold-miner doctrine --show <id> 查看详情")
    print("使用 gold-miner doctrine --check 运行军规审查")
    print("使用 gold-miner doctrine --toggle <rule_id> 启用/禁用规则")


def _print_rule_detail(rule) -> None:
    sev_cn = {"block": "阻断 (BLOCK)", "warn": "警告 (WARN)", "info": "提示 (INFO)"}
    cat_cn = {
        "position_sizing": "仓位管理", "timing": "时机选择",
        "emotion": "情绪纪律", "process": "流程纪律",
    }
    print(f"\n{'='*60}")
    print(f"  军规详情: {rule.id}")
    print(f"{'='*60}")
    print(f"  名称: {rule.name}")
    print(f"  级别: {sev_cn.get(rule.severity, rule.severity)}")
    print(f"  类别: {cat_cn.get(rule.category, rule.category)}")
    print(f"  描述: {rule.description}")
    print(f"  状态: {'启用' if rule.enabled else '禁用'}")
    print(f"{'='*60}")


def _print_strategy_detail(strategy) -> None:
    regime_cn = {"trending": "趋势市", "ranging": "震荡市", "crisis": "危机市", "recovery": "复苏市", "all": "通用"}
    print(f"\n{'='*60}")
    print(f"  策略详情: {strategy.id} {strategy.name}")
    print(f"{'='*60}")
    print(f"  适用市场: {regime_cn.get(strategy.applicable_regime, strategy.applicable_regime)}")
    print(f"\n  {strategy.description}")
    if strategy.position_sizing:
        print(f"\n  仓位管理: {strategy.position_sizing}")
    if strategy.entry_rules:
        print("  入场规则:")
        for r in strategy.entry_rules:
            print(f"    - {r}")
    if strategy.exit_rules:
        print("  离场规则:")
        for r in strategy.exit_rules:
            print(f"    - {r}")
    if strategy.stop_loss_rule:
        print(f"  止损: {strategy.stop_loss_rule}")
    if strategy.mental_models:
        print(f"  关联思维模型: {', '.join(strategy.mental_models)}")
    if strategy.pros:
        print(f"  优势: {', '.join(strategy.pros[:3])}")
    if strategy.cons:
        print(f"  劣势: {', '.join(strategy.cons[:3])}")
    print(f"{'='*60}")


def _print_model_detail(model) -> None:
    print(f"\n{'='*60}")
    print(f"  思维模型: {model.id} {model.name}")
    print(f"{'='*60}")
    print(f"\n  {model.description}")
    print(f"\n  核心原则: {model.key_principle}")
    print(f"  适用场景: {model.when_to_apply}")
    if model.gold_application:
        print(f"  黄金应用: {model.gold_application}")
    if model.related_strategies:
        print(f"  关联策略: {', '.join(model.related_strategies)}")
    if model.reference:
        print(f"  参考来源: {model.reference}")
    print(f"{'='*60}")


def _run_doctrine_check(args: argparse.Namespace) -> None:
    """独立运行军规审查（基于模拟上下文）."""
    # 构造模拟决策和上下文
    direction = args.direction or random.choice(["long", "short", "neutral"])
    position = args.price or random.uniform(0.05, 0.5) if args.price else random.uniform(0.05, 0.5)

    decision = {
        "direction": direction,
        "position_pct": round(position, 2),
        "signal_type": "中等信号" if position > 0.2 else "弱信号",
    }

    active_dims = args.dims.split(",") if args.dims else ["technical", "fundamental"]
    context = {
        "current_exposure": 0.3,
        "gold_allocation_pct": 0.35,
        "daily_change_pct": args.change or 1.5,
        "near_data_event": args.data_event or False,
        "consecutive_stops": 0,
        "vix": 18.5,
        "fear_greed_index": 55,
        "unrealized_pnl_pct": 0.12,
        "has_trailing_stop": True,
        "bullish_signal_count": 5 if direction == "long" else 2,
        "bearish_signal_count": 2 if direction == "long" else 5,
        "active_dimensions": active_dims,
        "bull_confidence": 0.65 if direction == "long" else 0.35,
        "bear_confidence": 0.35 if direction == "long" else 0.65,
        "stop_loss_set": True,
        "has_decision_record": True,
    }

    _print_and_apply_doctrine(decision, context)


def _print_and_apply_doctrine(decision: dict, context: dict) -> dict:
    """运行军规审查并打印结果."""
    checker = DoctrineChecker()
    result = checker.check(decision, context)
    adjusted = checker.apply_doctrine(decision, result)

    print(f"\n{'='*60}")
    print("  投资军规审查")
    print(f"{'='*60}")
    print(f"  决策: 方向={decision.get('direction', '?')} | 仓位={decision.get('position_pct', 0):.0%}")
    print(f"  通过: {result.passed_count}/{len(result.violations)}")

    if result.blocks:
        print(f"\n  ■ 阻断 ({len(result.blocks)}项):")
        for v in result.blocks:
            print(f"    ✗  {v.rule.name}: {v.message}")

    if result.warnings:
        print(f"\n  ◆ 警告 ({len(result.warnings)}项):")
        for v in result.warnings:
            print(f"    !  {v.rule.name}: {v.message}")

    if result.infos:
        print(f"\n  ○ 提示 ({len(result.infos)}项):")
        for v in result.infos:
            print(f"    i  {v.rule.name}: {v.message}")

    if result.all_passed:
        print("\n  ✅ 全部军规通过")

    if adjusted.get("doctrine_override"):
        print(f"\n  ⚡ 军规调整: {adjusted['doctrine_override']}")
        print(f"     调整后仓位: {adjusted.get('position_pct', 0):.0%}")

    print(f"{'='*60}")
    return adjusted
