---
type: Concept
title: Analysis Pipeline — 9-Step Analysis Engine
description: Core analysis engine that orchestrates data collection, signal generation, verification, doctrine checks, Munger models, agent debate, and trade decisions.
tags: [pipeline, analysis, signals, agent-debate, doctrine]
resource: /src/gold_miner/pipeline/analysis.py
---

# Analysis Pipeline — 9-Step Analysis Engine

The **AnalysisPipeline** (`pipeline/analysis.py`, ~88KB) is the project's core. It is invoked by `gold-miner scan` and orchestrates everything from data collection to trade recommendations.

## The 9 Steps

### Step 1: prepare
Calendar DOW validation + event sync + deep news search + data collection.
- Validates event day-of-week against reference tables (nonfarm=Fri, FOMC=Wed, jobless claims=Thu)
- Syncs economic calendar events
- Fetches deep news via NewsAPI + web search
- Collects price data (spot gold, DXY, rates, silver, breakeven, JD accumulation gold)

### Step 2: generate_signals
Generates 17 parallel signal channels (Phase 1 ThreadPoolExecutor, max_workers=14):
1. **technical** — RSI, MACD, Bollinger Bands, 20-day range, K-line patterns
2. **chanlun** — Daily 分型/笔/中枢/背驰/买卖点 structure (technical enhancement)
3. **trend_gate** — Long-term MA50/100/200 trend gate (doctrine r026)
4. **fundamental** — DXY, real rates, breakeven, gold-silver ratio, central bank buying, **India GDP/INR**
5. **news** — 24h news sentiment scoring + raw news items
6. **sentiment** — AU futures open interest & volume-price relationships (spot OHLCV fallback)
7. **oil** — Oil pass-through channel (inflation → rate expectations)
8. **smart_money** — CFTC COT, ETF flow, COMEX large traders, 13F, composite score
9. **event** — Economic calendar + event-driven post-event signals
10. **recent_events** — Time-decay weighting, stale event auto-downgrade
11. **polymarket** — Prediction market probabilities (keyset fallback)
12. **hype_bias** — Over-hype detection, retail noise filter
13. **long_term** — Trend direction, fundamental score, scenario matrix
14. **scenario** — Black swan / what-if scenario analysis
15. **anomaly** — Divergence detection, volume surge
16. **monitor** — Active monitor trigger evaluation
17. **deepseek** — LLM deep analysis (with `--deep`)

### Step 3: source_truth
Source verification + fact-vs-interpretation classification.
- Cross-references news against T0-T3 tiered sources
- Labels each signal as FACT / INTERPRETATION / PROJECTION / OPINION

### Step 4: doctrine_check
Auto-checks all 30 investment rules (r001-r030) with block/warn/info severity.
- Position sizing: single position >20%, total exposure >80%, data-event proximity
- Emotional discipline: consecutive stop-losses, VIX extremes, one-sided consensus
- Operational discipline: conditional orders, rebalancing, trailing stop

### Step 5: munger_models
Selects 2-3 Munger mental models relevant to current market context. Applied models include:
- Mr. Market — don't be ruled by short-term sentiment
- Margin of Safety — leave room for error
- Circle of Competence — stay within gold analysis expertise
- Check-list Method — use discipline against human weakness
- Invert, Always Invert — consider what would make the trade fail

### Step 6: profile_match
Checks investor profile constraints:
- Qualitative profile (risk tolerance, trading style, source preferences)
- Quantitative portfolio (holdings, cost basis, stop-loss levels)
- Ensures recommendations stay within the investor's risk envelope

### Step 7: agent_debate
**Three-agent debate** that consumes all previous steps as input:
- **🐮 BullAgent** — Finds bullish reasons with smart_money_arguments (CFTC, ETF, COMEX, 13F). Smart money arguments are preserved separately from conventional arguments.
- **🐻 BearAgent** — Finds bearish reasons with smart_money_arguments.
- **💼 PortfolioManager** — Synthesizes both sides, checks doctrine gates, produces final position recommendations.

Smart money flow (dimension 👔) is an **independent dimension** — it is extracted and displayed separately, never buried in conventional arguments.

### Step 8: decide
Trade decision + conditional order review:
- Produces final trade recommendations with position size, stop-loss, take-profit
- Reviews existing conditional orders for updates
- Generates TradeDecision for dashboard display

### Step 9: plan
Future event tracking + scenario planning + Monitor creation:
- Lists upcoming high-impact events (NFP, CPI, FOMC, PCE)
- Builds scenario matrix (bull/bear/base cases)
- Creates/updates Monitors for ongoing tracking

## Execution Flow

```python
pipeline = AnalysisPipeline()
result = pipeline.run(ctx)
```

The pipeline logs timing for each step. If Step 1 fails to fetch gold price data, the pipeline short-circuits immediately.

## Parallel Execution

Recent performance improvements (commit 1cb5b0a, 1632780):
- Step 1 sub-steps parallelized (4-way)
- FactChecker dedup parallelized
- Monitor ingestion parallelized
- LLM calls parallelized
- httpx connection pool reused across steps (`_SharedClientWrapper`)

## CLI Entry Points

| Command | Description |
|---------|-------------|
| `gold-miner scan` | Full 9-step pipeline |
| `gold-miner prepare` | Step 1 only (calendar + events + data) |
| `gold-miner longterm` | Long-term analysis |

## Key Source Files

- `/src/gold_miner/pipeline/analysis.py` — Pipeline definition and all 9 steps
- `/src/gold_miner/pipeline/__init__.py` — Package exports (AnalysisPipeline, LongTermAnalyzer)
- `/src/gold_miner/cli/scan.py` — scan command handler
- `/src/gold_miner/cli/prepare.py` — prepare command handler
- `/src/gold_miner/decision/agents.py` — BullAgent, BearAgent, PortfolioManager
- `/src/gold_miner/decision/institutional_flow.py` — Smart money gating logic
