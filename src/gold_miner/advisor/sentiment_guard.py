"""情绪与市场节奏对齐 — 监控散户情绪、机构动向、ETF资金流.

核心逻辑:
  1. 监控散户情绪指标（恐惧贪婪指数、VIX、社交媒体情绪）
  2. 监控机构持仓（COT报告、ETF资金流）
  3. 当散户与机构方向背离时，提示跟随机构
  4. 当情绪极端时，发出反向预警

使用方式:
    guard = SentimentGuard()
    report = guard.analyze()
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from gold_miner.advisor.core import AdvisorReport, AlertLevel, SentimentReading
from gold_miner.data.macro import MacroDataFetcher
from gold_miner.signals.etf_flow_signal import EtfFlowSignalGenerator
from gold_miner.data.sentiment import SentimentDataFetcher
from gold_miner.signals.sentiment_signal import SentimentAnalyzer


# ---------------------------------------------------------------------------
# COT 模拟数据 — 实际部署时需接入 CFTC COT 报告
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CotReading:
    """COT 持仓读数."""
    non_commercial_net_long: int     # 投机净多头
    commercial_net_long: int         # 商业净多头
    retail_sentiment: str            # 散户情绪推断


class SentimentGuard:
    """情绪守卫 — 让你不被市场情绪左右，与大资金节奏一致."""

    def __init__(self) -> None:
        self._last_reading: SentimentReading | None = None

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def analyze(self) -> AdvisorReport:
        """分析当前市场情绪并生成对齐建议.

        Returns:
            AdvisorReport，report_type="sentiment"
        """
        logger.info("[SentimentGuard] 分析市场情绪...")

        reading = self._collect_reading()
        self._last_reading = reading

        warnings = []
        alignment_note = self._build_alignment_note(reading)

        # 极端情绪预警
        if reading.retail_extreme:
            warnings.append(
                f"⚠️ 散户情绪极端{reading.retail_sentiment}，"
                f"此时应保持冷静，{'逆向思考' if reading.retail_sentiment == 'greedy' else '不被恐慌左右'}"
            )

        # 与机构背离
        if self._is_divergence(reading):
            warnings.append(
                f"⚠️ 散户与机构方向背离 — 散户{reading.retail_sentiment}，"
                f"机构{reading.institutional_signal}。建议跟随机构节奏"
            )

        # ETF 流向
        if reading.etf_flow_signal == "outflow_strong":
            warnings.append("🔴 ETF 大幅净流出，机构可能在减持，考虑跟随减仓")
        elif reading.etf_flow_signal == "inflow_strong":
            warnings.append("🟢 ETF 大幅净流入，机构在增持，趋势可能延续")

        return AdvisorReport(
            report_type="sentiment",
            sentiment=reading,
            confidence=0.7,
            sources=["FearGreedIndex", "VIX", "ETFFlow", "COT(approx)"],
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # 数据采集
    # ------------------------------------------------------------------

    def _collect_reading(self) -> SentimentReading:
        """采集多维度情绪数据."""
        # 恐惧贪婪指数
        fear_greed = self._fetch_fear_greed()

        # VIX
        vix = self._fetch_vix()

        # ETF 资金流
        etf_signal = self._fetch_etf_flow()

        # 散户情绪判断
        retail_sent, retail_extreme = self._infer_retail_sentiment(fear_greed, vix)

        # 机构动向
        inst_signal = self._infer_institutional(etf_signal)

        # COT 持仓
        cot_pos = self._infer_cot()

        return SentimentReading(
            retail_sentiment=retail_sent,
            retail_extreme=retail_extreme,
            institutional_signal=inst_signal,
            cot_position=cot_pos,
            etf_flow_signal=etf_signal,
            fear_greed_index=fear_greed,
            vix_level=vix,
        )

    @staticmethod
    def _fetch_fear_greed() -> float | None:
        """获取恐惧贪婪指数."""
        try:
            macro = MacroDataFetcher()
            df = macro.fetch_fear_greed()
            if not df.empty:
                return float(df["value"].iloc[-1])
        except Exception as e:
            logger.debug(f"恐惧贪婪指数获取失败: {e}")
        return None

    @staticmethod
    def _fetch_vix() -> float | None:
        """获取 VIX 指数."""
        try:
            macro = MacroDataFetcher()
            df = macro.fetch_vix()
            if not df.empty:
                return float(df["value"].iloc[-1])
        except Exception as e:
            logger.debug(f"VIX获取失败: {e}")
        return None

    @staticmethod
    def _fetch_etf_flow() -> str:
        """获取 ETF 资金流向信号."""
        try:
            gen = EtfFlowSignalGenerator()
            signals = gen.generate_signals()
            # 从信号中提取方向
            for sig in signals:
                if sig.direction == "bearish" and sig.strength == "strong":
                    return "outflow_strong"
                if sig.direction == "bearish":
                    return "outflow"
                if sig.direction == "bullish" and sig.strength == "strong":
                    return "inflow_strong"
                if sig.direction == "bullish":
                    return "inflow"
            return "neutral"
        except Exception as e:
            logger.debug(f"ETF资金流获取失败: {e}")
            return "unknown"

    @staticmethod
    def _infer_retail_sentiment(
        fear_greed: float | None,
        vix: float | None,
    ) -> tuple[str, bool]:
        """推断散户情绪."""
        extreme = False
        sentiment = "neutral"

        if fear_greed is not None:
            if fear_greed >= 75:
                sentiment = "greedy"
                extreme = True
            elif fear_greed >= 55:
                sentiment = "optimistic"
            elif fear_greed <= 25:
                sentiment = "fearful"
                extreme = True
            elif fear_greed <= 45:
                sentiment = "pessimistic"

        # VIX 辅助判断
        if vix is not None:
            if vix >= 30:
                sentiment = "fearful"
                extreme = True
            elif vix <= 15 and sentiment not in ("greedy", "optimistic"):
                sentiment = "complacent"

        return sentiment, extreme

    @staticmethod
    def _infer_institutional(etf_flow: str) -> str:
        """推断机构动向."""
        mapping = {
            "inflow_strong": "buying",
            "inflow": "buying",
            "outflow_strong": "selling",
            "outflow": "selling",
            "neutral": "neutral",
        }
        return mapping.get(etf_flow, "unknown")

    @staticmethod
    def _infer_cot() -> str:
        """推断 COT 持仓方向（简化版）."""
        # TODO: 接入真实 COT 数据
        # 当前返回 neutral，实际应从 CFTC 周报解析
        return "neutral"

    # ------------------------------------------------------------------
    # 分析逻辑
    # ------------------------------------------------------------------

    @staticmethod
    def _build_alignment_note(reading: SentimentReading) -> str:
        """构建与市场节奏对齐的建议."""
        if reading.retail_extreme:
            if reading.retail_sentiment in ("greedy", "complacent"):
                return (
                    "散户贪婪/自满，往往是顶部信号。"
                    "机构如果同时在减持，应果断减仓。"
                    "记住：在别人贪婪时恐惧"
                )
            if reading.retail_sentiment == "fearful":
                return (
                    "散户恐慌，往往是底部信号。"
                    "如果机构在增持，这是难得的机会。"
                    "记住：在别人恐惧时贪婪"
                )

        if reading.institutional_signal == "buying":
            return "机构在买入，趋势大概率延续，可跟随"
        if reading.institutional_signal == "selling":
            return "机构在减持，警惕趋势反转，考虑减仓"

        return "市场情绪中性，按策略正常执行"

    @staticmethod
    def _is_divergence(reading: SentimentReading) -> bool:
        """判断散户与机构是否背离."""
        retail_bullish = reading.retail_sentiment in ("greedy", "optimistic", "complacent")
        retail_bearish = reading.retail_sentiment in ("fearful", "pessimistic")
        inst_buying = reading.institutional_signal == "buying"
        inst_selling = reading.institutional_signal == "selling"

        return (retail_bullish and inst_selling) or (retail_bearish and inst_buying)
