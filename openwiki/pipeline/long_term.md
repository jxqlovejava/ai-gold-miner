---
type: Concept
title: Long-Term Analysis Engine
description: Multi-horizon (1/6/12/24/36 month) gold price trend analysis using fundamental, technical, and scenario-based signals.
tags: [pipeline, long-term, trend-analysis, scenario]
resource: /src/gold_miner/pipeline/long_term.py
---

# Long-Term Analysis Engine

The **LongTermAnalyzer** (`pipeline/long_term.py`) provides gold price trend analysis across 1, 6, 12, 24, and 36 month horizons. It was relocated from the now-deleted `workflows/` directory to `pipeline/` (commit fd9d2f4).

## Architecture

The analyzer produces three major outputs:
1. **Signal Analysis** — Long-term fundamental, trend, and scenario signals
2. **Scenarios** — Scenario matrix with probability-weighted price projections
3. **Strategic Recommendation** — Action, target position %, confidence level

### Component Signals

| Signal Module | Description |
|--------------|-------------|
| `signals/long_term_fundamental.py` | Central bank reserves, monetary policy cycle, debt/GDP, demographic trends |
| `signals/long_term_trend.py` | Multi-year moving averages, secular trend lines, cycle analysis |
| `signals/long_term_scenario.py` | Tail-risk scenarios (currency crisis, de-dollarization, war, debt crisis) |

### Output Structure

```python
{
    "summary": {"action": "观望", "target_position_pct": 0.5, "confidence": 0.6},
    "munger_models": ["Margin of Safety", "Circle of Competence"],
    "trigger_conditions": ["DXY breaks below 95", "Central bank buying > 800t"],
    "rebalancing_rules": ["Reduce if gold > 60% of portfolio"],
    "scenario_matrix": {
        "base_price": 3200.0,
        "expected_price": 3550.0,
        "weighted_expected_change_pct": 10.9,
        "scenarios": [
            {"name": "Central Bank Buying", "probability_pct": 35, ...},
            {"name": "USD Weakening", "probability_pct": 25, ...},
            {"name": "Recession", "probability_pct": 20, ...}
        ]
    }
}
```

## CLI

```bash
gold-miner longterm --horizon 12     # 12-month analysis
gold-miner longterm --dry-run        # Show steps without execution
gold-miner longterm --output report.json  # Save to file
```

## Key Source Files

- `/src/gold_miner/pipeline/long_term.py` — LongTermAnalyzer
- `/src/gold_miner/pipeline/long_term_result.py` — Result data structures
- `/src/gold_miner/signals/long_term_fundamental.py` — Long-term fundamental signals
- `/src/gold_miner/signals/long_term_trend.py` — Long-term trend signals
- `/src/gold_miner/signals/long_term_scenario.py` — Long-term scenario signals
