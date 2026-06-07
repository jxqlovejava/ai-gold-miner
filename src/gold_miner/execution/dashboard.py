"""决策仪表盘 — 格式化输出交易决策."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from gold_miner.signals.base import SignalBundle


@dataclass
class TradeDecision:
    signal: str
    instrument: str
    position_pct: float
    entry_price: float
    entry_range: tuple[float, float] = (0.0, 0.0)
    stop_loss: float = 0.0
    take_profit: float = 0.0
    trailing_stop: bool = False
    score_details: dict[str, float] = field(default_factory=dict)
    risk_assessment: dict[str, Any] = field(default_factory=dict)
    action_list: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)


class DashboardFormatter:
    @classmethod
    def format(cls, decision: TradeDecision) -> str:
        lines = [
            "=" * 50,
            "           黄金投资决策仪表盘",
            "=" * 50,
            "",
            f"  信号: {'买入' if decision.signal == 'buy' else '卖出' if decision.signal == 'sell' else '持有'}",
            f"  标的: {decision.instrument}",
            f"  仓位: {decision.position_pct:.0%}",
            "",
            f"  入场价: {decision.entry_price:.2f}",
            f"  建议区间: {decision.entry_range[0]:.2f} ~ {decision.entry_range[1]:.2f}",
            "",
            f"  止损位: {decision.stop_loss:.2f}",
            f"  止盈位: {decision.take_profit:.2f}",
            f"  移动止损: {'是' if decision.trailing_stop else '否'}",
            "",
            "-" * 50,
            "  多因子评分详情:",
        ]

        for dim, score in decision.score_details.items():
            bar = cls._score_bar(score)
            lines.append(f"    {dim:10s}: {score:+.2f} {bar}")

        lines.extend(["-" * 50, "  风险评估:"])
        for key, value in decision.risk_assessment.items():
            lines.append(f"    {key}: {value}")

        if decision.action_list:
            lines.extend(["-" * 50, "  操作清单:"])
            for i, action in enumerate(decision.action_list, 1):
                lines.append(f"    {i}. {action}")

        lines.extend(["", f"  生成时间: {decision.timestamp.strftime('%Y-%m-%d %H:%M:%S')}", "=" * 50])
        return "\n".join(lines)

    @classmethod
    def _score_bar(cls, score: float, width: int = 20) -> str:
        pos = int((score + 1) / 2 * width)
        pos = max(0, min(width, pos))
        bar = ["-"] * width
        bar[width // 2] = "|"
        if pos != width // 2:
            bar[pos] = "*"
        return f"[{''.join(bar)}]"

    @classmethod
    def from_analysis(
        cls,
        signal_bundle: SignalBundle,
        portfolio_decision: dict[str, Any],
        instrument: str = "现货黄金",
        current_price: float = 0.0,
    ) -> TradeDecision:
        direction = portfolio_decision.get("direction", "neutral")
        signal_map = {"long": "buy", "short": "sell", "neutral": "hold"}

        score_details: dict[str, float] = {}
        for dim in ["technical", "fundamental", "news", "sentiment", "event", "polymarket", "anomaly", "scenario"]:
            signals = signal_bundle.by_dimension(dim)
            if signals:
                score_details[dim] = round(sum(s.score for s in signals) / len(signals), 2)
            else:
                score_details[dim] = 0.0

        position_pct = portfolio_decision.get("position_pct", 0)
        stop_loss = 0.0
        take_profit = 0.0
        if direction == "long" and current_price > 0:
            stop_loss = current_price * 0.97
            take_profit = current_price * 1.06
        elif direction == "short" and current_price > 0:
            stop_loss = current_price * 1.03
            take_profit = current_price * 0.94

        action_list: list[str] = []
        if direction == "long":
            action_list.append(f"以 {current_price:.2f} 附近开仓买入")
            action_list.append(f"设置止损 {stop_loss:.2f}")
            action_list.append(f"设置止盈 {take_profit:.2f}")
        elif direction == "short":
            action_list.append(f"以 {current_price:.2f} 附近平仓或做空")
            action_list.append(f"设置止损 {stop_loss:.2f}")
        else:
            action_list.append("维持当前仓位，等待更明确信号")

        return TradeDecision(
            signal=signal_map.get(direction, "hold"),
            instrument=instrument,
            position_pct=position_pct,
            entry_price=current_price,
            entry_range=(current_price * 0.995, current_price * 1.005),
            stop_loss=stop_loss,
            take_profit=take_profit,
            score_details=score_details,
            risk_assessment={
                "最大回撤预期": f"{position_pct * 3:.1f}%",
                "综合评分": f"{signal_bundle.composite_score:+.2f}",
                "置信度": f"{signal_bundle.confidence:.0%}",
            },
            action_list=action_list,
        )

    @staticmethod
    def display_strategy_comparison(
        comparison: Any,  # StrategyComparison
    ) -> None:
        """展示策略对比表."""
        if comparison is None or not hasattr(comparison, "results"):
            return

        print(f"\n{'='*60}")
        print("  \U0001f3af 多目标策略对比")
        print(f"{'='*60}")

        for name, dec in comparison.results.items():
            if dec.position_pct > 0:
                active = "✔" if comparison.recommended and comparison.recommended.value == name else "  "
                bar = "█" * int(dec.position_pct * 20)
                print(
                    f"  [{active}] {name:18s} | 仓位 {dec.position_pct:.0%} {bar} "
                    f"| 止损 {dec.stop_loss:.1f}"
                )
                if dec.take_profit_levels:
                    tps = ", ".join(f"{tp:.0f}" for tp in dec.take_profit_levels)
                    print(f"      止盈: {tps}")
            else:
                inactive = "✘" if dec.reason else "  "
                print(f"  [{inactive}] {name:18s} | 未激活 ({dec.reason[:40]})")

        if comparison.recommended:
            print(f"\n  ➡ 推荐: {comparison.recommended.value}")

    @staticmethod
    def display_anomalies(anomaly_reports: list[Any]) -> None:
        """展示异常检测结果."""
        if not anomaly_reports:
            return

        print(f"\n{'='*60}")
        print("  \U0001f6a8 异常信号检测")
        print(f"{'='*60}")

        for r in anomaly_reports[:5]:
            sev_icon = {"high": "\U0001f534", "medium": "\U0001f7e1", "low": "\U0001f7e2"}.get(
                getattr(r, "severity", "low"), "⚪"
            )
            review = " [需人工]" if getattr(r, "requires_human_review", False) else ""
            print(
                f"  {sev_icon} [{getattr(r, 'severity', 'low')}] "
                f"{getattr(r, 'anomaly_type', 'unknown')}{review}"
            )
            print(f"     {getattr(r, 'description', '')[:80]}")

    @staticmethod
    def display_scenarios(scenarios: list[Any]) -> None:
        """展示极端情景分析."""
        if not scenarios:
            return

        print(f"\n{'='*60}")
        print("  \U0001f30d 极端情景分析")
        print(f"{'='*60}")

        for s in sorted(scenarios, key=lambda x: getattr(x, "confidence", 0), reverse=True)[:5]:
            conf = getattr(s, "confidence", 0)
            if conf < 0.05:
                continue
            bar = "█" * int(conf * 20)
            direction = "\U0001f7e2" if getattr(s, "gold_direction", "").value == "bullish" else "\U0001f534"
            impact = getattr(s, "gold_impact_score", 0)
            btc_impact = getattr(s, "btc_impact_score", 0)
            btc_dir = getattr(s, "btc_direction", "")
            btc_icon = "\U0001f7e1" if btc_dir in ("bullish", "crash_then_pump") else "\U0001f7e0" if btc_dir == "bearish" else "\U000026ab"
            print(
                f"  {direction} {getattr(s, 'name', ''):24s} | "
                f"概率 {conf:.0%} {bar} | 金{impact:+.0%} {btc_icon}BTC{btc_impact:+.0%}"
            )
            desc = getattr(s, "description", "")
            if desc:
                print(f"     {desc[:80]}")
            btc_note = getattr(s, "btc_note", "")
            if btc_note:
                print(f"     BTC: {btc_note[:70]}")

    @staticmethod
    def display_event_calendar(events: list[Any]) -> None:
        """展示即将到来的重要事件."""
        if not events:
            return

        print(f"\n{'='*60}")
        print("  \U0001f4c5 重要事件日历")
        print(f"{'='*60}")

        for e in events[:5]:
            impact_icon = {"high": "\U0001f534", "medium": "\U0001f7e1", "low": "⚪"}.get(
                getattr(e, "impact", None) and getattr(e, "impact", "low").value, "⚪"
            )
            scheduled = getattr(e, "scheduled_at", None)
            time_str = scheduled.strftime("%m-%d %H:%M") if scheduled else "待定"
            print(f"  {impact_icon} {time_str}  {getattr(e, 'name', '')}")
