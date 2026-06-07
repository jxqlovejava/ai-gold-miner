"""情景分析存储 — JSONL 持久化."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from gold_miner.config import settings
from gold_miner.scenarios.models import ScenarioReport


class ScenarioStore:
    """情景分析报告的 JSONL 持久化存储."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else settings.data_path
        self.file_path = self.data_dir / "scenarios.jsonl"

    def save(self, report: ScenarioReport) -> None:
        """保存情景分析报告."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        record = _report_to_dict(report)
        with open(self.file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def load(self, report_id: str) -> ScenarioReport | None:
        """按ID加载单个报告."""
        if not self.file_path.exists():
            return None
        for line in _read_lines(self.file_path):
            try:
                data = json.loads(line)
                if data.get("id") == report_id:
                    return _dict_to_report(data)
            except json.JSONDecodeError:
                continue
        return None

    def list_all(self, limit: int = 20) -> list[ScenarioReport]:
        """列出最近的报告（最新在前）."""
        if not self.file_path.exists():
            return []
        reports: list[ScenarioReport] = []
        for line in _read_lines_reverse(self.file_path, limit):
            try:
                data = json.loads(line)
                reports.append(_dict_to_report(data))
            except json.JSONDecodeError:
                continue
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


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").strip().split("\n")


def _read_lines_reverse(path: Path, limit: int) -> list[str]:
    """读取JSONL文件最后N行（高效，不加载全文件到内存）."""
    if not path.exists():
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return []

            buf_size = 8192
            lines: list[str] = []
            pos = size
            carry = b""

            while pos > 0 and len(lines) < limit:
                read_size = min(buf_size, pos)
                pos -= read_size
                f.seek(pos)
                chunk = f.read(read_size)
                parts = (chunk + carry).split(b"\n")
                if pos > 0:
                    carry = parts[0]
                    parts = parts[1:]
                else:
                    carry = b""
                decoded = [p.decode("utf-8", errors="replace").strip() for p in reversed(parts)]
                lines.extend(p for p in decoded if p)
                if carry:
                    lines.append(carry.decode("utf-8", errors="replace").strip())

            return [l for l in lines if l][:limit]
    except Exception:
        return []
