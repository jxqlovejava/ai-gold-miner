"""信号基类与通用类型."""
from __future__ import annotations

import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from gold_miner.compat import StrEnum


class SignalDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class SignalStrength(StrEnum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"


class FactType(StrEnum):
    """信号的事实/解释分类.

    用于区分不可争议的客观数据与需要主观判断的因果推断，
    避免把「解释」当作「事实」引用。
    """

    FACT = "fact"                    # 不可争议的客观数据: 价格、成交量、官方数值
    INTERPRETATION = "interpretation"  # 因果推断: "X 因为 Y 上涨"
    PROJECTION = "projection"         # 预测/展望: "预计 X 将..."
    OPINION = "opinion"               # 机构/分析师主观观点


DIMENSION_LABELS: dict[str, str] = {
    # 信号维度 key → 中文标签（输出语言铁律：维度名一律中文）
    "technical": "技术面",
    "fundamental": "基本面",
    "sentiment": "情绪面",
    "news": "消息面",
    "long_term": "长期趋势",
    "oil": "油价",
    "hype_bias": "反带节奏",
    "recent_events": "近期事件",
    "polymarket": "预测市场",
    "monitor": "监控触发",
    "event": "事件驱动",
    "event_calendar": "经济日历",
    "scenario": "情景推演",
    "macro_pivot": "宏观政策转向",
    # 记忆/主题 ID 兼容（事件驱动信号可能以主题维出现，兜底显示）
    "smart_money": "聪明钱",
    "geopolitical": "地缘冲突",
    "fed_policy": "美联储政策",
    "ceasefire_diplomacy": "停火谈判与外交",
    "israel_houthi": "以色列-胡塞-也门",
}


def dimension_label(dim: str) -> str:
    """信号维度 key → 中文标签；未收录的 key 原样返回."""
    return DIMENSION_LABELS.get(dim, dim)


def hype_suppression_factor(signals: list[Signal]) -> float:
    """反带节奏对情绪面方向分的压制系数 [0, 0.5].

    检出机构带节奏信号（metadata 含 ``heuristic``，如标题党炒作/同源洗稿/
    情绪极端化/低可信源带节奏/机构言行不一）时，按最强 hype 信号的 |score|
    缩放：|score| 越大（带节奏越严重），压制越强。情绪面均分需 ×(1-factor)
    向中性收敛——机构带节奏制造的狂热情绪不可信，不应作为方向依据。

    0.0 = 无压制。在 ScoringEngine.score() 与 SignalBundle.
    dimension_direction_summary() 两处调用，保证 composite 与展示口径一致。
    """
    hype_scores = [abs(s.score) for s in signals if s.metadata.get("heuristic")]
    return min(0.5, max(hype_scores)) if hype_scores else 0.0


# 维度方向判定噪音带：均分 |avg| 落在此带内视为「无方向优势」，
# 即使计数一边倒也不确认方向（防弱信号堆数撑起假看多/假看空）。
DIMENSION_NOISE_BAND = 0.10


def _char_width(ch: str) -> int:
    """单字符显示宽度（CJK 全角=2，其余=1）."""
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _fit_display(s: str, width: int) -> str:
    """按显示宽度截断并补空格，使中英文混排的表格对齐."""
    cur = 0
    for i, ch in enumerate(s):
        w = _char_width(ch)
        if cur + w > width:
            s = s[:i]
            break
        cur += w
    pad = width - cur
    return s + " " * max(pad, 0)


@dataclass
class Signal:
    name: str
    dimension: str
    direction: SignalDirection
    strength: SignalStrength
    score: float
    description: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    fact_type: FactType = FactType.INTERPRETATION  # 默认保守: 解释而非事实

    @property
    def is_fact(self) -> bool:
        return self.fact_type == FactType.FACT

    @property
    def needs_verification(self) -> bool:
        """需要额外验证的信号类型."""
        return self.fact_type in (FactType.PROJECTION, FactType.OPINION)

    def fact_label(self) -> str:
        """返回中文标签."""
        labels = {
            FactType.FACT: "🔵 事实",
            FactType.INTERPRETATION: "🟡 解释",
            FactType.PROJECTION: "🔮 预测",
            FactType.OPINION: "💬 观点",
        }
        return labels.get(self.fact_type, "❓ 未知")


@dataclass(frozen=True)
class DimensionConsensus:
    """多维度信号共识检测结果."""

    active_dimensions: int       # 有信号的维度数
    bullish_dimensions: int      # 多数信号偏多的维度数
    bearish_dimensions: int      # 多数信号偏空的维度数
    consensus_direction: str     # "bullish" | "bearish" | "none"
    consensus_ratio: float       # 同向维度 / 活跃维度
    has_consensus: bool          # 是否达成共识


@dataclass
class SignalBundle:
    signals: list[Signal] = field(default_factory=list)
    composite_score: float = 0.0
    confidence: float = 0.0
    hype_suppression: float = 0.0  # 反带节奏压制系数(本次评分已施加)，供日志/报告披露

    def add(self, signal: Signal) -> None:
        self.signals.append(signal)

    def by_dimension(self, dimension: str) -> list[Signal]:
        return [s for s in self.signals if s.dimension == dimension]

    def bullish_count(self) -> int:
        return sum(1 for s in self.signals if s.direction == SignalDirection.BULLISH)

    def bearish_count(self) -> int:
        return sum(1 for s in self.signals if s.direction == SignalDirection.BEARISH)

    def dimensional_consensus(
        self,
        min_active_dimensions: int = 4,
        consensus_ratio_threshold: float = 0.75,
    ) -> DimensionConsensus:
        """检测多维度信号是否形成方向共识.

        每个维度内取多数方向，然后统计各维度之间的方向一致性。
        当活跃维度数 ≥ min_active_dimensions 且同向比例 ≥ consensus_ratio_threshold
        时判定为共识形成。

        Args:
            min_active_dimensions: 最少活跃维度数（默认 4）
            consensus_ratio_threshold: 同向比例阈值（默认 0.75）
        """
        if not self.signals:
            return DimensionConsensus(
                active_dimensions=0,
                bullish_dimensions=0,
                bearish_dimensions=0,
                consensus_direction="none",
                consensus_ratio=0.0,
                has_consensus=False,
            )

        # 按维度分组，每个维度取多数方向
        dim_direction: dict[str, str] = {}
        for dim in {s.dimension for s in self.signals}:
            signals_in_dim = self.by_dimension(dim)
            counter = Counter(
                s.direction.value for s in signals_in_dim
                if s.direction != SignalDirection.NEUTRAL
            )
            if not counter:
                continue
            # 取该维度最多信号的方向
            dim_direction[dim] = counter.most_common(1)[0][0]

        active = len(dim_direction)
        bullish = sum(1 for d in dim_direction.values() if d == "bullish")
        bearish = sum(1 for d in dim_direction.values() if d == "bearish")
        majority = max(bullish, bearish)
        ratio = majority / active if active > 0 else 0.0

        has_consensus = (
            active >= min_active_dimensions
            and ratio >= consensus_ratio_threshold
        )

        direction = ("bullish" if bullish > bearish else "bearish") if has_consensus else "none"

        return DimensionConsensus(
            active_dimensions=active,
            bullish_dimensions=bullish,
            bearish_dimensions=bearish,
            consensus_direction=direction,
            consensus_ratio=ratio,
            has_consensus=has_consensus,
        )

    def dimension_direction_summary(self) -> dict[str, dict]:
        """返回每个维度的方向摘要，包含各方向信号计数、主导方向、均分等。

        四态判定（计数 + 均分双闸门）：
          - 非中性信号为 0           → insufficient_data（全中性，方向未知）
          - 计数一边倒 且 |均分|≥噪音带 → bullish / bearish（方向确认）
          - 计数一边倒 但 均分在噪音带内 → insufficient_data（强度不足以确认方向，
            防弱信号堆数撑起假方向）
          - 计数平手 (bull==bear)     → dispute（多空激烈分歧——分歧本身是信息，
            预示方向选择/波动放大，对应 r013 观望，不是数据缺失）

        Returns:
            dict: key=维度名, value={
                "dominant": "bullish"|"bearish"|"dispute"|"insufficient_data",
                "bullish": int, "bearish": int, "neutral": int,
                "total": int, "avg_score": float,
                "insufficient_data": bool, "dispute": bool,
            }
        """
        if not self.signals:
            return {}

        summary: dict[str, dict] = {}
        for dim in sorted({s.dimension for s in self.signals}):
            signals_in_dim = self.by_dimension(dim)
            bull = sum(1 for s in signals_in_dim if s.direction == SignalDirection.BULLISH)
            bear = sum(1 for s in signals_in_dim if s.direction == SignalDirection.BEARISH)
            neutral = sum(1 for s in signals_in_dim if s.direction == SignalDirection.NEUTRAL)
            total = len(signals_in_dim)
            avg_score = sum(s.score for s in signals_in_dim) / total if total > 0 else 0.0

            # 反带节奏压制：情绪面均分与 composite 同口径压低(向中性收敛)，
            # 使维度表方向/均分反映机构带节奏对情绪面的失真。
            if dim == "sentiment":
                _hype = hype_suppression_factor(self.signals)
                if _hype > 0:
                    avg_score *= (1 - _hype)

            # 四态判定: 计数 + 均分双闸门
            non_neutral = bull + bear
            if non_neutral == 0:
                dominant = "insufficient_data"          # 全中性，方向未知
            elif bull > bear:
                # 计数偏多但均分须走出噪音带才确认方向（含中性稀释：均分按全信号算）
                dominant = (
                    "bullish" if avg_score >= DIMENSION_NOISE_BAND else "insufficient_data"
                )
            elif bear > bull:
                dominant = (
                    "bearish" if avg_score <= -DIMENSION_NOISE_BAND else "insufficient_data"
                )
            else:
                # bull == bear: 平手 = 多空激烈分歧，是信息不是数据缺失
                dominant = "dispute"

            summary[dim] = {
                "dominant": dominant,
                "bullish": bull,
                "bearish": bear,
                "neutral": neutral,
                "total": total,
                "avg_score": round(avg_score, 2),
                "insufficient_data": dominant == "insufficient_data",
                "dispute": dominant == "dispute",
            }
        return summary

    def dimension_direction_counts(self) -> tuple[int, int, int, int]:
        """返回 (看多维度数, 看空维度数, 分歧维度数, 数据不足维度数)。

        基于 dimension_direction_summary() 的 dominant 字段计算。
        看多/看空为有效方向维度；分歧（多空平手）单独计数，作为观望信号
        不计入有效方向；数据不足为信息缺失维度。

        Returns:
            tuple: (bullish_dimensions, bearish_dimensions,
                    dispute_dimensions, insufficient_data_dimensions)
        """
        summary = self.dimension_direction_summary()
        bullish = sum(1 for v in summary.values() if v["dominant"] == "bullish")
        bearish = sum(1 for v in summary.values() if v["dominant"] == "bearish")
        dispute = sum(1 for v in summary.values() if v["dispute"])
        insufficient = sum(1 for v in summary.values() if v["insufficient_data"])
        return (bullish, bearish, dispute, insufficient)

    def format_dimension_table(self) -> str:
        """生成程序化维度方向总览表，LLM 可直接嵌入报告。

        表格包含每个维度的方向、看多/看空/中性信号数、均分，
        以及双口径汇总行：
          - 维度数对比（有效维度间的方向对比）
          - 信号数对比（全部信号的看多/看空/中性计数）
        双口径并列为用户提供两种视角：维度粒度看方向共识, 信号粒度看内部背离.

        Returns:
            str: 格式化的中文表格字符串
        """
        summary = self.dimension_direction_summary()
        if not summary:
            return "(无信号)"

        lines = [
            "┌──────────────────┬────────────────────┬──────┬──────┬──────┬────────┐",
            "│      维度        │        方向        │ 看多 │ 看空 │ 中性 │  均分  │",
            "├──────────────────┼────────────────────┼──────┼──────┼──────┼────────┤",
        ]

        dir_labels = {
            "bullish": "🟢 看多",
            "bearish": "🔴 看空",
            "dispute": "⚠️ 分歧",
            "insufficient_data": "🟡 数据不足",
        }

        for dim, info in summary.items():
            # 维度名输出中文标签（输出语言铁律），按显示宽度对齐
            dim_display = dimension_label(dim)
            dir_display = dir_labels.get(info["dominant"], info["dominant"])
            lines.append(
                f"│ {_fit_display(dim_display, 16)} │ {_fit_display(dir_display, 18)} │ "
                f"{info['bullish']:>4} │ {info['bearish']:>4} │ "
                f"{info['neutral']:>4} │ {info['avg_score']:>+6.2f} │"
            )

        lines.append("└──────────────────┴────────────────────┴──────┴──────┴──────┴────────┘")

        # 汇总行 1: 维度数对比（有效维度间的方向对比，分歧维度单独标注）
        bull_dims, bear_dims, disp_dims, insuf_dims = self.dimension_direction_counts()
        active = bull_dims + bear_dims
        if active > 0:
            if bull_dims > bear_dims:
                consensus_note = f"看多 {bull_dims}维 vs 看空 {bear_dims}维"
            elif bear_dims > bull_dims:
                consensus_note = f"看空 {bear_dims}维 vs 看多 {bull_dims}维"
            else:
                consensus_note = f"看多 {bull_dims}维 vs 看空 {bear_dims}维 (平手)"
        else:
            consensus_note = "无有效方向维度"
        if disp_dims > 0:
            consensus_note += f"（{disp_dims}维分歧）"
        if insuf_dims > 0:
            consensus_note += f"（{insuf_dims}维数据不足）"
        lines.append(f"  有效维度方向对比: {consensus_note}")

        # 汇总行 2: 信号数对比（全部信号的看多/看空/中性计数）
        # 揭示维度粒度掩盖的背离 —— 资金流各子项常被归入同一维度
        sig_bull = sum(1 for s in self.signals if s.direction == SignalDirection.BULLISH)
        sig_bear = sum(1 for s in self.signals if s.direction == SignalDirection.BEARISH)
        sig_neutral = sum(1 for s in self.signals if s.direction == SignalDirection.NEUTRAL)
        if sig_bull or sig_bear or sig_neutral:
            sig_note = f"看多 {sig_bull}个 vs 看空 {sig_bear}个"
            if sig_neutral > 0:
                sig_note += f"（{sig_neutral}个中性）"
            lines.append(f"  有效信号方向对比: {sig_note}")

        return "\n".join(lines)
