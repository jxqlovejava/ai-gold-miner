---
type: Concept
title: Signal System — 8-Dimensional Analysis
description: Signal generation architecture covering technical, fundamental, news, sentiment, smart money, event, polymarket, and anomaly dimensions.
tags: [signals, scoring, consensus, dimensions]
resource: /src/gold_miner/signals
---

# Signal System — 8-Dimensional Analysis

The signal system generates structured, scored signals across 8 dimensions. Each signal carries a direction (bullish/bearish/neutral), strength (strong/moderate/weak), score (-1 to +1), fact type, and metadata.

## Core Types

Defined in `signals/base.py`:

```python
SignalDirection: BULLISH | BEARISH | NEUTRAL
SignalStrength:  STRONG | MODERATE | WEAK
FactType:        FACT | INTERPRETATION | PROJECTION | OPINION
```

**SignalBundle** aggregates all signals with metadata including dimension-level details.

## Signal Generators

| Dimension | Generator | Data Sources | Key Indicators |
|-----------|-----------|-------------|----------------|
| **technical** | `TechnicalAnalyzer` | Yahoo Finance | RSI, MACD, Bollinger Bands, MAs, 20-day range |
| **fundamental** | `FundamentalAnalyzer` | FRED, Yahoo Finance | DXY (ICE ~100), real rates (TIPS), breakeven, gold-silver ratio, **India GDP/INR** |
| **news** | `NewsSignalGenerator` | NewsAPI, web search | 24h sentiment scoring, keyword impact detection |
| **sentiment** | `SentimentAnalyzer` | AKShare (AU futures) | Open interest trend, volume-price relationships, intraday bias |
| **smart_money** | `CotSignalGenerator`, `EtfFlowSignalGenerator`, `InstitutionalSignalGenerator` | CFTC, AKShare, Yahoo, 13F | CFTC non-commercial net, ETF flow, COMEX concentration, 13F, composite |
| **event** | `EconomicCalendarSignalGenerator`, `EventDrivenSignalGenerator` | Calendar store | Upcoming high-impact events, event detection |
| **polymarket** | `PolymarketSignalGenerator` | Polymarket API | Prediction market implied probabilities |
| **anomaly/scenario** | `AnomalyDetector`, `ScenarioAnalyzer` | Price data, scenario definitions | Divergence, volume surge, black swan detection |

## Smart Money Dimension

The smart money dimension (👔) is treated as a **first-class independent dimension** with its own dedicated generators:

- **CFTC COT** — Managed money net long, commercial hedger positioning
- **International ETF Flow** — GLD holdings in tonnes, cross-ETF volume analysis
- **COMEX Large Traders** — Concentration in futures market
- **13F Institutional Holdings** — Quarterly institutional filings
- **Smart Money Composite** — Aggregated score from all institutional sources

Proxy sources (domestic ETF price/volume) are **not** counted as real institutional flow.

## Scoring & Consensus

The **ScoringEngine** (`signals/engine.py`) produces a **DimensionConsensus**:

- Tracks active dimensions (those with non-zero signals)
- Computes consensus direction and ratio
- Flags when consensus is achieved (≥4 active dimensions, ≥75% same direction)
- Supports position threshold overrides when consensus signals low conviction

## Recent Signal Changes

- **India signals** (commit 97d8811) — INR/USD exchange rate analysis + GDP quarterly growth rate in `FundamentalAnalyzer` (file: `signals/fundamental.py`)
- **Open interest graceful skip** (commit 77cf21d) — `SentimentAnalyzer._analyze_open_interest()` now returns empty list when `open_interest` column is absent from AU dataframe, instead of crashing with KeyError
- **Sentinel news monitor** (commit c8b2dab) — Added midterm election patterns + India gold demand keyword rules in `sentinel/news_monitor.py`
- **Signal count/source attribution fixes** (commit 90a8d7d) — Systematic fix for two classes of bugs: incorrect signal count and wrong source tagging

## Key Source Files

- `/src/gold_miner/signals/__init__.py` — Public API exports
- `/src/gold_miner/signals/base.py` — Signal, SignalBundle, FactType, DimensionConsensus
- `/src/gold_miner/signals/engine.py` — ScoringEngine
- `/src/gold_miner/signals/technical.py` — Technical signals
- `/src/gold_miner/signals/fundamental.py` — Fundamental signals (~30KB, includes India)
- `/src/gold_miner/signals/news_signal.py` — News sentiment scoring
- `/src/gold_miner/signals/sentiment_signal.py` — AU futures sentiment
- `/src/gold_miner/signals/cot_signal.py` — CFTC COT signals
- `/src/gold_miner/signals/etf_flow_signal.py` — ETF flow signals (~16KB)
- `/src/gold_miner/signals/institutional_signal.py` — Institutional/13F signals (~18KB)
- `/src/gold_miner/signals/polymarket_signal.py` — Prediction market signals
