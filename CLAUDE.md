# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Multi-agent BTC trading bot — automated 24/7 live trading on Bitcoin futures. Currently migrating from Python prototype to structured Node.js project. A simulation dashboard (`dashboard.py`) exists as the working prototype.

## Current State

**Working prototype** (`dashboard.py`): single-file Python app with embedded frontend. Connects to Binance WS, runs 4 analysis agents + chief trader, manages positions with trailing stops, and displays everything in a Jarvis-style HUD dashboard.

**Key learnings from prototype:**
- 30% win rate but positive PnL (avg win > avg loss) — risk/reward is OK, entry timing is bad
- LONG trades are catastrophic (1W/7L) — bearish bias in agents
- Trailing stop works well — most profitable exits come from it
- Max hold time was killing winners — removed
- Learning system (optimizer) adjusts parameters but agents don't truly improve

## Architecture

**Multi-agent pipeline:**
1. **Data agents** (4 parallel): Technical (Binance candles + indicators), Sentiment (Fear & Greed), On-Chain (funding rate, liquidations), Order Flow (order book pressure)
2. **Chief Trader**: Aggregates signals with learnable weights, decides LONG/SHORT
3. **Position Manager**: Trailing stop, no time limit on winners
4. **Optimizer**: Learns from outcomes — adjusts entry threshold, stop distance, agent weights

## Tech Stack (current prototype)

Python 3.13, FastAPI, Binance WebSocket, Anthropic SDK (claude-haiku-4-5 for agents, claude-sonnet-4-6 for chief), LightweightCharts, GridStack.js

## Running the Dashboard

```bash
source .venv/bin/activate
python3 dashboard.py  # http://localhost:8080
```

Env vars: `COOLDOWN_SECONDS` (default 60), `INITIAL_BANKROLL` (default 100), `MIN_QUALITY` (default 0.40)

## Data Persistence

- `data/trades.json` — trade history (survives restarts)
- `data/optimizer.json` — learned parameters (survives restarts)
- PostgreSQL for production (TradeRepository supports both)

## Circuit Breakers

- Drawdown > 15% bankroll → stop
- 5 consecutive losses → pause 1h
- Single bet capped by Kelly criterion + max_stake_pct

## Frontend Design

Jarvis HUD aesthetic — dark theme, cyan/green accents, Orbitron + Share Tech Mono fonts, GridStack drag-and-drop panels. Chart has EMA12, EMA26, Bollinger Bands overlays.

## Language

Documentation and comments in French. Variable/function names in English.
