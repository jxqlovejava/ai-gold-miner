---
type: Concept
title: Data Sources Overview
description: All external data sources used by AI Gold Miner — FRED, Yahoo Finance, AKShare, CFTC, NewsAPI, JD Finance, and proxy management.
tags: [data, api, sources, fetchers]
resource: /src/gold_miner/data
---

# Data Sources Overview

AI Gold Miner collects data from multiple external APIs and web sources. The `data/` directory contains all fetchers.

## Data Fetchers

### Macro Data — FRED API
**File:** `data/macro.py`
- Trade-weighted USD (DTWEXBGS — broad index, ~120 level)
- 10-year TIPS real rate (REAINTRATREARAT10Y)
- 10-year breakeven inflation (T10YIE)
- Fed funds rate (DFF)
- CPI index (CPIAUCSL, monthly)
- PPI index (PPIACO, monthly)
- Unemployment rate (UNRATE, monthly)
- **Requires:** `FRED_API_KEY` in `.env`
- **Fallback:** Yahoo Finance DXY symbol (`DX-Y.NYB`)

### Spot Gold — Yahoo Finance
**File:** `data/spot_gold.py`
- XAU/USD spot price
- GLD/IAU ETF prices
- DXY index (ICE, ~100 level)
- Silver (XAG/USD)
- **Rate-limiting:** Exponential backoff for 429 responses (commit e94a8b0)

### ETF Flow — AKShare
**File:** `data/etf_flow.py`
- Domestic gold ETFs: 518880 (华安), 159934 (易方达), 159937 (博时), 518800 (国泰), 518660 (工银), 518850 (华夏)
- Volume, turnover, NAV change
- **Frequency:** Daily

### CFTC COT Report
**File:** `data/cot_report.py`
- Source: CFTC.gov weekly legacy report (`deafut.txt`)
- Non-commercial (managed money) long/short
- Commercial hedger positions
- Non-reportable positions
- **Multi-layer SSL/TLS fallback** (commit 2534579) — cascading from direct HTTPS → alternate DNS → CURL proxy → requests

### News — NewsAPI + Web Search
**File:** `data/news.py`
- Primary: NewsAPI
- Fallback: Web search (Tavily via LangChain)
- **Requires:** `NEWS_API_KEY`, `TAVILY_API_KEY`

### Fact Checker — Multi-Source Verification
**File:** `data/fact_checker.py`
- Cross-references news items against T0-T3 tiered sources
- Verification status: `confirmed` (2+ independent sources), `unverified`, `disputed`, `false`
- T0 domains: sec.gov, federalreserve.gov, treasury.gov, bls.gov, gold.org, imf.org
- T1-T2: reuters.com, bloomberg.com, ft.com, wsj.com, cnbc.com
- **Parallelized** (commit 1632780)

### JD Accumulation Gold
**File:** `data/jd_accumulation_gold.py`
- Source: JD Finance API
- Banks: 民生(MS), 浙商(ZS), 中信(ZX), 工行(GS), 广发(GF), 兴业(XY)
- Default: MS (民生积存金)
- Used for domestic accumulation gold price tracking (dual-target strategy)

### Sentiment Data
**File:** `data/sentiment.py`
- AKShare SHFE gold futures data (AU contracts)
- Open interest, volume, price

### Economic Data Recorder
**File:** `data/economic_data.py`
- Records fetched economic data points to `data/private/economic_data.jsonl`
- Supports historical tracking and trend analysis

## Proxy Management

**File:** `proxy/manager.py`

The system uses a dedicated ProxyManager that:
- Auto-discovers `mihomo` / `clash-meta` / `clash` binaries
- Starts an isolated proxy on port 17890 (API: 19090)
- Does NOT modify system proxy settings
- Uses `_SharedClientWrapper` for httpx connection pool reuse
- **Requires:** `MIHOMO_SUB_URL` for subscription URL

### get_proxied_client()
The `proxy/__init__.py` exports `get_proxied_client()` which returns an httpx client configured to use the proxy (or direct if proxy is unavailable).

## API Key Requirements

| Service | Env Var | Required For |
|---------|---------|-------------|
| FRED | `FRED_API_KEY` | Macro data |
| NewsAPI | `NEWS_API_KEY` | News analysis |
| Tavily | `TAVILY_API_KEY` | Web search fallback |
| DeepSeek/LLM | `LLM_API_KEY` | Deep article analysis |
| Proxy | `MIHOMO_SUB_URL` | Network proxy |

## Recent Changes

- **Rate-limiting** (commit e94a8b0): Yahoo Finance HTTP 429 handling with exponential backoff
- **SSL/TLS fallback** (commit 2534579): Multi-layer CFTC COT retrieval to bypass SSL/TLS proxy issues
- **Parallel FactChecker** (commit 1632780): Parallel cross-reference verification
- **httpx pool** (commit 1632780): Shared connection pool to avoid connection exhaustion

## Key Source Files

- `/src/gold_miner/data/macro.py` — FRED macro data fetcher
- `/src/gold_miner/data/spot_gold.py` — Yahoo Finance spot gold
- `/src/gold_miner/data/etf_flow.py` — AKShare ETF flow (~16KB)
- `/src/gold_miner/data/cot_report.py` — CFTC COT parser
- `/src/gold_miner/data/news.py` — News aggregation
- `/src/gold_miner/data/fact_checker.py` — Multi-source verification
- `/src/gold_miner/data/jd_accumulation_gold.py` — JD Finance gold
- `/src/gold_miner/data/sentiment.py` — AU futures data
- `/src/gold_miner/proxy/manager.py` — Proxy management
