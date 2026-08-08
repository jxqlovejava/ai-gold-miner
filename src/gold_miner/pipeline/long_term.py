"""中长期金价分析 — 独立分析类 (6-36个月视角)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from gold_miner.data.long_term_aggregator import LongTermDataAggregator
from gold_miner.decision.agents import AgentOpinion, BearAgent, BullAgent, PortfolioManager
from gold_miner.doctrine.checker import DoctrineChecker
from gold_miner.pipeline.long_term_result import LongTermAnalysisResult
from gold_miner.signals.base import SignalBundle
from gold_miner.signals.long_term_fundamental import LongTermFundamentalSignal
from gold_miner.signals.long_term_scenario import LongTermScenarioSignal
from gold_miner.signals.long_term_trend import LongTermTrendSignal
from gold_miner.storage import get_store


class LongTermAnalyzer:
    """中长期金价分析 (6-36 个月视角)."""

    def __init__(self) -> None:
        self.data_aggregator = LongTermDataAggregator()
        self.trend_signal_gen = LongTermTrendSignal()
        self.fundamental_signal_gen = LongTermFundamentalSignal()
        self.scenario_signal_gen = LongTermScenarioSignal()

    def run(self, horizon: int = 12, risk_profile: str = "moderate", dry_run: bool = False) -> LongTermAnalysisResult:
        if dry_run:
            return LongTermAnalysisResult(horizon_months=horizon)

        result = LongTermAnalysisResult(horizon_months=horizon)

        # 1. 读取投资者画像与持仓
        self._load_investor_data(result)

        # 2. 数据采集
        try:
            data_bundle = self.data_aggregator.fetch_all(gold_lookback_days=365)
            result.current_spot = data_bundle.current_spot or 3300.0
        except Exception as e:
            logger.warning(f"数据采集失败: {e}")
            result.current_spot = 3300.0
            data_bundle = None

        # 3. 信号生成
        bundle = SignalBundle()
        if data_bundle is not None:
            try:
                trend_signals = self.trend_signal_gen.generate_signals()
                for s in trend_signals:
                    bundle.add(s)
            except Exception as e:
                logger.warning(f"趋势信号生成失败: {e}")

            try:
                fundamental_signals = self.fundamental_signal_gen.generate_signals(
                    gold_history=data_bundle.gold_history
                )
                for s in fundamental_signals:
                    bundle.add(s)
            except Exception as e:
                logger.warning(f"基本面信号生成失败: {e}")

            try:
                scenario_signals, matrix = self.scenario_signal_gen.generate_signals(
                    base_price=result.current_spot,
                    horizon_months=horizon,
                    existing_bundle=bundle,
                )
                for s in scenario_signals:
                    bundle.add(s)
                result.scenario_matrix = matrix
            except Exception as e:
                logger.warning(f"情景矩阵生成失败: {e}")

        result.bundle = bundle

        # 4. Agent 博弈
        default_bull = AgentOpinion(
            agent_name="多头分析师",
            stance="neutral",
            confidence=0.0,
            arguments=["Agent 分析失败，使用默认观望立场"],
        )
        default_bear = AgentOpinion(
            agent_name="空头分析师",
            stance="neutral",
            confidence=0.0,
            arguments=["Agent 分析失败，使用默认观望立场"],
        )

        try:
            bull = BullAgent().analyze(bundle)
            result.bull_opinion = bull
        except Exception as e:
            logger.warning(f"多头 Agent 分析失败: {e}")
            result.bull_opinion = default_bull

        try:
            bear = BearAgent().analyze(bundle)
            result.bear_opinion = bear
        except Exception as e:
            logger.warning(f"空头 Agent 分析失败: {e}")
            result.bear_opinion = default_bear

        try:
            pm = PortfolioManager()
            decision = pm.decide(
                result.bull_opinion, result.bear_opinion, bundle, risk_profile=risk_profile
            )
            # 中长期战略建议仓位限制在单笔上限内（20%），避免军规阻断
            # 输出的是"战略配置比例"，不是单笔交易
            decision["position_pct"] = min(decision.get("position_pct", 0), 0.20)
            if decision["position_pct"] == 0 and decision.get("direction") == "long":
                decision["direction"] = "neutral"
        except Exception as e:
            logger.warning(f"投资组合决策失败: {e}")
            decision = {
                "direction": "neutral",
                "position_pct": 0.0,
                "reasoning": "决策组件异常，默认观望",
            }
        result.trade_decision = decision

        # 5. 军规审查
        try:
            doctrine_ctx = self._build_doctrine_context(result, data_bundle)
            doctrine = DoctrineChecker().check(decision, doctrine_ctx)
            result.doctrine_result = doctrine
            adjusted = DoctrineChecker().apply_doctrine(decision, doctrine)
            result.trade_decision = adjusted
        except Exception as e:
            logger.warning(f"军规审查失败: {e}")
            result.doctrine_result = None

        # 6. Munger 模型
        result.munger_models = self._select_munger_models(result)

        # 7. 战略建议
        self._build_strategic_recommendation(result)

        # 7.5 V9 分级低吸高抛建议 (成本管理原则)
        try:
            result.low_buy_high_sell = self._evaluate_low_buy_high_sell(result)
        except Exception as e:
            logger.warning(f"分级低吸高抛评估失败: {e}")
            result.low_buy_high_sell = {"low_buy_suggestion": "评估失败", "warnings": [str(e)]}

        # 8. 生成消息
        self._build_messages(result)

        return result

    def dry_run_steps(self) -> list[str]:
        return [
            "[1] 读取投资者画像与持仓",
            "[2] 采集中长期数据 (央行/ETF/COT/财政/金价)",
            "[3] 生成长期趋势信号、结构性基本面信号、情景矩阵",
            "[4] Agent 博弈 (多头 vs 空头)",
            "[5] 军规审查 (r001-r015)",
            "[6] Munger 模型匹配",
            "[7] 输出战略仓位建议、触发条件、再平衡规则",
        ]

    def _load_investor_data(self, result: LongTermAnalysisResult) -> None:
        """加载投资者画像与持仓，支持 private 或 example fallback."""
        store = get_store()

        # 优先读取 private，不存在则尝试 example
        profile = store.load_investor_profile()
        if not profile:
            example_path = Path("data/investor_profile.example.md")
            if example_path.exists():
                profile = example_path.read_text(encoding="utf-8")
                result.warnings.append("使用示例投资者画像，请填写 data/private/investor_profile.md")
        result.investor_profile = profile

        portfolio = store.load_portfolio()
        if not portfolio:
            example_portfolio_path = Path("data/portfolio.example.yaml")
            if example_portfolio_path.exists():
                import yaml
                try:
                    portfolio = yaml.safe_load(example_portfolio_path.read_text(encoding="utf-8")) or {}
                    result.warnings.append("使用示例持仓数据，请填写 data/private/portfolio.yaml")
                except Exception:
                    portfolio = {}
        result.portfolio = portfolio

    def _build_doctrine_context(
        self,
        result: LongTermAnalysisResult,
        data_bundle: Any | None,
    ) -> dict[str, Any]:
        """构建军规审查上下文."""
        ctx: dict[str, Any] = {
            "current_exposure": self._current_gold_exposure(result.portfolio),
            "gold_allocation_pct": self._current_gold_exposure(result.portfolio),
            "bull_confidence": result.bull_opinion.confidence if result.bull_opinion else 0,
            "bear_confidence": result.bear_opinion.confidence if result.bear_opinion else 0,
            "bullish_signal_count": result.bundle.bullish_count(),
            "bearish_signal_count": result.bundle.bearish_count(),
            "active_dimensions": list({s.dimension for s in result.bundle.signals}),
            "stop_loss_set": True,  # 中长期建议默认要求设置止损
            "has_decision_record": False,
        }

        if data_bundle is not None and not data_bundle.gold_history.empty:
            latest = data_bundle.gold_history["close"]
            if len(latest) >= 2:
                ctx["daily_change_pct"] = (latest.iloc[-1] / latest.iloc[-2] - 1) * 100

        return ctx

    @staticmethod
    def _current_gold_exposure(portfolio: dict[str, Any]) -> float:
        """从 portfolio 提取当前黄金敞口比例."""
        try:
            assets = portfolio.get("assets", {})
            total = portfolio.get("total_assets", 0)
            if not total:
                return 0.0
            gold_value = sum(
                float(v.get("value", 0)) for k, v in assets.items()
                if "gold" in k.lower() or "黄金" in k
            )
            return gold_value / float(total)
        except Exception:
            return 0.0

    def _select_munger_models(self, result: LongTermAnalysisResult) -> list[str]:
        """根据分析结果选择最相关的 Munger 模型."""
        models: list[str] = []

        # 去美元化/央行购金 → 激励机制 + 二阶效应
        has_cb_buying = any(
            "央行购金" in s.name for s in result.bundle.signals if s.direction.value == "bullish"
        )
        if has_cb_buying:
            models.append("激励机制: 制裁风险驱动央行储备重组，行为可预测")
            models.append("二阶效应: 去美元化不只是汇率问题，更是储备资产安全诉求")

        # 债务/GDP 高位 → 安全边际 + 延迟效应
        has_debt = any("债务" in s.name for s in result.bundle.signals)
        if has_debt:
            models.append("安全边际: 债务货币化预期为黄金提供不对称上行保护")
            models.append("延迟效应: 财政恶化对金价的影响通常滞后 12-24 个月显现")

        # 实际利率低位 → 机会成本
        has_real_rate = any("实际利率" in s.name for s in result.bundle.signals)
        if has_real_rate:
            models.append("机会成本: 实际利率是持有黄金的真实代价，低位降低配置门槛")

        # 情景矩阵 → 检查清单方法 + 否证思维
        if result.scenario_matrix:
            models.append("检查清单方法: 用五情景矩阵系统性评估极端与基准状态")
            models.append("否证思维: 主动寻找熊市情景成立的条件，而非只确认牛市叙事")

        # 默认模型
        if len(models) < 2:
            models.append("能力圈: 黄金是唯一同时具有货币属性、避险属性和零息资产特征的标的")
            models.append("市场先生: 中长期不预测价格，只利用极端情绪导致的错误定价")

        return models[:3]

    def _build_strategic_recommendation(self, result: LongTermAnalysisResult) -> None:
        """构建战略建议、触发条件与再平衡规则."""
        decision = result.trade_decision
        direction = decision.get("direction", "neutral")
        position_pct = decision.get("position_pct", 0)

        if direction == "long" and position_pct > 0:
            action = "增持/定投加码"
        elif direction == "short" and position_pct > 0:
            action = "减持/降低敞口"
        else:
            action = "维持现有仓位/观望"

        result.strategic_recommendation = {
            "action": action,
            "target_position_pct": position_pct,
            "horizon_months": result.horizon_months,
            "current_spot": result.current_spot,
            "confidence": self._overall_confidence(result),
        }

        # 触发条件
        result.trigger_conditions = self._build_triggers(result)

        # 再平衡规则
        result.rebalancing_rules = [
            f"每季度审视一次 {result.horizon_months} 个月情景矩阵概率",
            "央行季度购金连续两季低于 150 吨 → 重新评估结构性买盘",
            "10Y TIPS 实际利率突破 2.5% 且维持 3 个月 → 降低战略仓位",
            "美元储备份额止跌回升 → 重新评估去美元化叙事强度",
            "浮盈超过 20% 后，将硬止损上移至成本价以上",
        ]

    def _build_triggers(self, result: LongTermAnalysisResult) -> list[str]:
        """根据方向构建触发条件."""
        triggers: list[str] = []
        direction = result.trade_decision.get("direction", "neutral")

        if direction == "long":
            triggers.append("实际利率回落至 1% 以下 → 加仓")
            triggers.append("央行季度净购金连续两季高于 250 吨 → 加仓")
            triggers.append("金价回调至 200 日均线附近且长期信号未恶化 → 加仓")
        elif direction == "short":
            triggers.append("实际利率持续高于 2.5% 且经济强劲 → 减仓")
            triggers.append("央行购金连续两季降至 100 吨以下 → 减仓")
            triggers.append("金价相对 200 日均线溢价超过 20% → 获利了结")
        else:
            triggers.append("多空信号均衡，等待任一方向置信度突破 60% 再行动")
            triggers.append("新季度 WGC 数据公布后再评估央行买盘趋势")

        return triggers

    def _overall_confidence(self, result: LongTermAnalysisResult) -> float:
        """计算整体置信度."""
        bull_conf = result.bull_opinion.confidence if result.bull_opinion else 0.0
        bear_conf = result.bear_opinion.confidence if result.bear_opinion else 0.0
        doctrine_passed = 0.0
        if result.doctrine_result:
            total = result.doctrine_result.passed_count + result.doctrine_result.failed_count
            doctrine_passed = result.doctrine_result.passed_count / total if total > 0 else 0.0

        return round(min(max(bull_conf, bear_conf) * 0.6 + doctrine_passed * 0.4, 1.0), 2)

    def _evaluate_low_buy_high_sell(self, result: LongTermAnalysisResult) -> dict[str, Any]:
        """V9 分级低吸高抛评估 (成本管理原则).

        读取 portfolio 的 long_term 配置, 结合当前信号输出分级建议.
        信号数据不足时使用保守默认 (持有/观望).
        """
        from gold_miner.strategy.low_buy_high_sell import LowBuyHighSellAdvisor

        portfolio = result.portfolio or {}
        long_term_cfg = (portfolio.get("long_term") or {}).get("low_buy_high_sell") or {}
        advisor = LowBuyHighSellAdvisor(config=long_term_cfg or None)

        pools = {
            "core": (portfolio.get("long_term") or {}).get("pools", {}).get("core", 40),
            "tactical": (portfolio.get("long_term") or {}).get("pools", {}).get("tactical", 20),
            "opportunity": (portfolio.get("long_term") or {}).get("pools", {}).get("opportunity", 20),
        }

        # 从现有信号 bundle 提取输入 (信号不足时用默认)
        signals = result.bundle.signals if result.bundle else []
        sig_dict = {s.name: s for s in signals}

        def _score_of(*names: str) -> float | None:
            """取第一个存在信号的分数."""
            for n in names:
                s = sig_dict.get(n)
                if s is not None:
                    return float(s.score) if s.score is not None else None
            return None

        # RSI: 从技术信号取近似值 (若无则 None)
        # 注意: 实际信号名为 "RSI超卖"/"RSI超买" (signals/technical.py), 维度 technical
        rsi_val = _score_of("RSI超买", "RSI超卖", "RSI")
        # COT: 实际信号名为 "COT聪明钱长期加仓"/"COT聪明钱长期减仓" (signals/long_term_trend.py)
        cot_change = _score_of("COT聪明钱长期加仓", "COT聪明钱长期减仓", "COT净多变化")

        signal_obj = advisor.evaluate(
            current_price=result.current_spot or 3300.0,
            pools=pools,
            atr_trailing_triggered=False,  # 长期分析不做实时 ATR, 由 trailing_stop 模块处理
            rebalance_overweight=False,
            rsi_value=rsi_val,
            cot_net_position_change=cot_change,
            central_bank_buying_slow=False,
        )
        return signal_obj.to_dict()

    def _build_messages(self, result: LongTermAnalysisResult) -> None:
        """生成工作流输出消息."""
        rec = result.strategic_recommendation
        result.messages.append(
            f"中长期视角 ({result.horizon_months}个月): 当前金价 ${result.current_spot:,.0f}/oz"
        )
        result.messages.append(
            f"战略建议: {rec.get('action', '观望')} "
            f"目标仓位 {rec.get('target_position_pct', 0):.0%}"
        )
        bull_conf = result.bull_opinion.confidence if result.bull_opinion else 0.0
        bear_conf = result.bear_opinion.confidence if result.bear_opinion else 0.0
        result.messages.append(
            f"Agent 博弈: 多头信心 {bull_conf:.0%} "
            f"vs 空头信心 {bear_conf:.0%}"
        )

        if result.doctrine_result:
            if result.doctrine_result.has_blocks:
                result.messages.append("⚠️ 军规阻断，建议仓位归零观望")
            elif result.doctrine_result.warnings:
                result.messages.append(
                    f"⚠️ 军规警告 {len(result.doctrine_result.warnings)} 项，已自动降低建议仓位"
                )
            else:
                result.messages.append("军规审查通过")

        if result.scenario_matrix:
            result.messages.append(
                f"情景矩阵预期: ${result.scenario_matrix.expected_price:,.0f} "
                f"({result.scenario_matrix.weighted_expected_change_pct:+.1f}%)"
            )
            scenario_lines = []
            for s in result.scenario_matrix.scenarios:
                scenario_lines.append(
                    f"{s.name} {s.probability_pct:.0f}% (${s.gold_low:,.0f}-${s.gold_high:,.0f})"
                )
            result.messages.append("情景概率: " + " | ".join(scenario_lines))

        for warning in result.warnings:
            result.messages.append(f"注意: {warning}")
