"""情景分析存储 — JSONL 持久化."""

from __future__ import annotations

from pathlib import Path

from gold_miner.scenarios.models import ScenarioReport
from gold_miner.storage import get_store


class ScenarioStore:
    """情景分析报告的 JSONL 持久化存储."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self._store = get_store(private_data_dir=data_dir)

    def save(self, report: ScenarioReport) -> None:
        """保存情景分析报告."""
        record = _report_to_dict(report)
        self._store.append_scenario(record)

    def load(self, report_id: str) -> ScenarioReport | None:
        """按ID加载单个报告."""
        for data in self._store.load_scenarios():
            if data.get("id") == report_id:
                return _dict_to_report(data)
        return None

    def list_all(self, limit: int = 20) -> list[ScenarioReport]:
        """列出最近的报告（最新在前）."""
        reports: list[ScenarioReport] = []
        for data in reversed(self._store.load_scenarios()):
            try:
                reports.append(_dict_to_report(data))
            except (KeyError, ValueError):
                continue
            if len(reports) >= limit:
                break
        return reports


# ------------------------------------------------------------------
# 序列化辅助
# ------------------------------------------------------------------

def _report_to_dict(report: ScenarioReport) -> dict:
    d: dict = {
        "id": report.id,
        "created_at": report.created_at.isoformat(),
        "scenario_description": report.scenario_description,
        "time_horizon_months": report.time_horizon_months,
        "context_snapshot": report.context_snapshot,
        "trigger_conditions": report.trigger_conditions,
        "transmission_channels": [
            {
                "channel": c.channel,
                "direction": c.direction,
                "magnitude": c.magnitude,
                "description": c.description,
                "timeframe": c.timeframe,
            }
            for c in report.transmission_channels
        ],
        "historical_analogs": [
            {
                "event_name": a.event_name,
                "period": a.period,
                "gold_price_change_pct": a.gold_price_change_pct,
                "similarity_score": a.similarity_score,
                "key_parallels": a.key_parallels,
                "key_differences": a.key_differences,
            }
            for a in report.historical_analogs
        ],
        "price_impact": (
            {
                "direction": report.price_impact.direction,
                "base_case_change_pct": report.price_impact.base_case_change_pct,
                "bullish_case_change_pct": report.price_impact.bullish_case_change_pct,
                "bearish_case_change_pct": report.price_impact.bearish_case_change_pct,
                "peak_impact_months": report.price_impact.peak_impact_months,
                "confidence": report.price_impact.confidence,
                "reasoning": report.price_impact.reasoning,
            }
            if report.price_impact
            else None
        ),
        "key_levels": report.key_levels,
        "probability_assessment": report.probability_assessment,
        "strategy": (
            {
                "overall_position": report.strategy.overall_position,
                "spot_gold_action": report.strategy.spot_gold_action,
                "accumulation_gold_action": report.strategy.accumulation_gold_action,
                "suggested_entry_zones": report.strategy.suggested_entry_zones,
                "suggested_exit_zones": report.strategy.suggested_exit_zones,
                "hedging_suggestions": report.strategy.hedging_suggestions,
                "position_sizing": report.strategy.position_sizing,
                "rebalancing_frequency": report.strategy.rebalancing_frequency,
            }
            if report.strategy
            else None
        ),
        "risk_factors": report.risk_factors,
        "monitoring_indicators": report.monitoring_indicators,
        "prediction_id": report.prediction_id,
    }
    return d


def _dict_to_report(data: dict) -> ScenarioReport:
    from datetime import datetime

    from gold_miner.scenarios.models import (
        HistoricalAnalog,
        ImpactChannel,
        PriceImpactEstimate,
        StrategyRecommendation,
    )

    channels = [
        ImpactChannel(
            channel=c.get("channel", ""),
            direction=c.get("direction", "neutral"),
            magnitude=c.get("magnitude", "moderate"),
            description=c.get("description", ""),
            timeframe=c.get("timeframe", "medium-term"),
        )
        for c in data.get("transmission_channels", [])
    ]

    analogs = [
        HistoricalAnalog(
            event_name=a.get("event_name", ""),
            period=a.get("period", ""),
            gold_price_change_pct=float(a.get("gold_price_change_pct", 0)),
            similarity_score=float(a.get("similarity_score", 0.5)),
            key_parallels=a.get("key_parallels", []),
            key_differences=a.get("key_differences", []),
        )
        for a in data.get("historical_analogs", [])
    ]

    pi_data = data.get("price_impact")
    price_impact = None
    if pi_data:
        price_impact = PriceImpactEstimate(
            direction=pi_data.get("direction", "neutral"),
            base_case_change_pct=float(pi_data.get("base_case_change_pct", 0)),
            bullish_case_change_pct=float(pi_data.get("bullish_case_change_pct", 0)),
            bearish_case_change_pct=float(pi_data.get("bearish_case_change_pct", 0)),
            peak_impact_months=int(pi_data.get("peak_impact_months", 0)),
            confidence=float(pi_data.get("confidence", 0.5)),
            reasoning=pi_data.get("reasoning", ""),
        )

    strat_data = data.get("strategy")
    strategy = None
    if strat_data:
        strategy = StrategyRecommendation(
            overall_position=strat_data.get("overall_position", "观望"),
            spot_gold_action=strat_data.get("spot_gold_action", ""),
            accumulation_gold_action=strat_data.get("accumulation_gold_action", ""),
            suggested_entry_zones=[float(z) for z in strat_data.get("suggested_entry_zones", [])],
            suggested_exit_zones=[float(z) for z in strat_data.get("suggested_exit_zones", [])],
            hedging_suggestions=strat_data.get("hedging_suggestions", []),
            position_sizing=strat_data.get("position_sizing", ""),
            rebalancing_frequency=strat_data.get("rebalancing_frequency", ""),
        )

    return ScenarioReport(
        id=data.get("id", ""),
        created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(),
        scenario_description=data.get("scenario_description", ""),
        time_horizon_months=int(data.get("time_horizon_months", 12)),
        context_snapshot=data.get("context_snapshot", {}),
        trigger_conditions=data.get("trigger_conditions", []),
        transmission_channels=channels,
        historical_analogs=analogs,
        price_impact=price_impact,
        key_levels=[float(k) for k in data.get("key_levels", [])],
        probability_assessment=data.get("probability_assessment", ""),
        strategy=strategy,
        risk_factors=data.get("risk_factors", []),
        monitoring_indicators=data.get("monitoring_indicators", []),
        prediction_id=data.get("prediction_id"),
    )
