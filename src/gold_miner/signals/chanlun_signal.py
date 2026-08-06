"""缠论信号生成器 — 将 ChanlunResult 桥接为 gold-miner Signal 体系。

定位: 技术面结构增强。买卖点/中枢破位/中枢状态 → dimension="technical" 信号。
保守设计: 全部 WEAK 强度，score 范围 ±0.1~±0.35，仅作结构参考，不主导决策。
黄金仅做多语境: 一卖/二卖/三卖映射为「减仓/止盈参考」而非做空信号。
"""
from __future__ import annotations

import logging

import pandas as pd

from gold_miner.signals.base import Signal, SignalDirection, SignalStrength
from gold_miner.signals.chanlun.analyzer import ChanlunAnalyzer
from gold_miner.signals.chanlun.schema import ChanlunPoint, ChanlunResult

logger = logging.getLogger(__name__)

SOURCE_TIER = "T2"  # 结构计算类 → T2 解释性质


class ChanlunSignalGenerator:
    """缠论结构信号生成器.

    输入 gold_df（OHLCV + datetime 索引），输出 list[Signal]（dimension="technical"）。
    同时暴露 summary_dict() 供报告「缠论结构」板块渲染。
    """

    def __init__(self, gold_df, symbol: str = "Au99.99", name: str = "黄金") -> None:
        self.gold_df = gold_df
        self.symbol = symbol
        self.name = name
        self._result: ChanlunResult | None = None

    # ------------------------------------------------------------------
    # 分析入口（缓存结果）
    # ------------------------------------------------------------------

    def analyze(self) -> ChanlunResult:
        """运行缠论分析（结果缓存，多次调用不重复计算）。"""
        if self._result is None:
            self._result = ChanlunAnalyzer(freq="D").analyze(
                self.gold_df, self.symbol, self.name
            )
        return self._result

    def summary_dict(self) -> dict:
        """缠论结构摘要（供报告板块渲染）。"""
        return self.analyze().to_summary_dict()

    # ------------------------------------------------------------------
    # 信号生成
    # ------------------------------------------------------------------

    def generate_signals(self, recency_days: int = 45) -> list[Signal]:
        """生成缠论结构信号（0-4 条）。

        映射规则（保守）:
        - 一买/二买/三买 → BULLISH WEAK（分批建仓锚点参考，仅最近 1 个）
        - 一卖/二卖/三卖 → BEARISH WEAK（减仓/止盈参考，仅最近 1 个）
        - 现价跌破最近中枢下沿 → BEARISH WEAK（结构转弱）
        - 中枢上移且现价在中枢上方 → BULLISH WEAK（多头结构）

        Args:
            recency_days: 买卖点有效窗口（默认 45 天）。远古买卖点不作活跃信号，
                避免历史点位污染当前技术维度。
        """
        result = self.analyze()
        if result.current_state.get("gap"):
            return []
        signals: list[Signal] = []

        # 1) 买卖点 — 只取最近窗口内最新的买点/卖点各 1 个
        recent_points = self._recent_points(result.points, recency_days)
        for p in recent_points:
            sig = self._point_signal(p)
            if sig:
                signals.append(sig)

        # 2) 中枢状态信号
        cs = result.current_state
        if result.zhongshus and cs.get("zd") is not None:
            zs = result.zhongshus[-1]
            last_close = cs.get("last_close", 0.0)
            if last_close and last_close < zs.zd:
                signals.append(Signal(
                    name="缠论跌破中枢下沿",
                    dimension="technical",
                    direction=SignalDirection.BEARISH,
                    strength=SignalStrength.WEAK,
                    score=-0.15,
                    description=(
                        f"现价({last_close:.1f})跌破最近中枢下沿ZD({zs.zd:.1f})，"
                        f"结构转弱，追高需谨慎"
                    ),
                    metadata={
                        "source_tier": SOURCE_TIER,
                        "zs_zg": round(zs.zg, 1),
                        "zs_zd": round(zs.zd, 1),
                        "zs_state": zs.state,
                    },
                ))
            elif cs.get("position") == "中枢上方" and zs.state == "上移":
                signals.append(Signal(
                    name="缠论中枢上移",
                    dimension="technical",
                    direction=SignalDirection.BULLISH,
                    strength=SignalStrength.WEAK,
                    score=0.12,
                    description=(
                        f"最近中枢{zs.state}且现价({last_close:.1f})位于中枢上方，"
                        f"多头结构延续"
                    ),
                    metadata={
                        "source_tier": SOURCE_TIER,
                        "zs_zg": round(zs.zg, 1),
                        "zs_zd": round(zs.zd, 1),
                        "zs_state": zs.state,
                    },
                ))

        return signals

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _recent_points(points: list[ChanlunPoint], recency_days: int) -> list[ChanlunPoint]:
        """过滤出最近窗口内最新的买点/卖点各 1 个（去历史噪音）。

        窗口锚定在最新点位日期（非当前时间），保证历史回放也稳定。
        """
        if not points:
            return []
        parsed: list[tuple[pd.Timestamp, ChanlunPoint]] = []
        for p in points:
            try:
                parsed.append((pd.to_datetime(p.dt), p))
            except Exception:
                continue
        if not parsed:
            return []
        latest_dt = max(dt for dt, _ in parsed)
        cutoff = latest_dt - pd.Timedelta(days=recency_days)
        recent = [p for dt, p in parsed if dt >= cutoff]
        buys = [p for p in recent if p.kind in ("一买", "二买", "三买")]
        sells = [p for p in recent if p.kind in ("一卖", "二卖", "三卖")]
        out: list[ChanlunPoint] = []
        if buys:
            out.append(max(buys, key=lambda p: pd.to_datetime(p.dt)))
        if sells:
            out.append(max(sells, key=lambda p: pd.to_datetime(p.dt)))
        return out

    @staticmethod
    def _point_signal(p: ChanlunPoint) -> Signal | None:
        """单个买卖点 → Signal. 买点看多, 卖点看空(减仓参考)."""
        is_buy = p.kind in ("一买", "二买", "三买")
        direction = SignalDirection.BULLISH if is_buy else SignalDirection.BEARISH
        base_score = 0.25 if is_buy else -0.25
        # 置信度 0.7~0.8 → score 0.18~0.20；三买(0.8)略高于一买(0.7)
        score = round(base_score * p.confidence, 2)
        role = "分批建仓锚点参考" if is_buy else "减仓/止盈参考"
        return Signal(
            name=f"缠论{p.kind}",
            dimension="technical",
            direction=direction,
            strength=SignalStrength.WEAK,
            score=score,
            description=(
                f"{p.rationale}。{role}: 参考价位 {p.price:.1f} "
                f"(置信度 {p.confidence:.0%})"
            ),
            metadata={
                "source_tier": SOURCE_TIER,
                "kind": p.kind,
                "price": round(float(p.price), 1),
                "dt": str(p.dt),
                "confidence": p.confidence,
                "is_buy": is_buy,
            },
        )
