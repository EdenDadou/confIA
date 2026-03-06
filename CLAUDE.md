# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Polymarket BTC multi-agent trading system — automated 24/7 betting on BTC price movements on Polymarket. Currently in early development (Phase 1). A standalone simulation dashboard (`dashboard.py`) exists as a prototype.

## Architecture

**Multi-agent pipeline:**
1. **Data layer** (4 parallel agents): Market (Binance WS), Sentiment (Fear & Greed, NLP), On-Chain (CoinGlass), Polymarket Monitor (odds, liquidity)
2. **Decision layer**: Chief Trader aggregates signals with weighted scoring (tech 30%, sentiment 20%, on-chain 20%, polymarket 30%), applies 1/2 Kelly sizing, threshold > 65/100
3. **Execution layer**: Places orders on Polymarket CLOB API, enforces circuit breakers
4. **Supervision layer**: Claude-powered Health Checker (15min cron) and Strategy Reviewer (2h cron)

Agents communicate via **Redis Streams**. State persisted in **PostgreSQL**.

## Tech Stack

Python 3.12, Redis Streams, PostgreSQL, Docker Compose, Anthropic SDK (claude-sonnet-4-6), Pydantic v2, ccxt, websockets, prometheus-client, Grafana

## Running the Dashboard (Prototype)

```bash
python3 dashboard.py  # serves on http://localhost:8080
```

Dependencies: `fastapi`, `uvicorn`, `httpx`, `numpy`, `websockets`

The dashboard connects to Binance WS for real-time BTC data, builds 5s candles, computes RSI divergences, and simulates trades with a 30s cooldown and 70% win probability threshold.

## Circuit Breakers (Hard-coded, non-overridable)

- Drawdown > 15% daily bankroll → stop 24h
- 5 consecutive losses → pause 1h
- Polymarket API down > 2min → stop execution
- Confidence < 65 → no trade
- Single bet > 5% bankroll → capped

## Target Project Structure

```
agents/{market,sentiment,onchain,polymarket,chief_trader,execution,health,reviewer}/
shared/{redis_client,models,kelly}.py
infra/{docker-compose.yml,prometheus/,grafana/}
tests/
```

## Language

Project documentation and code comments are in French. Variable/function names are in English.
