---
type: Concept
title: Investment Doctrines — Rules, Munger Models, and Profile
description: 30 investment rules (r001-r030), Munger mental models, extreme scenario guards, and investor profile matching system.
tags: [doctrine, rules, munger, risk, profile]
resource: /docs/doctrine.md
---

# Investment Doctrines — Rules, Munger Models, and Profile

The doctrine system enforces investment discipline through automated rule checking, Munger model selection, and investor profile constraints.

## Doctrine Rules (r001-r030)

Defined in `doctrine/rules.py` and documented in `docs/doctrine.md`. Each rule has a severity level:

| Severity | Meaning |
|----------|---------|
| **block** | Must pass — decision cannot proceed |
| **warn** | Should pass — decision can proceed but flagged |
| **info** | Informational only |

### Category: Position Sizing
| ID | Name | Severity |
|----|------|----------|
| r001 | Single position cap (≤20% total assets) | block |
| r002 | Total gold exposure cap (≤80%) | block |
| r003 | Gold overweight warning (>50%) | warn |
| r004 | No heavy position before data events | warn |
| r005 | No chase/rush on 3%+ daily move | block |
| r006 | Friday position reduction | warn |
| r007 | Holiday position reduction | warn |
| r027 | Gold position rebalancing (55% warning, 60% force) | warn |
| r028 | Staged position building (≥2 batches, ≥5 days apart) | warn |
| r029 | Safety margin before adding positions | warn |

### Category: Emotional Discipline
| ID | Name | Severity |
|----|------|----------|
| r008 | Consecutive stop-loss rest (3→3 days) | block |
| r009 | Extreme sentiment pause (VIX>40, fear-greed>90/<10) | warn |
| r010 | Profit stop-loss migration (>20% → cost+) | block |
| r011 | One-sided consensus warning (>80%) | warn |
| r012 | Multi-dimension confirmation (≥2 dimensions) | warn |
| r013 | Divergence pause (both sides >60% confidence) | warn |
| r014 | Mandatory stop-loss on every trade | block |
| r015 | Written decision record | info |

### Category: Operational Discipline
| ID | Name | Severity |
|----|------|----------|
| r016 | Pre-data adjustment (1-2 days ahead) | warn |
| r017 | Conditional orders over manual | warn |
| r018 | Reduce on bounces, not bottoms | warn |
| r019 | Consecutive high-volatility pause | warn |

### Category: Information & Signal
| ID | Name | Severity |
|----|------|----------|
| r020 | ETF flow over CFTC (timeliness) | info |
| r021 | Retail buying + institutional selling | warn |
| r024 | Smart vs retail flow divergence | warn |

### Category: Psychology & Trend
| ID | Name | Severity |
|----|------|----------|
| r022 | Losing position decision quality drop (>10%) | warn |
| r023 | Empty-position perspective check | warn |
| r025 | ATR trailing stop (14×ATR×2.5) | block |
| r026 | 200-day MA as filter only, 60-day + fundamental confirmation | warn |

### Category: Core Principles
| ID | Name | Severity |
|----|------|----------|
| r030 | Always leave margin of safety | warn |

## Doctrine Checker

**File:** `doctrine/checker.py`

The **DoctrineChecker** evaluates all applicable rules against current market conditions and portfolio state. It is invoked in **Step 4** of the pipeline.

Input context includes:
- Position data (current holdings, P&L)
- Market conditions (daily change, VIX, data calendar)
- Signal consensus (dimension alignment)

Output: List of `DoctrineCheck` results with rule ID, severity, passed/failed, and explanation.

## Munger Model System

**File:** `doctrine/munger_models.py`

A library of Charlie Munger mental models organized by discipline:

| Discipline | Models |
|-----------|--------|
| **invest** | Mr. Market, Margin of Safety, Circle of Competence, Opportunity Cost |
| **psychology** | Incentive Bias, Availability Heuristic, Confirmation Bias, Social Proof |
| **physics** | Critical Mass, Break Point, Feedback Loops |
| **biology** | Evolution, Adaptation, Red Queen Effect |
| **economics** | Comparative Advantage, Trade-offs, Scale Effects |

Applied in **Step 5** of the pipeline — 2-3 models are selected based on current market context.

## Investor Profile

The investor profile is split into two private files (not in version control):

1. **Qualitative** (`data/private/investor_profile.md`): Risk tolerance, trading style, source preferences, notes
2. **Quantitative** (`data/private/portfolio.yaml`): Holdings in grams, cost basis, stop-loss levels, capital allocation

If private files don't exist, `data/investor_profile.example.md` and `data/portfolio.example.yaml` are used as placeholders.

## Extreme Guard

**File:** `advisor/extreme_guard.py`

The **ExtremeGuard** provides black swan / gray rhino scenario detection with:

| Scenario | Typical Drawdown | Duration |
|----------|-----------------|----------|
| Geopolitical war escalation | -8% | 30 days |
| USD credit crisis | -5% (gold positive) | 60 days |
| Liquidity crisis | -15% | 14 days |
| Fed policy error | -10% | 45 days |
| China economic hard landing | -12% | 90 days |
| Tail-risk hedging failure | -12% | 20 days |

Each scenario includes hedge suggestions and probability estimates.

## Key Source Files

- `/docs/doctrine.md` — Full doctrine definitions (source of truth)
- `/src/gold_miner/doctrine/rules.py` — Rule definitions in code
- `/src/gold_miner/doctrine/checker.py` — DoctrineChecker
- `/src/gold_miner/doctrine/munger_models.py` — Munger model library
- `/src/gold_miner/advisor/extreme_guard.py` — Extreme scenario guard (~9KB)
- `/src/gold_miner/advisor/sentiment_guard.py` — Sentiment guard
- `/src/gold_miner/strategy/kelly.py` — Kelly criterion position sizing
- `/src/gold_miner/strategy/trailing_stop.py` — ATR trailing stop
- `/src/gold_miner/strategy/safety.py` — Safety checks
