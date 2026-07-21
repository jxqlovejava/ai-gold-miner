---
type: Concept
title: AI Gold Miner — Quickstart
description: Entrypoint for the AI Gold Miner documentation. Covers setup, first run, CLI usage, and links to all major documentation sections.
tags: [quickstart, gold-miner, investment]
---

# AI Gold Miner — Quickstart

**给黄金投资小白的 AI 投资副驾驶。** 把金价拆成技术、基本面、消息、情绪、事件等维度，让 AI 看数据、做辩论、查纪律、给建议。

## Quick Start

### Docker (simplest)

```bash
docker compose up --build
```

Runs `gold-miner scan` in demo mode — no API keys needed.

### Local Installation

Requires Python **3.11** (locked to avoid OpenSSL 3.x compatibility issues).

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # edit with your API keys
```

### First Run

```bash
gold-miner scan --demo
```

This runs the full 9-step pipeline in demo mode, skipping any API-dependent features.

## Documentation Sections

| Section | Description |
|---------|-------------|
| [Architecture Overview](architecture/overview.md) | Module map, data flow, key design decisions |
| [9-Step Analysis Pipeline](pipeline/analysis-pipeline.md) | Core analysis engine: prepare → signals → debate → decide |
| [Long-Term Analysis](pipeline/long_term.md) | 1-36 month trend analysis |
| [Signal System](signals/overview.md) | 8-dimensional signal generation and consensus |
| [CLI Commands](cli/commands.md) | All `gold-miner` commands and options |
| [Data Sources](data-sources/overview.md) | FRED, Yahoo Finance, AKShare, CFTC, JD, news, proxy |
| [Investment Doctrines](doctrine/overview.md) | r001-r030 rules, Munger models, investor profile |

## Key Concepts

- **AnalysisPipeline** — 9-step pipeline executed by `gold-miner scan`
- **Agent Debate** — 🐮 Bull vs 🐻 Bear vs 💼 PortfolioManager three-way debate
- **Smart Money Flow** — CFTC COT + ETF flow + COMEX + 13F institutional signals
- **Doctrine Check** — 30 investment rules auto-checked (block/warn/info)
- **Source Truth** — T0 (official) through T3 (unverified) labeling for all external data
- **Investor Profile** — Qualitative risk profile + quantitative portfolio read from `data/private/`

## Backlog

- Backtesting system (`src/gold_miner/backtest/`, `src/gold_miner/strategy/`) — significant but not yet documented
- Experience & self-improvement loop (`src/gold_miner/experience/`, `src/gold_miner/improvement/`)
- Events & calendar system (`src/gold_miner/events/`)
- Advisor subsystem (`src/gold_miner/advisor/`)
- Web dashboard (`src/gold_miner/web/`)
- Agent scheduler (`src/gold_miner/agent/`)
- Verification & prediction tracking (`src/gold_miner/verification/`)
