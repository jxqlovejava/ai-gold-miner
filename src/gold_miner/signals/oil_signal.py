"""油价信号 — WTI 原油冲击经「通胀预期→利率预期」渠道对金价的传导.

设计背景 (2026-07-24): 7/23 胡塞袭击沙特油轮 → WTI +7.16% → 9月加息概率
52%→77% → 金价单日 -2.4%。既有 8 维信号中通胀预期 (T10YIE) 为滞后指标，
油价冲击领先其 1-2 天，且无任何维度能量化捕获该渠道。

核心认知: 油价对金价非单调关系，符号取决于主导传导渠道——
  - 单日脉冲 (供给冲击)   → 加息预期渠道  → 短期看空 (1-3天)
  - 持续上行              → 通胀压力固化  → 短期看空 + 滞胀观察
  - 20日大幅上行          → 滞胀逻辑接管  → 中期看多 (双渠道受益)
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
from loguru import logger

from gold_miner.data.macro import MacroDataFetcher
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength

# 渠道标签 (写入 metadata["channel"] 与 description, 供报告归因引用)
CH_RATE_SHOCK = "rate_expectations"      # 加息预期渠道: 油价脉冲 → 通胀预期 → 加息概率 → 利空
CH_RATE_RELIEF = "rate_relief"           # 加息压力缓和: 油价急跌 → 通胀预期回落 → 利多
CH_STAGFLATION = "stagflation_watch"     # 滞胀观察: 油价持续高位 → 中期利多

# 阈值 (%)
_SPIKE_MODERATE = 3.0    # 单日脉冲 moderate
_SPIKE_STRONG = 5.0      # 单日脉冲 strong
_TREND_MODERATE = 8.0    # 5日趋势 moderate
_TREND_STRONG = 12.0     # 5日趋势 strong
_STAGFLATION_20D = 20.0  # 20日涨幅 → 滞胀观察


class OilSignalGenerator:
    """WTI 油价信号生成器 (dimension="oil").

    数据: FRED DCOILWTICO (日度, T0) + 新浪 hf_CL 实时价覆盖最新点 (缩小 FRED 1日滞后).
    """

    SOURCE_TIER = "T0"  # FRED 官方一手数据
    FRED_SERIES = "DCOILWTICO"

    def __init__(self, oil_df: pd.DataFrame | None = None) -> None:
        self.oil = oil_df

    def _fetch_oil(self) -> pd.DataFrame:
        """获取 WTI 近 40 日价格 (FRED), 并用新浪实时价覆盖最新点."""
        if self.oil is not None and not self.oil.empty:
            return self.oil
        df = MacroDataFetcher().fetch(
            start=datetime.now() - timedelta(days=40),
            series_id=self.FRED_SERIES,
        )
        latest_rt = self._fetch_sina_wti()
        if latest_rt and not df.empty:
            df = df.sort_values("timestamp").reset_index(drop=True)
            if abs(latest_rt - df["value"].iloc[-1]) / df["value"].iloc[-1] > 0.001:
                df.loc[len(df)] = {
                    "timestamp": pd.Timestamp.now().normalize(),
                    "value": latest_rt,
                    "series_id": "hf_CL",
                }
        return df

    @staticmethod
    def _fetch_sina_wti() -> float | None:
        """新浪 NYMEX WTI 期货实时价 (hf_CL)."""
        try:
            import httpx

            resp = httpx.get(
                "https://hq.sinajs.cn/list=hf_CL",
                headers={"Referer": "https://finance.sina.com.cn"},
                timeout=8.0,
            )
            fields = resp.text.split("=", 1)[-1].strip('";\n').split(",")
            price = float(fields[0])
            return price if price > 0 else None
        except Exception as e:
            logger.debug(f"新浪WTI实时价获取失败 (降级FRED): {e}")
            return None

    def generate_signals(self) -> list[Signal]:
        signals: list[Signal] = []
        try:
            df = self._fetch_oil()
        except Exception as e:
            logger.warning(f"油价数据获取失败: {e}")
            return signals
        if df is None or df.empty or len(df) < 6:
            logger.warning("油价数据不足 (<6点), 跳过油价信号")
            return signals

        df = df.sort_values("timestamp").reset_index(drop=True)
        latest = float(df["value"].iloc[-1])
        chg1 = (latest / float(df["value"].iloc[-2]) - 1) * 100
        chg5 = (latest / float(df["value"].iloc[-6]) - 1) * 100
        chg20 = (latest / float(df["value"].iloc[0]) - 1) * 100

        signals.extend(self._spike_signal(latest, chg1))
        signals.extend(self._trend_signal(latest, chg5))
        signals.extend(self._stagflation_signal(latest, chg20))
        return signals

    @staticmethod
    def _spike_signal(latest: float, chg1: float) -> list[Signal]:
        """单日脉冲 — 供给冲击 → 加息预期渠道 → 短期利空金."""
        if chg1 >= _SPIKE_STRONG:
            return [Signal(
                name="油价单日暴涨",
                dimension="oil",
                direction=SignalDirection.BEARISH,
                strength=SignalStrength.STRONG,
                score=-0.8,
                description=(
                    f"WTI 单日 {chg1:+.1f}% 至 ${latest:.2f} — 供给冲击经"
                    f"「通胀预期→加息预期→美元/美债收益率」渠道短期利空金 (1-3天)。"
                    f"若判别为一次性脉冲而非趋势性加息，跌至支撑区属于洗盘概率高"
                ),
                metadata={"channel": CH_RATE_SHOCK, "wti": latest, "chg_1d_pct": round(chg1, 2)},
            )]
        if chg1 >= _SPIKE_MODERATE:
            return [Signal(
                name="油价单日大涨",
                dimension="oil",
                direction=SignalDirection.BEARISH,
                strength=SignalStrength.MODERATE,
                score=-0.5,
                description=(
                    f"WTI 单日 {chg1:+.1f}% 至 ${latest:.2f} — 加息预期渠道短期利空金"
                ),
                metadata={"channel": CH_RATE_SHOCK, "wti": latest, "chg_1d_pct": round(chg1, 2)},
            )]
        if chg1 <= -_SPIKE_STRONG:
            return [Signal(
                name="油价单日暴跌",
                dimension="oil",
                direction=SignalDirection.BULLISH,
                strength=SignalStrength.MODERATE,
                score=0.4,
                description=(
                    f"WTI 单日 {chg1:+.1f}% 至 ${latest:.2f} — 通胀压力缓和，"
                    f"加息预期降温渠道利多金"
                ),
                metadata={"channel": CH_RATE_RELIEF, "wti": latest, "chg_1d_pct": round(chg1, 2)},
            )]
        if chg1 <= -_SPIKE_MODERATE:
            return [Signal(
                name="油价单日大跌",
                dimension="oil",
                direction=SignalDirection.BULLISH,
                strength=SignalStrength.WEAK,
                score=0.25,
                description=f"WTI 单日 {chg1:+.1f}% 至 ${latest:.2f} — 加息压力边际缓和",
                metadata={"channel": CH_RATE_RELIEF, "wti": latest, "chg_1d_pct": round(chg1, 2)},
            )]
        return []

    @staticmethod
    def _trend_signal(latest: float, chg5: float) -> list[Signal]:
        """5日趋势 — 通胀压力固化 → 短期持续利空金."""
        if chg5 >= _TREND_STRONG:
            return [Signal(
                name="油价持续飙升",
                dimension="oil",
                direction=SignalDirection.BEARISH,
                strength=SignalStrength.STRONG,
                score=-0.7,
                description=(
                    f"WTI 5日 {chg5:+.1f}% 至 ${latest:.2f} — 通胀压力固化，"
                    f"利率渠道压制持续；同时触发滞胀观察 (见20日信号)"
                ),
                metadata={"channel": CH_RATE_SHOCK, "wti": latest, "chg_5d_pct": round(chg5, 2)},
            )]
        if chg5 >= _TREND_MODERATE:
            return [Signal(
                name="油价持续上行",
                dimension="oil",
                direction=SignalDirection.BEARISH,
                strength=SignalStrength.MODERATE,
                score=-0.5,
                description=f"WTI 5日 {chg5:+.1f}% 至 ${latest:.2f} — 通胀压力持续累积，利空金",
                metadata={"channel": CH_RATE_SHOCK, "wti": latest, "chg_5d_pct": round(chg5, 2)},
            )]
        if chg5 <= -_TREND_STRONG:
            return [Signal(
                name="油价持续回落",
                dimension="oil",
                direction=SignalDirection.BULLISH,
                strength=SignalStrength.MODERATE,
                score=0.4,
                description=f"WTI 5日 {chg5:+.1f}% 至 ${latest:.2f} — 通胀压力趋势性缓和，利多金",
                metadata={"channel": CH_RATE_RELIEF, "wti": latest, "chg_5d_pct": round(chg5, 2)},
            )]
        if chg5 <= -_TREND_MODERATE:
            return [Signal(
                name="油价趋势回落",
                dimension="oil",
                direction=SignalDirection.BULLISH,
                strength=SignalStrength.WEAK,
                score=0.25,
                description=f"WTI 5日 {chg5:+.1f}% 至 ${latest:.2f} — 通胀压力边际缓和",
                metadata={"channel": CH_RATE_RELIEF, "wti": latest, "chg_5d_pct": round(chg5, 2)},
            )]
        return []

    @staticmethod
    def _stagflation_signal(latest: float, chg20: float) -> list[Signal]:
        """20日大幅上行 — 滞胀逻辑观察 → 中期利多金 (双渠道受益)."""
        if chg20 >= _STAGFLATION_20D:
            return [Signal(
                name="滞胀观察",
                dimension="oil",
                direction=SignalDirection.BULLISH,
                strength=SignalStrength.MODERATE,
                score=0.4,
                description=(
                    f"WTI 20日 {chg20:+.1f}% 至 ${latest:.2f} — 油价持续高位，"
                    f"滞胀逻辑开始接管：短期利率渠道利空与中期滞胀利多并存，"
                    f"若增长数据走弱确认滞胀，黄金将双渠道受益"
                ),
                metadata={"channel": CH_STAGFLATION, "wti": latest, "chg_20d_pct": round(chg20, 2)},
            )]
        return []


if __name__ == "__main__":
    # ponytail: 最小自测 — 逻辑破坏即失败 (阈值分支 + 空数据降级)
    sigs = OilSignalGenerator().generate_signals()
    for s in sigs:
        print(f"[{s.direction.value}] {s.name} | {s.strength.value} | {s.score:+.2f} | {s.description[:60]}")

    # 注入合成数据验证阈值分支
    import pandas as _pd

    def _mk(changes: list[float]) -> _pd.DataFrame:
        base = 90.0
        rows = []
        for i, c in enumerate(changes):
            base *= 1 + c / 100
            rows.append({"timestamp": _pd.Timestamp("2026-06-15") + _pd.Timedelta(days=i),
                         "value": base, "series_id": "TEST"})
        return _pd.DataFrame(rows)

    spike = OilSignalGenerator(oil_df=_mk([0] * 25 + [7.2])).generate_signals()
    assert any(s.name == "油价单日暴涨" and s.direction is SignalDirection.BEARISH for s in spike), spike
    trend = OilSignalGenerator(oil_df=_mk([3.0] * 25)).generate_signals()
    assert any(s.name == "油价持续飙升" for s in trend), trend
    stag = OilSignalGenerator(oil_df=_mk([1.2] * 30)).generate_signals()
    assert any(s.name == "滞胀观察" and s.direction is SignalDirection.BULLISH for s in stag), stag
    flat = OilSignalGenerator(oil_df=_mk([0.1] * 30)).generate_signals()
    assert flat == [], flat
    thin = OilSignalGenerator(oil_df=_mk([5.0, 5.0])).generate_signals()
    assert thin == [], thin  # 数据点不足 (<6) 必须静默降级
    print("oil_signal 自测通过 ✅")
