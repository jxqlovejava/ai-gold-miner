"""信号管线 — 统一协调各维度信号生成顺序."""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from loguru import logger

from gold_miner.data.calendar import CalendarEvent, EventCalendar
from gold_miner.data.news import NewsItem
from gold_miner.signals.base import Signal, SignalBundle


@dataclass
class PipelineStep:
    name: str
    generator: Callable[..., list[Signal]]
    depends_on: list[str] = field(default_factory=list)
    weight_override: float | None = None
    enabled: bool = True


@dataclass
class PipelineContext:
    gold_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    dxy_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    rate_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    breakeven_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    silver_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    au_df: pd.DataFrame | None = None
    news_items: list[NewsItem] = field(default_factory=list)
    calendar: EventCalendar = field(default_factory=EventCalendar)
    metadata: dict[str, Any] = field(default_factory=dict)


class SignalPipeline:
    """信号管线 — 按拓扑顺序执行信号生成步骤."""

    def __init__(self) -> None:
        self._steps: dict[str, PipelineStep] = {}
        self._setup_default_steps()

    def _setup_default_steps(self) -> None:
        # 占位步骤 — 实际 generator 在 execute 时注入
        from gold_miner.signals.cot_signal import CotSignalGenerator
        from gold_miner.signals.economic_calendar import EconomicCalendarSignalGenerator
        from gold_miner.signals.etf_flow_signal import EtfFlowSignalGenerator
        from gold_miner.signals.event_driven import EventDrivenSignalGenerator
        from gold_miner.signals.fundamental import FundamentalAnalyzer
        from gold_miner.signals.hype_bias_signal import HypeBiasSignalGenerator
        from gold_miner.signals.institutional_signal import InstitutionalSignalGenerator
        from gold_miner.signals.news_signal import NewsSignalGenerator
        from gold_miner.signals.polymarket_signal import PolymarketSignalGenerator
        from gold_miner.signals.recent_events import RecentEventSignalGenerator
        from gold_miner.signals.sentiment_signal import SentimentAnalyzer
        from gold_miner.signals.technical import TechnicalAnalyzer

        self.register(PipelineStep(
            name="event_pre",
            generator=lambda ctx: EventDrivenSignalGenerator(ctx.calendar).generate_pre_event_signals(),
            depends_on=[],
        ))

        self.register(PipelineStep(
            name="economic_calendar",
            generator=lambda _ctx: EconomicCalendarSignalGenerator().generate_signals(),
            depends_on=[],
        ))

        self.register(PipelineStep(
            name="recent_events",
            generator=lambda ctx: RecentEventSignalGenerator(
                calendar=ctx.calendar,
            ).generate_signals(),
            depends_on=[],
        ))

        self.register(PipelineStep(
            name="technical",
            generator=lambda ctx: TechnicalAnalyzer(ctx.gold_df).generate_signals(),
            depends_on=[],
        ))

        self.register(PipelineStep(
            name="fundamental",
            generator=lambda ctx: FundamentalAnalyzer(
                gold_df=ctx.gold_df, dxy_df=ctx.dxy_df,
                rate_df=ctx.rate_df, breakeven_df=ctx.breakeven_df,
                silver_df=ctx.silver_df,
            ).generate_signals(),
            depends_on=[],
        ))

        self.register(PipelineStep(
            name="news",
            generator=lambda ctx: NewsSignalGenerator().analyze(ctx.news_items),
            depends_on=[],
        ))

        self.register(PipelineStep(
            name="sentiment",
            generator=lambda ctx: SentimentAnalyzer(ctx.au_df).generate_signals(),
            depends_on=[],
        ))

        self.register(PipelineStep(
            name="etf_flow",
            generator=lambda _ctx: EtfFlowSignalGenerator().generate_signals(),
            depends_on=[],
        ))

        self.register(PipelineStep(
            name="cot",
            generator=lambda _ctx: CotSignalGenerator().generate_signals(),
            depends_on=[],
        ))

        self.register(PipelineStep(
            name="smart_money",
            generator=lambda ctx: InstitutionalSignalGenerator(
                current_spot=ctx.gold_df["close"].iloc[-1] if not ctx.gold_df.empty else 3300
            ).generate_signals(),
            depends_on=[],
        ))

        self.register(PipelineStep(
            name="hype_bias",
            generator=lambda ctx: HypeBiasSignalGenerator(
                news_items=ctx.news_items,
                current_spot=ctx.gold_df["close"].iloc[-1] if not ctx.gold_df.empty else 0.0,
            ).generate_signals(),
            depends_on=["news", "smart_money"],
        ))

        self.register(PipelineStep(
            name="event_post",
            generator=lambda ctx: EventDrivenSignalGenerator(
                ctx.calendar
            ).generate_post_event_signals_from_calendar(lookback_days=7),
            depends_on=[],
        ))

        self.register(PipelineStep(
            name="polymarket",
            generator=lambda ctx: PolymarketSignalGenerator().generate(
                ctx.metadata.get("polymarket_markets", [])
            ),
            depends_on=[],
        ))

        # Feature 1 — 异常检测
        self.register(PipelineStep(
            name="anomaly",
            generator=lambda _ctx: [],
            depends_on=["event_pre", "technical", "fundamental", "news"],
            enabled=True,
        ))

        # 极端情景分析
        from gold_miner.signals.scenario import ScenarioAnalyzer

        self.register(PipelineStep(
            name="scenario",
            generator=lambda ctx: ScenarioAnalyzer().generate_signals(),
            depends_on=["polymarket", "fundamental"],
            enabled=True,
        ))

    def register(self, step: PipelineStep) -> None:
        self._steps[step.name] = step

    def enable(self, name: str) -> None:
        if name in self._steps:
            self._steps[name].enabled = True

    def disable(self, name: str) -> None:
        if name in self._steps:
            self._steps[name].enabled = False

    def execute(self, context: PipelineContext) -> SignalBundle:
        """执行管线 — 拓扑排序 + 并行批次 (同一依赖级别的步骤并行执行)."""
        bundle = SignalBundle()
        enabled = {s.name: s for s in self._steps.values() if s.enabled}
        if not enabled:
            return bundle

        executed: set[str] = set()
        all_names = set(enabled.keys())

        while len(executed) < len(all_names):
            # 找出所有依赖已满足的步骤
            ready = [
                step for name, step in enabled.items()
                if name not in executed
                and all(dep in executed for dep in step.depends_on)
            ]

            if not ready:
                # 循环依赖或配置错误 — 安全退出
                remaining = all_names - executed
                logger.error(
                    f"Pipeline: 无法继续 — 剩余步骤依赖未满足: {remaining}"
                )
                break

            # 同一批次并行执行
            if len(ready) == 1:
                step = ready[0]
                try:
                    signals = step.generator(context)
                    for s in signals:
                        bundle.add(s)
                    executed.add(step.name)
                    logger.debug(f"Pipeline: {step.name} → {len(signals)}个信号")
                except Exception:
                    logger.exception(f"Pipeline步骤 [{step.name}] 执行失败")
                    executed.add(step.name)
            else:
                with ThreadPoolExecutor(max_workers=len(ready)) as pool:
                    futures: dict[Future, str] = {}
                    for step in ready:
                        futures[pool.submit(step.generator, context)] = step.name

                    for future in as_completed(futures):
                        name = futures[future]
                        try:
                            signals = future.result()
                            for s in signals:
                                bundle.add(s)
                            executed.add(name)
                            logger.debug(f"Pipeline: {name} → {len(signals)}个信号")
                        except Exception:
                            logger.exception(f"Pipeline步骤 [{name}] 执行失败")
                            executed.add(name)

        return bundle

    def execute_sequential(self, context: PipelineContext) -> SignalBundle:
        """串行执行管线 (原始实现,用于调试/对比)."""
        bundle = SignalBundle()
        executed: set[str] = set()

        enabled_steps = [s for s in self._steps.values() if s.enabled]

        while len(executed) < len(enabled_steps):
            for step in enabled_steps:
                if step.name in executed:
                    continue
                if not all(dep in executed for dep in step.depends_on):
                    continue

                try:
                    signals = step.generator(context)
                    for s in signals:
                        bundle.add(s)
                    executed.add(step.name)
                    logger.debug(f"Pipeline: {step.name} → {len(signals)}个信号")
                except Exception:
                    logger.exception(f"Pipeline步骤 [{step.name}] 执行失败")
                    executed.add(step.name)

        return bundle

    def execute_with_post_event(
        self,
        context: PipelineContext,
        events_with_outcomes: list[tuple[CalendarEvent, str, str]],
    ) -> SignalBundle:
        """包含事件结果后分析的完整管线."""
        from gold_miner.signals.event_driven import EventDrivenSignalGenerator

        # 临时替换 event_post 步骤
        original = self._steps.get("event_post")
        self._steps["event_post"] = PipelineStep(
            name="event_post",
            generator=lambda ctx: EventDrivenSignalGenerator(
                ctx.calendar
            ).generate_post_event_signals(events_with_outcomes),
            depends_on=[],
        )

        bundle = self.execute(context)

        if original:
            self._steps["event_post"] = original

        return bundle

    @property
    def step_names(self) -> list[str]:
        return [s.name for s in self._steps.values() if s.enabled]
