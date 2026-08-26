"""置信度校准 — 用历史预测-结果配对, 把 confidence 校准为经验命中概率.

背景 (2026-08-26): Brier 分解显示系统 confidence 系统性低估 —
平均预测概率 0.645 vs 平均实际命中 0.815 (低估 0.17), Reliability 0.051。
方向级校准 (用历史同方向命中率代替 confidence) 使 Brier 0.1856 → 0.1475。

策略:
  - 按方向 (long/short/neutral) 统计历史已结算命中率;
  - 某方向样本 ≥ MIN_CALIBRATION_SAMPLES 才用命中率校准, 否则保留原始 confidence
    (小样本命中率不可靠, 如 short 历史仅 2 条 0% — 校准会失真);
  - 校准随 prediction_journal 增长自动更新 (每次 record_prediction 重建)。

注意: neutral 命中率是「|收益|<1.5% 中性带」概率, 反映行情波动而非预测能力;
      calibrated_confidence 用于决策参考, 不代表方向预测力强弱。
"""
from __future__ import annotations

from typing import Any

# 某方向最少已结算样本数 — 低于此阈值不校准 (保留原始 confidence)
MIN_CALIBRATION_SAMPLES = 10


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """兼容 dict 或 dataclass 记录对象."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _normalize(direction: str) -> str:
    """long/short/neutral 归一 (lazy import 避免与 tracker 循环依赖)."""
    from gold_miner.improvement.tracker import normalize_direction

    return normalize_direction(direction)


def _is_correct(direction: str, actual_return: float) -> bool:
    """判定是否正确 (与 tracker.determine_correctness 对齐)."""
    from gold_miner.improvement.tracker import determine_correctness

    return determine_correctness(direction, actual_return)


def build_calibration(records: list[Any]) -> dict[str, dict[str, float]]:
    """从预测记录构建方向级校准表.

    Args:
        records: 预测记录 (dict 或 dataclass), 每条含 direction/confidence/
                 actual_price/actual_return/invalidated。

    Returns:
        {direction: {"n": int, "hit_rate": float, "mean_conf": float}}
    """
    bucket: dict[str, list[Any]] = {}
    for r in records:
        if _get(r, "invalidated") or _get(r, "actual_price") is None:
            continue
        d = _normalize(_get(r, "direction") or "")
        bucket.setdefault(d, []).append(r)

    table: dict[str, dict[str, float]] = {}
    for d, grp in bucket.items():
        ok = sum(1 for r in grp if _is_correct(d, _get(r, "actual_return") or 0))
        table[d] = {
            "n": float(len(grp)),
            "hit_rate": round(ok / len(grp), 3) if grp else 0.0,
            "mean_conf": round(
                sum(_get(r, "confidence") or 0 for r in grp) / len(grp), 3
            ) if grp else 0.0,
        }
    return table


def calibrate_confidence(
    direction: str,
    confidence: float,
    table: dict[str, dict[str, float]] | None = None,
    min_samples: int = MIN_CALIBRATION_SAMPLES,
) -> float:
    """方向 + 原始置信度 → 校准后置信度.

    该方向历史已结算样本 ≥ min_samples → 用命中率;
    样本不足 → 保留原始 confidence (小样本命中率不可靠)。
    """
    d = _normalize(direction)
    entry = (table or {}).get(d)
    if entry and entry.get("n", 0) >= min_samples:
        return float(entry["hit_rate"])
    return float(confidence)
