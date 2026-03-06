# Design : Dashboard Expert Multi-Agents avec Trailing Stop

**Date :** 2026-03-06
**Statut :** Approuvé

---

## Vue d'ensemble

Le dashboard passe d'un fichier monolithique à une architecture multi-agents via Redis Streams. Chaque agent est un expert spécialisé qui publie ses signaux. Le Chief Trader agrège et décide. Les positions restent ouvertes avec trailing stop + confirmation des signaux.

---

## Agents (6 modules)

| Agent | Source | Fréquence | Indicateurs |
|-------|--------|-----------|-------------|
| **Technical** | Binance WS 1s→5s candles | Temps réel | RSI, MACD, Bollinger, VWAP, EMA multi-TF (5s/1m/5m), Stoch RSI, ATR, Volume Profile, OBV, VPIN |
| **Sentiment** | Alternative.me API | 1h | Fear & Greed Index |
| **On-Chain** | Playwright scraping CoinGlass + APIs gratuites Binance Futures | 30s-1min | Liquidations, OI changes, funding rate, whale movements, long/short ratio |
| **Order Flow** | MEXC API (order book + funding) | 5s | OB imbalance, bid/ask ratio, spread, volume delta |
| **Chief Trader** | Redis Streams (agrège tout) | Continu | Score pondéré, 1/2 Kelly sizing, direction LONG/SHORT |
| **Position Manager** | Tick-by-tick via WS | Continu | Trailing stop, gestion position ouverte |

---

## Logique de trading

```
1. OUVERTURE — Chief Trader agrège les signaux → ouvre LONG ou SHORT toutes les 30s
2. PENDANT LA POSITION (tick-by-tick) :
   - Trailing stop = ATR × 1.5 en dessous du plus haut (LONG) / au-dessus du plus bas (SHORT)
   - Le stop monte (LONG) ou descend (SHORT), jamais l'inverse
3. FERMETURE si :
   - Trailing stop touché → ferme
   - OU signaux se retournent (RSI cross, MACD flip, OB imbalance inverse) → ferme
   - OU 30s écoulées ET trade en perte → ferme
4. MAINTIEN si (à 30s) :
   - Trade en profit ET signaux confirment la direction → garde ouvert, trailing stop actif
   - Prochain trade autorisé seulement après fermeture
```

---

## Scraping On-Chain (Playwright)

- CoinGlass : page liquidations BTC, heatmap, OI
- Binance Futures : long/short ratio, top trader positions
- Worker headless continu, publie sur Redis toutes les 30-60s

---

## Stack technique

- Redis Streams pour la communication inter-agents
- Chaque agent = process async, lancé par le dashboard au startup
- Playwright pour le scraping (headless Chromium)
- Dashboard FastAPI côté frontend, consomme Redis

---

## Structure fichiers

```
agents/
  technical/agent.py    — indicateurs techniques
  sentiment/agent.py    — Fear & Greed
  onchain/agent.py      — Playwright scraper + Binance Futures API
  orderflow/agent.py    — MEXC order book
  chief_trader/agent.py — agrégation + décision
  position/manager.py   — trailing stop + gestion position
shared/
  redis_client.py
  models.py
  kelly.py
  indicators.py         — fonctions RSI, MACD, Bollinger, etc.
dashboard.py            — frontend + API, lit Redis
```

---

## Circuit breakers (inchangés)

| Condition | Action |
|-----------|--------|
| Drawdown > 15% du bankroll journalier | Stop total 24h |
| 5 trades perdants consécutifs | Pause 1h |
| Score de confiance < 65 | Pas de trade |
| Mise calculée > 5% du bankroll | Cap à 5% |
