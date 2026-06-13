"""决策指令系统 — 将多维度信号转化为具体行动指令.

核心逻辑:
  1. 采集全部信号 → SignalBundle
  2. 多因子打分 → composite_score
  3. 策略引擎选择最优策略 → StrategyDecision
  4. 军规审查 → 风控约束
  5. 输出: ActionInstruction (操作/仓位/价位/止损/理由)

使用方式:
    guide = ActionGuide()
    instruction = guide.generate(current_position_pct=0.3, avg_cost=2300)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from gold_miner.advisor.core import (
    ActionInstruction,
    ActionType,
    AdvisorReport,
    PositionSize,
)
from gold_miner.data.macro import MacroDataFetcher
from gold_miner.data.news import NewsFetcher
from gold_miner.data.sentiment import SentimentDataFetcher
from gold_miner.data.spot_gold import SpotGoldFetcher
from gold_miner.decision.agents import BearAgent, BullAgent, PortfolioManager
from gold_miner.decision.risk import RiskManager
from gold_miner.doctrine.checker import DoctrineChecker
from gold_miner.doctrine.models import DoctrineResult
from gold_miner.signals.base import SignalBundle
from gold_miner.signals.engine import ScoringEngine
from gold_miner.signals.etf_flow_signal import EtfFlowSignalGenerator
from gold_miner.signals.fundamental import FundamentalAnalyzer
from gold_miner.signals.news_signal import NewsSignalGenerator
from gold_miner.signals.sentiment_signal import SentimentAnalyzer
from gold_miner.signals.technical import TechnicalAnalyzer
from gold_miner.strategy.engine import MultiObjectiveEngine
from gold_miner.strategy.objectives import StrategyDecision, StrategyObjective


@dataclass
class MarketSnapshot:
    """市场快照 — 决策所需全部数据."""
    gold_price: float
    dxy: float | None
    real_rate: float | None
    silver: float | None
    gold_df: Any = None
    dxy_df: Any = None
    rate_df: Any = None
    silver_df: Any = None
    breakeven_df: Any = None


class ActionGuide:
    """行动指令生成器.

    整合了信号采集、打分、策略选择、军规审查的完整决策链.
    """

    def __init__(self) -> None:
        self.scoring = ScoringEngine()
        self.strategy_engine = MultiObjectiveEngine()
        self.doctrine = DoctrineChecker()
        self.risk = RiskManager()
        self.bull = BullAgent()
        self.bear = BearAgent()
        self.pm = PortfolioManager()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def generate(
        self,
        current_position_pct: float = 0.0,
        avg_cost: float = 0.0,
        strategy_preference: StrategyObjective | None = None,
        with_news: bool = True,
        with_sentiment: bool = True,
    ) -> AdvisorReport:
        """生成今日行动指令.

        Args:
            current_position_pct: 当前仓位比例 0~1
            avg_cost: 持仓均价
            strategy_preference: 策略偏好，None=自动选择
            with_news: 是否包含新闻信号
            with_sentiment: 是否包含情绪信号

        Returns:
            AdvisorReport，report_type="action_guide"
        """
        logger.info("[ActionGuide] 开始生成行动指令...")

        # 1. 采集市场数据
        snapshot = self._fetch_data()
        logger.info(f"[ActionGuide] 金价: ${snapshot.gold_price:.2f}")

        # 2. 生成信号
        bundle = self._build_signals(snapshot, with_news, with_sentiment)
        logger.info(f"[ActionGuide] 信号总数: {len(bundle.signals)}")

        # 3. 多因子打分
        bundle = self.scoring.score(bundle)
        logger.info(f"[ActionGuide] 综合评分: {bundle.composite_score:+.3f}, 置信度: {bundle.confidence:.1%}")

        # 4. Agent 辩论
        opinions = self._agent_debate(bundle)
        logger.info(f"[ActionGuide] Agent 辩论完成")

        # 5. 策略决策
        strategy_decision = self._select_strategy(
            bundle, snapshot, current_position_pct, avg_cost, strategy_preference
        )

        # 6. 军规审查
        decision_dict = self._strategy_to_dict(strategy_decision, snapshot)
        doctrine_result = self.doctrine.check(
            decision_dict, context={"current_position_pct": current_position_pct}
        )

        # 7. 风控审查
        risk_checks = self.risk.check(
            decision=decision_dict,
            current_position_pct=current_position_pct,
        )

        # 8. 组装指令
        instruction = self._build_instruction(
            strategy_decision,
            bundle,
            doctrine_result,
            risk_checks,
            snapshot,
            current_position_pct,
        )

        # 9. 构建报告
        warnings = []
        for v in doctrine_result.violations:
            if v.rule.severity == "block" and not v.passed:
                warnings.append(f"🚫 军规拦截: {v.message}")
            elif v.rule.severity == "warn" and not v.passed:
                warnings.append(f"⚠️ 军规提醒: {v.message}")

        for check in risk_checks:
            if not check.passed:
                warnings.append(f"🔒 风控: {check.message}")

        return AdvisorReport(
            report_type="action_guide",
            instruction=instruction,
            confidence=bundle.confidence,
            sources=["SpotGold", "DXY", "RealRate", "Technical", "Fundamental"]
            + (["News"] if with_news else [])
            + (["Sentiment"] if with_sentiment else []),
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _fetch_data(self) -> MarketSnapshot:
        """采集市场数据."""
        gold_fetcher = SpotGoldFetcher()
        gold_df = gold_fetcher.fetch_recent(days=90)
        gold_price = float(gold_df["close"].iloc[-1]) if not gold_df.empty else 0.0

        macro = MacroDataFetcher()
        dxy_df = macro.fetch_dxy()
        rate_df = macro.fetch_real_rate()
        silver_df = macro.fetch_silver()
        breakeven_df = macro.fetch_breakeven()

        return MarketSnapshot(
            gold_price=gold_price,
            dxy=float(dxy_df["value"].iloc[-1]) if not dxy_df.empty else None,
            real_rate=float(rate_df["value"].iloc[-1]) if not rate_df.empty else None,
            silver=float(silver_df["value"].iloc[-1]) if not silver_df.empty else None,
            gold_df=gold_df,
            dxy_df=dxy_df,
            rate_df=rate_df,
            silver_df=silver_df,
            breakeven_df=breakeven_df,
        )

    def _build_signals(
        self,
        snapshot: MarketSnapshot,
        with_news: bool,
        with_sentiment: bool,
    ) -> SignalBundle:
        """构建完整信号包."""
        bundle = SignalBundle()

        # 技术面
        if snapshot.gold_df is not None and not snapshot.gold_df.empty:
            tech = TechnicalAnalyzer(snapshot.gold_df)
            for sig in tech.generate_signals():
                bundle.add(sig)

        # 基本面
        fundamental = FundamentalAnalyzer(
            gold_df=snapshot.gold_df,
            dxy_df=snapshot.dxy_df,
            rate_df=snapshot.rate_df,
            silver_df=snapshot.silver_df,
            breakeven_df=snapshot.breakeven_df,
        )
        for sig in fundamental.generate_signals():
            bundle.add(sig)

        # 消息面
        if with_news:
            try:
                news_gen = NewsSignalGenerator()
                for sig in news_gen.fetch_and_analyze(hours=24):
                    bundle.add(sig)
            except Exception as e:
                logger.warning(f"新闻信号获取失败: {e}")

        # 情绪面
        if with_sentiment:
            try:
                sent_fetcher = SentimentDataFetcher()
                au_df = sent_fetcher.fetch_au_futures(lookback=60)
                if not au_df.empty:
                    sent_analyzer = SentimentAnalyzer(au_df=au_df)
                    for sig in sent_analyzer.generate_signals():
                        bundle.add(sig)
            except Exception as e:
                logger.warning(f"情绪面数据获取失败: {e}")

        # ETF 资金流
        try:
            etf_gen = EtfFlowSignalGenerator()
            for sig in etf_gen.generate_signals():
                bundle.add(sig)
        except Exception as e:
            logger.debug(f"ETF 信号异常: {e}")

        return bundle

    def _agent_debate(self, bundle: SignalBundle) -> list[Any]:
        """Agent 辩论 — 收集多空观点."""
        opinions = []
        try:
            opinions.append(self.bull.analyze(bundle))
        except Exception as e:
            logger.debug(f"多头Agent异常: {e}")
        try:
            opinions.append(self.bear.analyze(bundle))
        except Exception as e:
            logger.debug(f"空头Agent异常: {e}")
        return opinions

    def _select_strategy(
        self,
        bundle: SignalBundle,
        snapshot: MarketSnapshot,
        current_position_pct: float,
        avg_cost: float,
        preference: StrategyObjective | None,
    ) -> StrategyDecision:
        """选择最优策略并生成决策."""
        # 计算 ATR（简化版）
        atr = self._estimate_atr(snapshot.gold_df)

        # 方向判断
        direction = self._determine_direction(bundle)

        # 计算当前组合收益（简化）
        portfolio_return = 0.0
        if current_position_pct > 0 and avg_cost > 0:
            portfolio_return = (snapshot.gold_price - avg_cost) / avg_cost

        # 策略引擎评估
        comparison = self.strategy_engine.evaluate(
            bundle=bundle,
            direction=direction,
            entry_price=snapshot.gold_price,
            atr=atr,
            portfolio_return=portfolio_return,
            volatility=atr / snapshot.gold_price if snapshot.gold_price > 0 else 0.02,
            dxy_correlation=-0.6,  # 简化: 黄金与美元典型负相关
        )

        # 如果用户有策略偏好，使用偏好策略
        if preference:
            decision = comparison.results.get(preference.value)
            if decision:
                return decision

        # 否则使用推荐策略
        recommended = comparison.recommended
        if recommended and comparison.results.get(recommended.value):
            return comparison.results[recommended.value]

        # fallback: 取第一个有仓位的
        for obj in self.strategy_engine.STRATEGY_PRIORITY:
            dec = comparison.results.get(obj.value)
            if dec and dec.position_pct > 0:
                return dec

        # 最 fallback: 均衡策略
        return comparison.results.get("balanced") or list(comparison.results.values())[0]

    @staticmethod
    def _determine_direction(bundle: SignalBundle) -> str:
        """根据信号包判断方向."""
        if bundle.composite_score > 0.2:
            return "long"
        if bundle.composite_score < -0.2:
            return "short"
        return "neutral"

    @staticmethod
    def _estimate_atr(gold_df: Any) -> float:
        """估算 ATR（平均真实波幅）."""
        if gold_df is None or gold_df.empty or len(gold_df) < 14:
            return 30.0  # 默认 $30
        try:
            high = gold_df["high"].iloc[-14:]
            low = gold_df["low"].iloc[-14:]
            close = gold_df["close"].iloc[-15:-1]
            tr1 = high - low
            tr2 = (high - close.shift(1)).abs()
            tr3 = (low - close.shift(1)).abs()
            tr = tr1.combine(tr2, max).combine(tr3, max)
            return float(tr.mean())
        except Exception:
            return 30.0

    @staticmethod
    def _strategy_to_dict(decision: StrategyDecision, snapshot: MarketSnapshot) -> dict[str, Any]:
        """将 StrategyDecision 转为 dict 供军规审查."""
        return {
            "action": decision.direction,
            "position_pct": decision.position_pct,
            "stop_loss": decision.stop_loss,
            "take_profit": decision.take_profit_levels[0] if decision.take_profit_levels else None,
            "gold_price": snapshot.gold_price,
        }

    def _build_instruction(
        self,
        decision: StrategyDecision,
        bundle: SignalBundle,
        doctrine: DoctrineResult,
        risk_checks: list[Any],
        snapshot: MarketSnapshot,
        current_position: float,
    ) -> ActionInstruction:
        """将策略决策转化为用户友好的行动指令."""
        # 映射方向到操作类型
        action_map = {
            "long": ActionType.BUY if current_position == 0 else ActionType.ADD,
            "short": ActionType.SELL if current_position > 0 else ActionType.HEDGE,
            "neutral": ActionType.HOLD,
        }
        action = action_map.get(decision.direction, ActionType.HOLD)

        # 仓位映射
        position = self._pct_to_position_size(decision.position_pct)

        # 构建理由
        reason = self._build_reason(decision, bundle)

        # 风险说明
        risk_note = self._build_risk_note(doctrine, risk_checks)

        # 军规引用
        doctrine_refs = [
            v.rule.name for v in doctrine.violations
            if v.rule.severity in ("block", "warn") and not v.passed
        ]

        return ActionInstruction(
            action=action,
            position_size=position,
            target_pct=round(decision.position_pct, 2),
            entry_price=snapshot.gold_price if action in (ActionType.BUY, ActionType.ADD) else None,
            stop_loss=round(decision.stop_loss, 2) if decision.stop_loss > 0 else None,
            take_profit=round(decision.take_profit_levels[0], 2) if decision.take_profit_levels else None,
            urgency="high" if abs(bundle.composite_score) > 0.5 else "normal",
            reason=reason,
            risk_note=risk_note,
            doctrine_refs=doctrine_refs,
        )

    @staticmethod
    def _pct_to_position_size(pct: float) -> PositionSize:
        if pct >= 0.7:
            return PositionSize.FULL
        if pct >= 0.5:
            return PositionSize.HEAVY
        if pct >= 0.3:
            return PositionSize.MODERATE
        if pct >= 0.1:
            return PositionSize.LIGHT
        return PositionSize.EMPTY

    @staticmethod
    def _build_reason(decision: StrategyDecision, bundle: SignalBundle) -> str:
        """构建操作理由."""
        parts: list[str] = []
        parts.append(f"策略: {decision.objective.value}")
        parts.append(f"综合评分: {bundle.composite_score:+.2f} (置信度 {bundle.confidence:.0%})")

        # 信号方向统计
        bull = bundle.bullish_count()
        bear = bundle.bearish_count()
        if bull + bear > 0:
            parts.append(f"信号分布: {bull}多/{bear}空")

        if decision.reason:
            parts.append(decision.reason)

        return " | ".join(parts)

    @staticmethod
    def _build_risk_note(doctrine: DoctrineResult, risk_checks: list[Any]) -> str:
        """构建风险提示."""
        notes: list[str] = []

        for v in doctrine.violations:
            if v.rule.severity == "block" and not v.passed:
                notes.append(f"【拦截】{v.message}")
            elif v.rule.severity == "warn" and not v.passed:
                notes.append(f"【提醒】{v.message}")

        for check in risk_checks:
            if not check.passed:
                notes.append(f"【风控】{check.message}")

        return "; ".join(notes) if notes else "无显著风险"
