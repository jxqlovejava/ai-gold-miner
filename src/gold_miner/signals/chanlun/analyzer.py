"""缠论分析器 — 组合去包含/分型/笔/中枢/背驰/买卖点。

自研纯 Python 核心，零新增依赖（仅 numpy/pandas）。周期固定日线 "D"（周线去包含易过度合并）。
定位为技术面结构增强，不改变宏观基本面主导的决策链。
"""
from __future__ import annotations

import logging
from dataclasses import replace

import numpy as np
import pandas as pd

from gold_miner.signals.chanlun.core.bi import build_bis
from gold_miner.signals.chanlun.core.bihuang import detect_divergence
from gold_miner.signals.chanlun.core.fractal import detect_fractals
from gold_miner.signals.chanlun.core.merge import merge_bars
from gold_miner.signals.chanlun.core.zhongshu import detect_zhongshus
from gold_miner.signals.chanlun.points import detect_points
from gold_miner.signals.chanlun.schema import Bi, ChanlunPoint, ChanlunResult

logger = logging.getLogger(__name__)

# 部分数据源（akshare 等）返回 RangeIndex，真实日期在「日期」列。
# 全管道（去包含/分型/笔/中枢/买卖点）依赖 index 作为 dt，
# 故入口统一归一化为 DatetimeIndex，保证 dt 语义与报告日期渲染。
_DATETIME_COLS = ("date", "datetime", "日期", "trade_date", "Date", "time")


def _ensure_datetime_index(df) -> pd.DataFrame:
    if df is None or isinstance(getattr(df, "index", None), pd.DatetimeIndex):
        return df
    for col in _DATETIME_COLS:
        if col in df.columns:
            try:
                parsed = pd.to_datetime(df[col], errors="coerce")
                if parsed.notna().sum() < len(df) * 0.9:
                    continue  # 该列解析失败比例过高，尝试下一列
                out = df.copy()
                out["_dt"] = parsed
                out = out.set_index("_dt").drop(columns=[col])
                return out
            except Exception:
                continue
    return df


# 部分数据源返回中文列名（开盘/收盘/最高/最低/成交量/成交额），
# 下游 merge_bars/_assign_macd_area 依赖英文列名 open/high/low/close，
# 统一在 analyze 入口映射，避免 KeyError 静默降级为空结果。
_CN_COL_MAP = {
    "开盘": "open", "收盘": "close", "最高": "high", "最低": "low",
    "成交量": "volume", "成交额": "amount",
}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None:
        return df
    rename = {c: _CN_COL_MAP[c] for c in df.columns if c in _CN_COL_MAP}
    if rename:
        df = df.rename(columns=rename)
    return df


def _assign_macd_area(bis: list[Bi], df: pd.DataFrame) -> list[Bi]:
    """按笔区间回填 MACD 柱面积。"""
    close = df["close"].values.astype(float)
    ema12 = pd.Series(close).ewm(span=12, adjust=False).mean().values
    ema26 = pd.Series(close).ewm(span=26, adjust=False).mean().values
    dif = ema12 - ema26
    dea = pd.Series(dif).ewm(span=9, adjust=False).mean().values
    macd = (dif - dea) * 2.0
    raw_pos = {dt: k for k, dt in enumerate(df.index)}
    out: list[Bi] = []
    for b in bis:
        s = raw_pos.get(b.start_fx.dt)
        e = raw_pos.get(b.end_fx.dt)
        if s is None or e is None:
            area = 0.0
        else:
            lo, hi = min(s, e), max(s, e) + 1
            area = float(np.sum(np.abs(macd[lo:hi])))
        out.append(replace(b, macd_area=area))
    return out


def make_chanlun_citation() -> dict:
    """缠论结构计算的数据溯源引用（T2 解释性质）。"""
    return {
        "provider": "indicator",
        "field": "chanlun_D",
        "data_type": "daily_bar",
        "source_tier": "T2",
        "nature": "interpretation",
        "confidence": 0.8,
    }


class ChanlunAnalyzer:
    """缠论结构分析器。freq 固定 "D"（黄金日线）。"""

    def __init__(self, freq: str = "D", min_bi_bars: int = 4):
        self.freq = freq
        self.min_bi_bars = min_bi_bars

    def analyze(self, df, symbol: str, name: str = "", freq: str | None = None) -> ChanlunResult:
        f = freq or self.freq
        df = _ensure_datetime_index(df)
        df = _normalize_columns(df)
        if df is None or len(df) < 30:
            return self._empty_result(symbol, name, f, reason="[DATA_GAP] 缠论: 数据不足30根")
        try:
            merged = merge_bars(df)
            fractals = detect_fractals(merged)
            bis = _assign_macd_area(build_bis(fractals, self.min_bi_bars), df)
            zss = detect_zhongshus(bis)
            divergences = detect_divergence(bis)
            points = detect_points(bis, zss, divergences)

            current_state = self._current_state(bis, zss, points, df)
            signals = self.to_signal(points)
            citations = [make_chanlun_citation()]
            conf = self._confidence(len(df))
            return ChanlunResult(
                symbol=symbol, name=name, freq=f, backend="self",
                fractals=fractals, bis=bis, zhongshus=zss, points=points,
                current_state=current_state, signals=signals,
                source_citations=citations, confidence=conf,
            )
        except Exception as exc:
            logger.warning("chanlun analyze failed: %s", exc)
            return self._empty_result(symbol, name, f, reason=f"[DATA_GAP] 缠论: {exc}")

    @staticmethod
    def to_signal(points: list[ChanlunPoint]) -> dict:
        """长多信号映射。一买/二买/三买 → entry；其余 → exit。"""
        entry, exit_ = [], []
        for p in points:
            item = {"kind": p.kind, "price": p.price, "dt": str(p.dt),
                    "confidence": p.confidence}
            (entry if p.kind in ("一买", "二买", "三买") else exit_).append(item)
        return {"entry": entry, "exit": exit_}

    @staticmethod
    def _current_state(bis, zss, points, df) -> dict:
        last_close = float(df["close"].iloc[-1])
        state = {"last_close": last_close, "bi_count": len(bis),
                 "zhongshu_state": "未形成", "position": "未知"}
        if zss:
            zs = zss[-1]
            state["zhongshu_state"] = zs.state
            state["zg"], state["zd"], state["zz"] = zs.zg, zs.zd, zs.zz
            if last_close > zs.zg:
                state["position"] = "中枢上方"
            elif last_close < zs.zd:
                state["position"] = "中枢下方"
            else:
                state["position"] = "中枢内"
        if points:
            # detect_points 按笔序生成，points 不保证时间有序；
            # last_point 必须取时间最新的买卖点，否则最近信号失真
            lp = max(points, key=lambda p: pd.to_datetime(p.dt))
            state["last_point"] = {"kind": lp.kind, "dt": str(lp.dt), "price": float(lp.price)}
        return state

    @staticmethod
    def _confidence(n_bars: int) -> float:
        base = 0.75
        if n_bars < 50:
            base *= 0.8
        return round(max(0.0, min(1.0, base)), 3)

    @staticmethod
    def _empty_result(symbol, name, freq, reason) -> ChanlunResult:
        return ChanlunResult(symbol=symbol, name=name, freq=freq, backend="self",
                             fractals=[], bis=[], zhongshus=[], points=[],
                             current_state={"gap": reason},
                             signals={"entry": [], "exit": []},
                             source_citations=[], confidence=0.0)
