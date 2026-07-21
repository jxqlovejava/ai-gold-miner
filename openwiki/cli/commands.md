---
type: Concept
title: CLI Commands Reference
description: Complete reference for all gold-miner CLI commands, options, and usage patterns.
tags: [cli, commands, reference]
resource: /src/gold_miner/cli/core.py
---

# CLI Commands Reference

The `gold-miner` CLI is defined in `cli/core.py` and dispatches to handler modules in `cli/*.py`.

## Usage

```bash
gold-miner <command> [options]
```

## Commands

| Command | Description | Handler |
|---------|-------------|---------|
| `scan` | Run full 9-step analysis pipeline | `cli/scan.py` |
| `prepare` | Step 1 only: calendar + events + news + data | `cli/prepare.py` |
| `longterm` | Long-term trend analysis (1-36 month) | `cli/long_term.py` |
| `quote` | Real-time gold/ETF/DXY quotes | `cli/quote.py` |
| `analyze` | Article/news analysis | `cli/analysis.py` |
| `scenario` | Scenario analysis | `cli/scenario.py` |
| `doctrine` | Doctrine check & Munger model search | `cli/doctrine.py` |
| `backtest` | Strategy backtesting | `cli/backtest.py` |
| `track` | Prediction tracking & review | `cli/tracking.py` |
| `verify` | Prediction verification & settlement | `cli/verify.py` |
| `report` | Report generation | `cli/report.py` |
| `journal` | Prediction journal management | `cli/journal.py` |
| `record` | Trade recording | `cli/record.py` |
| `daemon` | Scheduled scanning daemon | `cli/daemon.py` |
| `doctor` | System diagnosis | `cli/verify.py` |
| `setup` | System setup | `cli/verify.py` |
| `web` | Launch Streamlit dashboard | `cli/web.py` |
| `proxy-install` | Proxy installation helper | `cli/proxy_install.py` |

## Global Options

| Option | Description |
|--------|-------------|
| `--demo` | Demo mode: skip API key features |
| `--days N` | Lookback days (default: 365) |
| `--news` | Enable news analysis |
| `--sentiment` | Enable sentiment analysis |
| `--risk {aggressive,moderate,conservative}` | Risk profile override |
| `--deep` | LLM deep analysis |
| `--capital FLOAT` | Initial capital (backtest) |
| `--output PATH` | Output file path |
| `--behavior` | Behavior backtest mode |

## Command-Specific Options

### scan
```bash
gold-miner scan [--days 30] [--news] [--sentiment] [--deep] [--demo]
```

### prepare
```bash
gold-miner prepare
```

### longterm
```bash
gold-miner longterm --horizon 12 [--risk moderate] [--output report.json] [--dry-run]
```

### analyze
```bash
gold-miner analyze --url <article_url>
gold-miner analyze --text <article_text>
gold-miner analyze --show <id>
gold-miner analyze --update <id> --llm-analysis <json>
gold-miner analyze --predict <id> --direction bullish --confidence 0.8
```

### doctrine
```bash
gold-miner doctrine --check [--dims technical,fundamental]
gold-miner doctrine --toggle <rule_id>
gold-miner doctrine --list --type rules
gold-miner doctrine --search <keyword>         # Search Munger models
gold-miner doctrine --discipline <discipline>  # Filter by discipline
```

### scenario
```bash
gold-miner scenario --text <description> [--save] [--track]
```

### track
```bash
gold-miner track --list
gold-miner track --price 3200
gold-miner track --resolve-id <prediction_id>
```

### backtest
```bash
gold-miner backtest --days 365 --capital 100000 [--behavior]
```

### daemon
```bash
gold-miner daemon --interval 60 [--once]
```

## Entry Points

| Entry | Path | Description |
|-------|------|-------------|
| `gold-miner` | `gold_miner/cli/core.py:main()` | Main CLI (via pyproject.toml scripts) |
| `gold-miner-agent` | `gold_miner/agent/cli.py:main()` | Agent scheduler CLI |

## Key Source Files

- `/src/gold_miner/cli/core.py` — argparse setup & dispatch (~10KB)
- `/src/gold_miner/cli/scan.py` — scan handler
- `/src/gold_miner/cli/prepare.py` — prepare handler (added in commit 9e251f6)
- `/src/gold_miner/cli/long_term.py` — longterm handler
