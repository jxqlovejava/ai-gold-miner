"""情景分析器 — LLM驱动的极端事件影响推演."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from gold_miner.config import settings
from gold_miner.llm.client import LLMClient
from gold_miner.scenarios.models import (
    HistoricalAnalog,
    ImpactChannel,
    PriceImpactEstimate,
    ScenarioReport,
    StrategyRecommendation,
)


class ScenarioAnalyzer:
    """使用LLM分析极端未来事件对黄金价格的影响."""

    def __init__(self) -> None:
        self.llm = LLMClient()

    def analyze(
        self,
        scenario_description: str,
        time_horizon_months: int = 12,
        context: dict[str, Any] | None = None,
    ) -> ScenarioReport:
        """分析情景并返回完整报告."""
        import uuid

        report_id = uuid.uuid4().hex[:12]
        context = context or {}

        prompt = self._build_prompt(scenario_description, time_horizon_months, context)
        result = self._call_llm(prompt)

        if result is None:
            return self._fallback_report(report_id, scenario_description, time_horizon_months, context)

        return self._parse_response(report_id, scenario_description, time_horizon_months, context, result)

    # ------------------------------------------------------------------
    # Prompt 构建
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        description: str,
        horizon: int,
        context: dict[str, Any],
    ) -> str:
        context_block = ""
        if context:
            lines = ["## 当前市场背景"]
            if context.get("spot_gold"):
                lines.append(f"- 现货黄金: ${context['spot_gold']:.2f}/oz")
            if context.get("dxy"):
                lines.append(f"- 美元指数(DXY): {context['dxy']:.2f}")
            if context.get("real_rate") is not None:
                lines.append(f"- 美国实际利率(TIPS): {context['real_rate']:.2f}%")
            if context.get("breakeven") is not None:
                lines.append(f"- 通胀预期(盈亏平衡): {context['breakeven']:.2f}%")
            if context.get("silver"):
                lines.append(f"- 白银: ${context['silver']:.2f}/oz")
            if context.get("gold_silver_ratio"):
                lines.append(f"- 金银比: {context['gold_silver_ratio']:.1f}")
            lines.append("")
            context_block = "\n".join(lines)

        return f"""你是一位资深宏观经济与贵金属投资策略师。请针对以下假设性极端事件，系统推演其对黄金价格的影响。

{context_block}
## 假设情景
{description}

## 分析时间窗口
未来 {horizon} 个月

## 分析框架
请按以下结构输出JSON（不要包含其他文字）：

```json
{{
  "trigger_conditions": ["触发条件1", "触发条件2"],
  "transmission_channels": [
    {{
      "channel": "利率/美元/避险/通胀/央行行为/市场流动性/地缘政治/其他",
      "direction": "bullish / bearish",
      "magnitude": "strong / moderate / weak",
      "description": "传导逻辑说明，80字以内",
      "timeframe": "immediate / short-term / medium-term / long-term"
    }}
  ],
  "historical_analogs": [
    {{
      "event_name": "历史事件名",
      "period": "发生时间 e.g. 2008-2009",
      "gold_price_change_pct": 数字(如15.5表示+15.5%),
      "similarity_score": 0.0-1.0,
      "key_parallels": ["相似点1", "相似点2"],
      "key_differences": ["差异点1"]
    }}
  ],
  "price_impact": {{
    "direction": "bullish / bearish / neutral",
    "base_case_change_pct": 数字(如10.5),
    "bullish_case_change_pct": 数字,
    "bearish_case_change_pct": 数字,
    "peak_impact_months": 整数(影响在事件发生后几个月达峰),
    "confidence": 0.0-1.0,
    "reasoning": "核心推理，200字以内"
  }},
  "key_levels": [关键价位1, 关键价位2],
  "probability_assessment": "事件发生概率的定性评估，以及不同阶段黄金的潜在反应路径",
  "strategy": {{
    "overall_position": "增持/减持/观望/对冲",
    "spot_gold_action": "多头/空头/观望/分批建仓",
    "accumulation_gold_action": "定投加码/定投减码/暂停/维持",
    "suggested_entry_zones": [入场价位],
    "suggested_exit_zones": [离场价位],
    "hedging_suggestions": ["对冲建议1", "对冲建议2"],
    "position_sizing": "仓位建议，如'不超过总资产25%'",
    "rebalancing_frequency": "再平衡频率，如'每月'"
  }},
  "risk_factors": ["风险因子1", "风险因子2"],
  "monitoring_indicators": ["先行指标1", "先行指标2"]
}}
```

## 重要原则
1. 保持专业、客观、基于经济学逻辑
2. 区分直接影响与二阶效应
3. 考虑央行政策响应（美联储可能如何应对？）
4. 考虑不同时间尺度的动态演化（初期避险→中期流动性→长期结构性变化）
5. 如果情景描述不完整，请基于合理假设填补，并在reasoning中说明假设
6. 所有数字要有逻辑依据，基于历史类比的量级调整"""

    # ------------------------------------------------------------------
    # LLM 调用
    # ------------------------------------------------------------------

    def _call_llm(self, prompt: str) -> dict[str, Any] | None:
        if not self.llm.enabled:
            return None

        messages = [{"role": "user", "content": prompt}]
        raw = self.llm.chat(messages, max_tokens=4096, temperature=0.3)

        if not raw:
            return None

        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
        json_str = json_match.group(1).strip() if json_match else raw.strip()

        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"raw_response": raw, "parse_error": True}

    # ------------------------------------------------------------------
    # 响应解析
    # ------------------------------------------------------------------

    def _parse_response(
        self,
        report_id: str,
        description: str,
        horizon: int,
        context: dict[str, Any],
        raw: dict[str, Any],
    ) -> ScenarioReport:
        if raw.get("parse_error"):
            return self._fallback_report(report_id, description, horizon, context)

        channels = [
            ImpactChannel(
                channel=c.get("channel", ""),
                direction=c.get("direction", "neutral"),
                magnitude=c.get("magnitude", "moderate"),
                description=c.get("description", ""),
                timeframe=c.get("timeframe", "medium-term"),
            )
            for c in raw.get("transmission_channels", [])
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
            for a in raw.get("historical_analogs", [])
        ]

        pi_raw = raw.get("price_impact", {})
        price_impact = PriceImpactEstimate(
            direction=pi_raw.get("direction", "neutral"),
            base_case_change_pct=float(pi_raw.get("base_case_change_pct", 0)),
            bullish_case_change_pct=float(pi_raw.get("bullish_case_change_pct", 0)),
            bearish_case_change_pct=float(pi_raw.get("bearish_case_change_pct", 0)),
            peak_impact_months=int(pi_raw.get("peak_impact_months", 0)),
            confidence=float(pi_raw.get("confidence", 0.5)),
            reasoning=pi_raw.get("reasoning", ""),
        )

        strat_raw = raw.get("strategy", {})
        strategy = StrategyRecommendation(
            overall_position=strat_raw.get("overall_position", "观望"),
            spot_gold_action=strat_raw.get("spot_gold_action", ""),
            accumulation_gold_action=strat_raw.get("accumulation_gold_action", ""),
            suggested_entry_zones=[float(z) for z in strat_raw.get("suggested_entry_zones", [])],
            suggested_exit_zones=[float(z) for z in strat_raw.get("suggested_exit_zones", [])],
            hedging_suggestions=strat_raw.get("hedging_suggestions", []),
            position_sizing=strat_raw.get("position_sizing", ""),
            rebalancing_frequency=strat_raw.get("rebalancing_frequency", ""),
        )

        return ScenarioReport(
            id=report_id,
            created_at=datetime.now(),
            scenario_description=description,
            time_horizon_months=horizon,
            context_snapshot=context,
            trigger_conditions=raw.get("trigger_conditions", []),
            transmission_channels=channels,
            historical_analogs=analogs,
            price_impact=price_impact,
            key_levels=[float(k) for k in raw.get("key_levels", [])],
            probability_assessment=raw.get("probability_assessment", ""),
            strategy=strategy,
            risk_factors=raw.get("risk_factors", []),
            monitoring_indicators=raw.get("monitoring_indicators", []),
        )

    # ------------------------------------------------------------------
    # 兜底报告（无LLM可用时）
    # ------------------------------------------------------------------

    def _fallback_report(
        self,
        report_id: str,
        description: str,
        horizon: int,
        context: dict[str, Any],
    ) -> ScenarioReport:
        return ScenarioReport(
            id=report_id,
            created_at=datetime.now(),
            scenario_description=description,
            time_horizon_months=horizon,
            context_snapshot=context,
            probability_assessment="LLM未配置，无法生成情景分析。请设置 LLM_API_KEY 环境变量。",
        )
