"""凯利公式仓位管理.

参考 Ed Thorp (1967) 实践的 1/4 Kelly 准则，结合黄金投资的现实约束：
- 用信号综合评分作为期望收益估计
- 用 ATR 作为波动率代理
- 1/4 Kelly 避免过激
- 硬上限取 r001 (20%)，叠加军规阻断

公式:
  f* = μ / σ²          (连续凯利，适合对数正态收益)
  实际仓位 = min(f* / 4, 0.20)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KellyResult:
    """凯利仓位计算结果."""

    raw_kelly: float           # 原始凯利比例
    quarter_kelly: float       # 1/4 凯利
    suggested_pct: float       # 最终建议仓位（已叠加硬上限）
    edge: float                # 期望收益估计
    variance: float            # 方差估计
    confidence: float          # 信号置信度
    capped: bool               # 是否触及硬上限
    rationale: str             # 计算说明

    def is_actionable(self) -> bool:
        return self.suggested_pct > 0.01


def kelly_position(
    composite_score: float,
    confidence: float,
    atr_pct: float = 0.02,
    hard_cap: float = 0.20,
    quarter_factor: float = 0.25,
) -> KellyResult:
    """计算凯利公式建议仓位.

    Args:
        composite_score: 综合评分 [-1, +1]，正数利多
        confidence: 信号置信度 [0, 1]
        atr_pct: 日线 ATR 占价格百分比（如 0.02 = 2%）
        hard_cap: 硬仓位上限（r001 = 20%）
        quarter_factor: 凯利缩放因子（默认 1/4）

    Returns:
        KellyResult with suggested position
    """
    # 边缘 = 信号方向 × 置信度 × |评分| × 缩放因子
    # 缩放因子 0.001 将信号映射到日收益率量级 (~1-5bp)
    edge = composite_score * confidence * 0.001

    # 方差 = ATR_pct²  (日度年化波动率代理)
    variance = max(atr_pct ** 2, 0.0001)  # 避免除零，最低 1%²

    raw = edge / variance if variance > 0 else 0.0
    raw = max(raw, 0.0)  # 凯利不做空取 0

    quarter = raw * quarter_factor
    suggested = min(quarter, hard_cap)

    return KellyResult(
        raw_kelly=round(raw, 4),
        quarter_kelly=round(quarter, 4),
        suggested_pct=round(suggested, 4),
        edge=round(edge, 6),
        variance=round(variance, 6),
        confidence=round(confidence, 4),
        capped=suggested >= hard_cap,
        rationale=_build_rationale(composite_score, confidence, atr_pct, raw, quarter, suggested, hard_cap),
    )


def _build_rationale(
    composite: float,
    confidence: float,
    atr_pct: float,
    raw: float,
    quarter: float,
    suggested: float,
    cap: float,
) -> str:
    if composite <= -0.15:
        qualifier = "信号偏空，凯利归零"
    elif composite < 0:
        qualifier = "信号偏弱但不做空"
    elif raw < 0.05:
        qualifier = "优势太小，保守轻仓或观望"
    elif raw > 1.0:
        qualifier = "信号极强但凯利超限，1/4 Kelly + 硬上限内控"
    else:
        qualifier = "适中"

    return (
        f"综合评分 {composite:+.2f} × 置信度 {confidence:.0%} = 边缘 {composite * confidence * 0.001:.4f}; "
        f"ATR {atr_pct:.1%} → σ²={atr_pct**2:.4f}; "
        f"凯利 {raw:.1%} → 1/4 Kelly {quarter:.1%} → {'触及' if suggested >= cap else ''}上限 {cap:.0%}; "
        f"{qualifier}"
    )
