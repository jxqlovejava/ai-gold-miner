"""价格预警 — 大波动、关键位突破、DXY异动检测."""

from dataclasses import dataclass, field

import pandas as pd
from loguru import logger

from gold_miner.config import settings
from gold_miner.execution.notifier import Notifier


@dataclass
class Alert:
    name: str
    message: str
    severity: str  # high / medium / info


class PriceAlert:
    """价格预警器 — 检测异常行情并推送通知."""

    def __init__(self) -> None:
        self.notifier = Notifier()
        self.alerts: list[Alert] = []

    def check_all(
        self,
        gold_df: pd.DataFrame,
        dxy_df: pd.DataFrame | None = None,
        silver_price: float | None = None,
    ) -> list[Alert]:
        """执行所有预警检查."""
        self.alerts = []

        if gold_df.empty:
            return self.alerts

        self._check_big_move(gold_df)
        self._check_key_level_break(gold_df)

        if dxy_df is not None and not dxy_df.empty:
            self._check_dxy_move(dxy_df)

        if silver_price is not None and silver_price > 0:
            self._check_gold_silver_ratio(gold_df, silver_price)

        # 发送通知
        if self.alerts and self.notifier.enabled:
            self._send_alerts()

        return self.alerts

    def _check_big_move(self, gold_df: pd.DataFrame) -> None:
        """检测大波动: 当日涨跌幅 > 阈值."""
        if len(gold_df) < 2:
            return

        latest = gold_df["close"].iloc[-1]
        prev = gold_df["close"].iloc[-2]
        change_pct = (latest - prev) / prev * 100

        if abs(change_pct) >= settings.alert_big_move_pct:
            direction = "上涨" if change_pct > 0 else "下跌"
            self.alerts.append(Alert(
                name="大波动",
                message=f"黄金{direction} {abs(change_pct):.2f}%，当前 ${latest:.2f}",
                severity="high",
            ))
            logger.warning(f"价格预警: {self.alerts[-1].message}")

    def _check_key_level_break(self, gold_df: pd.DataFrame) -> None:
        """检测关键位突破: 价格突破N日最高/最低."""
        lookback = settings.alert_key_level_lookback
        if len(gold_df) < lookback:
            return

        window = gold_df.iloc[-(lookback + 1):-1]
        latest = gold_df["close"].iloc[-1]
        high_n = window["high"].max()
        low_n = window["low"].min()

        if latest > high_n:
            self.alerts.append(Alert(
                name="关键位突破",
                message=f"金价突破{lookback}日最高 ${high_n:.2f}，当前 ${latest:.2f}",
                severity="medium",
            ))
        elif latest < low_n:
            self.alerts.append(Alert(
                name="关键位突破",
                message=f"金价跌破{lookback}日最低 ${low_n:.2f}，当前 ${latest:.2f}",
                severity="medium",
            ))

    def _check_dxy_move(self, dxy_df: pd.DataFrame) -> None:
        """检测美元指数异动."""
        if len(dxy_df) < 2:
            return

        latest = dxy_df["value"].iloc[-1]
        prev = dxy_df["value"].iloc[-2]
        change_pct = (latest - prev) / prev * 100

        if abs(change_pct) >= settings.alert_dxy_move_pct:
            direction = "走强" if change_pct > 0 else "走弱"
            self.alerts.append(Alert(
                name="DXY异动",
                message=f"美元指数{direction} {abs(change_pct):.2f}%，当前 {latest:.2f}",
                severity="medium",
            ))

    def _check_gold_silver_ratio(
        self, gold_df: pd.DataFrame, silver_price: float
    ) -> None:
        """检测金银比极值."""
        gold_price = gold_df["close"].iloc[-1]
        ratio = gold_price / silver_price

        if ratio >= settings.alert_gold_silver_ratio_high:
            self.alerts.append(Alert(
                name="金银比极值",
                message=f"金银比 {ratio:.1f} > {settings.alert_gold_silver_ratio_high}，避险情绪极端",
                severity="high",
            ))
        elif ratio <= settings.alert_gold_silver_ratio_low:
            self.alerts.append(Alert(
                name="金银比低位",
                message=f"金银比 {ratio:.1f} < {settings.alert_gold_silver_ratio_low}，风险偏好极高",
                severity="medium",
            ))

    def _send_alerts(self) -> None:
        """批量发送预警通知."""
        if not self.alerts:
            return

        lines = ["⚠️ 黄金预警通知", ""]
        for a in self.alerts:
            icon = "🔴" if a.severity == "high" else "🟡"
            lines.append(f"{icon} [{a.name}] {a.message}")

        self.notifier.send("\n".join(lines))
