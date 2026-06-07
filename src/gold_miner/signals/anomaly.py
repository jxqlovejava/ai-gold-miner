"""异常信号检测 — 机构带节奏、异常交易量、信息源可信度."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd
from loguru import logger

from gold_miner.signals.base import SignalBundle


@dataclass
class AnomalyReport:
    anomaly_type: str  # news_manipulation | volume_anomaly | signal_divergence | source_unreliable
    severity: str  # high | medium | low
    description: str
    affected_signal_names: list[str] = field(default_factory=list)
    confidence: float = 0.5
    requires_human_review: bool = False
    detected_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)


class AnomalyDetector:
    """异常检测器 — 多维度交叉验证."""

    DIVERGENCE_THRESHOLD = 0.4  # 新闻 vs 技术面分歧阈值
    VOLUME_ZSCORE_THRESHOLD = 2.5  # 成交量异常 z-score
    VOLUME_SURGE_MULTIPLIER = 2.0  # 新闻量突增倍数

    def __init__(
        self,
        divergence_threshold: float = 0.4,
        volume_zscore_threshold: float = 2.5,
        volume_surge_multiplier: float = 2.0,
    ) -> None:
        self.divergence_threshold = divergence_threshold
        self.volume_zscore_threshold = volume_zscore_threshold
        self.volume_surge_multiplier = volume_surge_multiplier

    def detect_news_manipulation(
        self,
        bundle: SignalBundle,
        news_count: int = 0,
        news_avg_sentiment: float = 0.0,
    ) -> list[AnomalyReport]:
        """检测机构带节奏.

        核心逻辑: 新闻情感与技术面+基本面方向分歧过大 → 可能存在消息操控。
        """
        reports: list[AnomalyReport] = []

        news_sigs = bundle.by_dimension("news")
        if not news_sigs:
            return reports

        # 新闻平均方向
        news_dir = sum(s.score for s in news_sigs) / len(news_sigs)

        # 技术面+基本面平均方向
        tech_fund_sigs = bundle.by_dimension("technical") + bundle.by_dimension("fundamental")
        if not tech_fund_sigs:
            return reports
        tf_dir = sum(s.score for s in tech_fund_sigs) / len(tech_fund_sigs)

        # 分歧检测: 同向但幅度差异大，或反向
        divergence = abs(news_dir - tf_dir)
        if divergence > self.divergence_threshold:
            severity = "high" if divergence > 0.6 else "medium"
            reports.append(AnomalyReport(
                anomaly_type="news_manipulation",
                severity=severity,
                description=(
                    f"新闻情感({news_dir:+.2f})与技术面+基本面({tf_dir:+.2f})"
                    f"分歧 {divergence:.2f}，可能存在消息面操控"
                ),
                affected_signal_names=[s.name for s in news_sigs],
                confidence=min(divergence / 0.8, 1.0),
                requires_human_review=severity == "high",
                metadata={
                    "news_direction": round(news_dir, 3),
                    "tech_fund_direction": round(tf_dir, 3),
                    "divergence": round(divergence, 3),
                },
            ))

        # 新闻量突增检测
        if news_count > 8 and news_avg_sentiment != 0:
            # 大量新闻且方向单一 → 可能是集中推送
            bull_ratio = sum(1 for s in news_sigs if s.score > 0) / len(news_sigs)
            if bull_ratio > 0.75 or bull_ratio < 0.25:
                reports.append(AnomalyReport(
                    anomaly_type="news_manipulation",
                    severity="medium",
                    description=(
                        f"24h内{news_count}条新闻高度一致"
                        f"({'看涨' if bull_ratio > 0.5 else '看跌'}占比 {bull_ratio:.0%})，"
                        f"可能存在集中推送"
                    ),
                    affected_signal_names=[s.name for s in news_sigs],
                    confidence=min(bull_ratio, 1.0),
                    requires_human_review=True,
                    metadata={
                        "news_count": news_count,
                        "bull_ratio": round(bull_ratio, 3),
                    },
                ))

        return reports

    def detect_volume_anomalies(
        self,
        volume_series: pd.Series | None = None,
        price_change_pct: float = 0.0,
        volume_change_pct: float = 0.0,
    ) -> list[AnomalyReport]:
        """检测异常交易量."""
        reports: list[AnomalyReport] = []

        if volume_series is not None and len(volume_series) >= 20:
            mean = volume_series.tail(20).mean()
            std = volume_series.tail(20).std()
            latest = volume_series.iloc[-1]
            if std > 0:
                z_score = abs(latest - mean) / std
                if z_score > self.volume_zscore_threshold:
                    reports.append(AnomalyReport(
                        anomaly_type="volume_anomaly",
                        severity="high" if z_score > 3.5 else "medium",
                        description=(
                            f"成交量异常: z-score={z_score:.1f}，"
                            f"当前 {latest:.0f} vs 均值 {mean:.0f}"
                        ),
                        confidence=min(z_score / 5.0, 1.0),
                        requires_human_review=z_score > 3.5,
                        metadata={
                            "z_score": round(z_score, 2),
                            "latest_volume": float(latest),
                            "mean_volume": float(mean),
                        },
                    ))

        # 量价背离: 量增价不动(放量滞涨/放量不跌)
        if abs(volume_change_pct) > 0.2 and abs(price_change_pct) < 0.005:
            reports.append(AnomalyReport(
                anomaly_type="volume_anomaly",
                severity="medium",
                description=(
                    f"量价背离: 量变{volume_change_pct:+.1%}但价变仅{price_change_pct:+.2%}"
                ),
                confidence=0.6,
                requires_human_review=False,
            ))

        return reports

    def detect_signal_divergence(
        self, bundle: SignalBundle
    ) -> list[AnomalyReport]:
        """检测跨维度信号冲突 — 多空方向严重不一致."""
        reports: list[AnomalyReport] = []
        bull = bundle.bullish_count()
        bear = bundle.bearish_count()
        total = bull + bear
        if total < 4:
            return reports

        if bull > 0 and bear > 0:
            ratio = min(bull, bear) / max(bull, bear)
            if ratio > 0.6:  # 多空接近均衡 → 市场高度不确定
                reports.append(AnomalyReport(
                    anomaly_type="signal_divergence",
                    severity="medium",
                    description=(
                        f"多空信号接近均衡: 看涨{bull} vs 看跌{bear}，"
                        f"市场方向高度不确定"
                    ),
                    confidence=ratio,
                    requires_human_review=False,
                ))

        return reports

    def run_all(
        self,
        bundle: SignalBundle,
        news_count: int = 0,
        news_avg_sentiment: float = 0.0,
        volume_series: pd.Series | None = None,
        price_change_pct: float = 0.0,
        volume_change_pct: float = 0.0,
    ) -> list[AnomalyReport]:
        """执行所有异常检测."""
        reports: list[AnomalyReport] = []
        reports.extend(self.detect_news_manipulation(bundle, news_count, news_avg_sentiment))
        reports.extend(self.detect_volume_anomalies(volume_series, price_change_pct, volume_change_pct))
        reports.extend(self.detect_signal_divergence(bundle))
        logger.info(f"异常检测: {len(reports)}个异常")
        return sorted(reports, key=lambda r: {"high": 0, "medium": 1, "low": 2}[r.severity])
