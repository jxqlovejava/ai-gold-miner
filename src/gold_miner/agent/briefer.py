"""简报生成器 — 盘前/开盘/尾盘/事件扫描."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd
from loguru import logger


@dataclass
class Briefing:
    """统一简报数据模型."""
    kind: str  # pre_market / post_open / closing / event_scan
    generated_at: datetime = field(default_factory=datetime.now)
    # 金价
    international_price: float = 0.0
    international_change_pct: float = 0.0
    domestic_price: float = 0.0
    domestic_change_pct: float = 0.0
    # 信号
    signal_direction: str = "neutral"
    composite_score: float = 0.0
    # 事件
    today_events: list[str] = field(default_factory=list)
    upcoming_events: list[str] = field(default_factory=list)
    # 建议
    action: str = "hold"
    position_advice: str = ""
    risk_warnings: list[str] = field(default_factory=list)
    # 持仓
    portfolio_summary: str = ""
    rule_violations: list[str] = field(default_factory=list)
    # 额外数据
    raw: dict[str, Any] = field(default_factory=dict)

    def to_markdown(self) -> str:
        """转为企业微信/通知用的 Markdown."""
        now = self.generated_at.strftime("%m/%d %H:%M")
        emoji = {"pre_market": "🌅", "post_open": "📈", "closing": "🌇", "event_scan": "📅"}
        e = emoji.get(self.kind, "📊")
        labels = {"pre_market": "盘前简报", "post_open": "开盘分析", "closing": "尾盘提醒", "event_scan": "事件扫描"}

        lines = [f"{e} **{labels.get(self.kind, self.kind)}** {now}"]
        lines.append("")
        lines.append(f"国际: ${self.international_price:.1f} ({self.international_change_pct:+.2f}%)")
        lines.append(f"国内: ¥{self.domestic_price:.2f} ({self.domestic_change_pct:+.2f}%)")

        if self.composite_score != 0:
            direction = "偏多" if self.composite_score > 0.15 else ("偏空" if self.composite_score < -0.15 else "中性")
            lines.append(f"信号: {direction} (综合评分{self.composite_score:+.2f})")

        if self.today_events:
            lines.append(f"\n📌 今日: {', '.join(self.today_events)}")

        if self.portfolio_summary:
            lines.append(f"\n{self.portfolio_summary}")

        if self.rule_violations:
            lines.append(f"\n⚠️ 军规: {'; '.join(self.rule_violations)}")

        if self.action != "hold":
            lines.append(f"\n🔔 建议: **{self.action}**")
        if self.position_advice:
            lines.append(f"{self.position_advice}")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "generated_at": self.generated_at.isoformat(),
            "international_price": self.international_price,
            "international_change_pct": self.international_change_pct,
            "domestic_price": self.domestic_price,
            "domestic_change_pct": self.domestic_change_pct,
            "signal_direction": self.signal_direction,
            "composite_score": self.composite_score,
            "today_events": self.today_events,
            "upcoming_events": self.upcoming_events,
            "action": self.action,
            "position_advice": self.position_advice,
            "risk_warnings": self.risk_warnings,
            "portfolio_summary": self.portfolio_summary,
            "rule_violations": self.rule_violations,
        }


class Briefer:
    """简报生成器 — 拉数据、跑管线、生成结构化简报."""

    def __init__(self) -> None:
        self._last_briefing: Briefing | None = None

    # ------------------------------------------------------------------
    # 四个核心简报入口
    # ------------------------------------------------------------------

    def pre_market(self) -> Briefing:
        """盘前简报: 隔夜国际金价 + 今日关键事件 + 方向预判."""
        logger.info("生成盘前简报...")
        price_intl, intl_chg = self._fetch_international()
        price_dom, dom_chg = self._fetch_domestic()
        events_today = self._get_today_events()
        events_week = self._get_upcoming_events(7)

        b = Briefing(
            kind="pre_market",
            international_price=price_intl,
            international_change_pct=intl_chg,
            domestic_price=price_dom,
            domestic_change_pct=dom_chg,
            today_events=events_today,
            upcoming_events=events_week[:5],
        )

        # 基于隔夜走势预判开盘方向
        if intl_chg < -1.0:
            b.signal_direction = "bearish"
            b.action = "观望，等开盘消化隔夜利空"
        elif intl_chg > 1.0:
            b.signal_direction = "bullish"
            b.action = "关注高开，不追涨"
        else:
            b.signal_direction = "neutral"

        self._last_briefing = b
        return b

    def post_open(self) -> Briefing:
        """开盘分析: 开盘价 vs 昨收 + 信号刷新 + 操作建议."""
        logger.info("生成开盘分析...")
        price_dom, dom_chg = self._fetch_domestic()
        price_intl, intl_chg = self._fetch_international()

        # 尝试跑完整信号管线
        score = 0.0
        action = "hold"
        try:
            from gold_miner.advisor.orchestrator import Advisor
            from gold_miner.agent.portfolio import PortfolioTracker

            advisor = Advisor()
            portfolio = PortfolioTracker()
            snap = portfolio.snapshot(price_dom)
            position_pct = snap.gold_allocation_pct / 100

            report = advisor.daily_guide(
                current_position_pct=position_pct,
                avg_cost=snap.positions[0].avg_cost if snap.positions else 0.0,
            )
            if report.instruction:
                score = getattr(report.instruction, "score", 0.0) or 0.0
                raw_action = getattr(report.instruction, "action", "hold") or "hold"
                action = raw_action if raw_action in ("buy", "sell", "hold") else "hold"
        except Exception as e:
            logger.warning(f"完整管线运行失败，使用简化模式: {e}")

        b = Briefing(
            kind="post_open",
            domestic_price=price_dom,
            domestic_change_pct=dom_chg,
            international_price=price_intl,
            international_change_pct=intl_chg,
            composite_score=score,
            action=action,
        )
        if score > 0.15:
            b.signal_direction = "bullish"
        elif score < -0.15:
            b.signal_direction = "bearish"

        try:
            portfolio = PortfolioTracker()
            b.portfolio_summary = portfolio.risk_summary(price_dom)
            b.rule_violations = portfolio.check_rules(price_dom)
        except Exception:
            pass

        self._last_briefing = b
        return b

    def closing(self) -> Briefing:
        """尾盘提醒: 日内走势 + 持仓检查 + 隔夜风险提示."""
        logger.info("生成尾盘提醒...")
        price_dom, dom_chg = self._fetch_domestic()
        price_intl, intl_chg = self._fetch_international()

        b = Briefing(
            kind="closing",
            domestic_price=price_dom,
            domestic_change_pct=dom_chg,
            international_price=price_intl,
            international_change_pct=intl_chg,
        )

        try:
            portfolio = PortfolioTracker()
            b.portfolio_summary = portfolio.risk_summary(price_dom)
            violations = portfolio.check_rules(price_dom)
            b.rule_violations = violations
            if violations:
                b.action = "检查军规违规项"
        except Exception:
            pass

        # 隔夜风险提示
        if abs(dom_chg) > 2.0:
            b.risk_warnings.append("今日波动>2%，隔夜风险较大")

        self._last_briefing = b
        return b

    def event_scan(self, days_ahead: int = 7) -> Briefing:
        """事件扫描: 未来N天关键事件 + 历史影响预测."""
        logger.info(f"扫描未来{days_ahead}天事件...")
        price_intl, intl_chg = self._fetch_international()
        price_dom, dom_chg = self._fetch_domestic()
        events = self._get_upcoming_events(days_ahead)

        b = Briefing(
            kind="event_scan",
            international_price=price_intl,
            international_change_pct=intl_chg,
            domestic_price=price_dom,
            domestic_change_pct=dom_chg,
            upcoming_events=events,
        )

        if events:
            b.position_advice = f"未来{days_ahead}天有{len(events)}个关键事件，注意仓位管理"

        self._last_briefing = b
        return b

    @property
    def latest(self) -> Briefing | None:
        return self._last_briefing

    # ------------------------------------------------------------------
    # 数据获取 (轻量，不依赖pipeline)
    # ------------------------------------------------------------------

    @staticmethod
    def _fetch_domestic() -> tuple[float, float]:
        try:
            import httpx
            from bs4 import BeautifulSoup
            resp = httpx.get("https://www.jinjia.com.cn/", timeout=10)
            soup = BeautifulSoup(resp.text, "html.parser")
            for li in soup.find_all("li"):
                name_div = li.find("div", class_="name")
                if name_div and "AU9999" in name_div.get_text():
                    new_div = li.find("div", class_="new")
                    rise_div = li.find("div", class_="rise")
                    if new_div:
                        price = float(new_div.get_text(strip=True))
                        chg_text = rise_div.get_text(strip=True) if rise_div else "0"
                        chg = float(chg_text.replace("%", "").replace("+", "")) / 100
                        return price, chg
        except Exception:
            pass
        # fallback: AKShare
        try:
            import akshare as ak
            df = ak.spot_hist_sge(symbol="Au99.99")
            if not df.empty:
                latest = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else latest
                price = float(latest["close"])
                chg = (price - float(prev["close"])) / float(prev["close"]) * 100
                return price, chg
        except Exception as e:
            logger.error(f"国内金价获取失败: {e}")
        return 0.0, 0.0

    @staticmethod
    def _fetch_international() -> tuple[float, float]:
        try:
            import httpx
            from bs4 import BeautifulSoup
            resp = httpx.get("https://www.jinjia.com.cn/gjgold/", timeout=10)
            soup = BeautifulSoup(resp.text, "html.parser")
            for li in soup.find_all("li"):
                name_div = li.find("div", class_="name")
                if name_div and "伦敦金" in name_div.get_text():
                    new_div = li.find("div", class_="new")
                    rise_div = li.find("div", class_="rise")
                    if new_div:
                        price = float(new_div.get_text(strip=True))
                        chg_text = rise_div.get_text(strip=True) if rise_div else "0"
                        chg = float(chg_text.replace("%", "").replace("+", "")) / 100
                        return price, chg
        except Exception:
            pass
        return 0.0, 0.0

    @staticmethod
    def _get_today_events() -> list[str]:
        try:
            from gold_miner.data.calendar import EventCalendar
            cal = EventCalendar()
            cal.load_fixed_calendar()
            today_events = cal.get_today()
            return [f"{e.name}" for e in today_events]
        except Exception:
            return []

    @staticmethod
    def _get_upcoming_events(days: int = 7) -> list[str]:
        try:
            from gold_miner.data.calendar import EventCalendar
            cal = EventCalendar()
            cal.load_fixed_calendar()
            upcoming = cal.get_upcoming(days=days)
            return [
                f"{e.scheduled_at.strftime('%m/%d')} {e.name}"
                for e in upcoming[:10]
            ]
        except Exception:
            return []
