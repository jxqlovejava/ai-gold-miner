---
type: Concept
title: Architecture Overview
description: Module map, data flow, and key design decisions of AI Gold Miner — a multi-dimensional gold investment analysis system.
tags: [architecture, design, data-flow]
resource: /src/gold_miner
---

# Architecture Overview

## Module Map

```
cli/                  — argparse dispatcher (gold-miner command)
├── core.py           — main() entrypoint, 26+ commands
├── scan.py           → AnalysisPipeline
├── prepare.py        → AnalysisPipeline._step_prepare
├── long_term.py      → LongTermAnalyzer
├── analysis.py       — article analysis
├── backtest.py       — strategy backtesting
├── journal.py        — prediction journal
├── scenario.py       — scenario analysis
├── doctrine.py       — doctrine/Munger/monitor CLI
├── tracking.py       — prediction tracking/review
├── verify.py         — prediction verification
├── report.py         — report generation
├── quote.py          — real-time quote
├── daemon.py         — scheduled scanning daemon
├── record.py         — trade recording
└── web.py            → Streamlit dashboard

pipeline/             — Analysis engine
├── analysis.py       — AnalysisPipeline (9-step), 88KB core
└── long_term.py      — LongTermAnalyzer (1-36 month)

signals/              — 17-channel parallel signal generation
├── base.py           — Signal, SignalBundle, SignalDirection, FactType
├── technical.py      — RSI, MACD, Bollinger Bands
├── fundamental.py    — DXY, rates, inflation, India signals
├── news_signal.py    — news sentiment scoring
├── sentiment_signal.py — AU futures OI, volume-price
├── cot_signal.py     — CFTC COT positions
├── etf_flow_signal.py — ETF flow analysis
├── institutional_signal.py — 13F + smart money composite
├── economic_calendar.py — calendar event signals
├── event_driven.py   — event-driven signals
├── scenario.py       — scenario analysis signals
├── monitor_signal.py — Monitor-driven signals
├── hype_bias_signal.py — hype/bias detection
├── polymarket_signal.py — prediction market signals
└── engine.py         — ScoringEngine, consensus

decision/             — Agent debate & risk
├── agents.py         — BullAgent, BearAgent, PortfolioManager
├── institutional_flow.py — institutional flow gating
├── position_state.py — position state resolution
└── risk.py           — RiskManager

data/                 — Data fetchers
├── macro.py          — FRED macro data (DXY, rates, CPI, PPI, unemployment)
├── spot_gold.py      — Yahoo Finance spot gold price
├── etf_flow.py       — AKShare domestic ETF flow
├── cot_report.py     — CFTC COT CSV parser
├── news.py           — NewsAPI + web search aggregator
├── fact_checker.py   — Multi-source cross verification
├── jd_accumulation_gold.py — JD Finance accumulation gold price
├── sentiment.py      — Sentiment data fetcher
├── economic_data.py  — Economic data recorder
└── source_tiers.py   — T0-T3 source tiering

doctrine/             — Investment rules & Munger models
├── rules.py          — r001-r030 doctrine definitions
├── checker.py        — DoctrineChecker
├── munger_models.py  — Munger mental models
└── monitor.py        — Monitor management

proxy/                — Proxy manager (mihomo/clash)
├── manager.py        — ProxyManager, shared httpx client pool
└── __init__.py       — get_proxied_client()

advisor/              — Advisory & early warning
├── core.py           — AdvisorReport, ExtremeStressTest
├── consultant.py     — Investment consultant
├── early_warning.py  — EarlyWarningEngine
├── extreme_guard.py  — Black swan scenario guard
├── sentiment_guard.py— Sentiment guard
├── monitor_evaluator.py — Monitor evaluation
└── orchestrator.py   — Orchestrator

strategy/             — Trading strategies
├── kelly.py          — Kelly criterion position sizing
├── engine.py         — Multi-objective strategy engine
├── objectives.py     — Strategy objectives
├── position_risk_manager.py — Position risk management
├── trailing_stop.py  — ATR trailing stop
└── safety.py         — Safety checks

storage/              — Local file persistence
├── local.py          — LocalFileStore (data/private/)
└── __init__.py       — get_store() factory

llm/                  — LLM client
└── client.py         — DeepSeek Anthropic-compatible API

events/               — Event calendar
├── models.py         — EventType definitions
├── store.py          — EventStore
└── ...

sentinel/             — News monitoring
└── news_monitor.py   — Breaking news monitor v2

web/                  — Streamlit dashboard
├── app.py            — Streamlit app
└── ...

utils/                — Utilities
```

## Data Flow

```
External APIs (FRED, Yahoo, AKShare, CFTC, NewsAPI, JD)
    │
    ▼
data/ fetchers  ──►  signals/ generators
    │                      │
    │                      ▼
    │               ScoringEngine (consensus)
    │                      │
    ▼                      ▼
pipeline/AnalysisPipeline (9-step)
    │
    ├─ Step 1: prepare (calendar, events, news, data)
    ├─ Step 2: generate_signals (8 dimensions)
    ├─ Step 3: source_truth (verification)
    ├─ Step 4: doctrine_check (r001-r030)
    ├─ Step 5: munger_models (2-3 models)
    ├─ Step 6: profile_match (investor profile)
    ├─ Step 7: agent_debate (Bull/Bear/PM)
    ├─ Step 8: decide (trade decision + conditional orders)
    └─ Step 9: plan (events + scenarios + monitors)
```

## Key Design Decisions

### 1. Pipeline over Workflow System
The old `workflows/` directory (972 lines) was deleted. All analysis now runs through the unified `AnalysisPipeline` in `pipeline/analysis.py`. The `long_term` workflow was moved into `pipeline/long_term.py`.

### 2. Smart Money as First-Class Dimension
Smart money flow (CFTC COT + ETF flow + COMEX + 13F) is an independent dimension in agent debates. Bull/Bear agents must include `smart_money_arguments` that cannot be drowned out by news/sentiment signals.

### 3. Proxy Isolation
`ProxyManager` runs an isolated mihomo/clash process on non-standard ports (17890/19090). It does **not** modify system proxy settings. The `_SharedClientWrapper` pattern provides httpx connection pool reuse across the pipeline while preventing premature closing.

### 4. Data Source Tiering (T0-T3)
Every external data source is labeled with a trust tier:
- **T0** — Official primary sources (FRED, SEC, CFTC, central banks)
- **T1** — Authorized data terminals (Bloomberg, exchanges)
- **T2** — Authoritative media (Reuters, Bloomberg)
- **T3** — Aggregated/social media

### 5. Fact vs Interpretation
Every `Signal` carries a `fact_type` (FACT / INTERPRETATION / PROJECTION / OPINION). The system defaults to INTERPRETATION to avoid treating causal claims as hard facts.

### 6. Dual Gold Price Tracking
The system tracks both **international spot gold** (XAU/USD via Yahoo Finance) and **domestic JD accumulation gold** (via JD Finance API) to support the dual-target investment strategy.

## Recent Architecture Changes

- **Workflow system deleted** (commit eb59318) — unified into Pipeline + CLI
- **long_term.py relocated** (commit fd9d2f4) — from `workflows/` to `pipeline/`
- **9-step pipeline finalized** (commits 1458470, 8c43fe6, 772e124) — agent debate moved to step 7, conclusion before outlook
- **httpx connection pool** (commit 1632780) — `_SharedClientWrapper` prevents premature pool closing
- **Parallel perf** (commit 1cb5b0a) — 4-way parallel: step 1 sub-steps, step 2 dedup, monitor ingestion, LLM
- **India signals** (commit 97d8811) — INR/USD rate + GDP quarterly growth in fundamental signals
- **Sentinel news monitor v2** (commit c8b2dab) — midterm election cycle + India gold demand patterns
