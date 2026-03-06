# Design : Système de Trading Multi-Agents Polymarket BTC

**Date :** 2026-03-06
**Statut :** Approuvé

---

## Contexte

Système automatisé 24/7 qui parie sur les mouvements de prix du BTC sur Polymarket, en combinant la surveillance des marchés existants et la création de nouveaux marchés sur des fenêtres de 5 minutes. Les décisions sont prises par des agents déterministes rapides, supervisés périodiquement par des agents Claude.

---

## Architecture globale

### Couche données (4 agents parallèles)

- **Agent Market** : Binance WebSocket `btcusdt@kline_5m`, order book depth, OHLCV, indicateurs techniques (RSI, MACD, Bollinger, VWAP, VPIN)
- **Agent Sentiment** : Fear & Greed Index (1h), CryptoCompare headlines NLP (15min), Santiment social volume (15min)
- **Agent On-Chain** : CoinGlass open interest, funding rate, liquidations
- **Agent Polymarket Monitor** : odds movement, liquidité des marchés, volume anormal, arbitrage odds/prix BTC, détection de marchés actifs et créables

### Couche décision

- **Chief Trader Agent** : agrège les 4 signaux avec pondération, calcule le score de confiance (0-100), applique le 1/2 Kelly pour la mise, publie les ordres sur Redis

```
score_final = (
    0.30 * score_technique +
    0.20 * score_sentiment +
    0.20 * score_onchain +
    0.30 * score_polymarket_odds
)
# Seuil minimum pour trader : score > 65/100
# Mise = (1/2 Kelly) * bankroll * edge_estimé
```

### Couche exécution

- **Execution Agent** : place les ordres sur Polymarket CLOB API, crée de nouveaux marchés si opportunité détectée, gère les positions ouvertes, applique les circuit breakers

### Couche supervision (agents Claude)

- **Health Checker Agent** (cron 15min) : vérifie heartbeats, lag Redis, erreurs API, drawdown, cohérence des données. Auto-fix via Docker API, alerte Telegram en CRITICAL, suspension de l'Execution Agent si nécessaire
- **Strategy Reviewer Agent** (cron 2h) : analyse P&L, win rate, concentration des pertes, calibration du Kelly, recommandations d'ajustement des poids (auto-appliquées si confiance > 80%)

---

## Sources de données

| Source | Type | Fréquence |
|--------|------|-----------|
| Binance WS | Prix, OHLCV, order book | Temps réel |
| CoinGlass | Open interest, funding rate, liquidations | 5min |
| Alternative.me | Fear & Greed Index | 1h |
| CryptoCompare | Sentiment headlines | 15min |
| Santiment | Social volume | 15min |
| Polymarket CLOB API | Odds, liquidité, volume | Temps réel |

---

## Circuit breakers (non négociables)

| Condition | Action |
|-----------|--------|
| Drawdown > 15% du bankroll journalier | Stop total 24h |
| 5 trades perdants consécutifs | Pause 1h + alerte |
| Polymarket API down > 2min | Stop exécution |
| Score de confiance < 65 | Pas de trade |
| Mise calculée > 5% du bankroll | Cap à 5% |

---

## Stack technique

- **Langage :** Python 3.12
- **Bus de messages :** Redis Streams
- **Base de données :** PostgreSQL (historique trades, signaux, rapports)
- **Orchestration :** Docker Compose (`restart: always` + health checks)
- **Supervision LLM :** Anthropic SDK (claude-sonnet-4-6)
- **Monitoring :** Prometheus + Grafana + Alertmanager (Telegram/email)

---

## Structure du projet

```
trade/
├── agents/
│   ├── market/          # Données marché + indicateurs techniques
│   ├── sentiment/       # Fear & Greed, NLP, Santiment
│   ├── onchain/         # CoinGlass, open interest
│   ├── polymarket/      # Monitor marchés + odds
│   ├── chief_trader/    # Agrégation signaux + calcul Kelly
│   ├── execution/       # Exécution CLOB Polymarket
│   ├── health/          # Superviseur Claude (15min)
│   └── reviewer/        # Revieweur stratégie Claude (2h)
├── shared/
│   ├── redis_client.py
│   ├── models.py        # Schemas Pydantic
│   └── kelly.py         # Calcul 1/2 Kelly
├── infra/
│   ├── docker-compose.yml
│   ├── prometheus/
│   └── grafana/
├── docs/
│   └── plans/
└── tests/
```

---

## Dashboard Grafana

- Courbe de capital + drawdown en temps réel
- Win rate, P&L cumulé, distribution des mises
- Heatmap des scores par agent
- Uptime et latence de chaque service
- Timeline des incidents et auto-fixes

---

## Ordre de développement

```
Phase 1 — Fondations
  → Redis + Postgres + Docker Compose
  → Agent Market (Binance WS)
  → Agent Polymarket Monitor

Phase 2 — Décision
  → Chief Trader (agrégation + Kelly)
  → Backtesting sur données historiques

Phase 3 — Exécution
  → Execution Agent (paper trading d'abord)
  → Circuit breakers

Phase 4 — Supervision
  → Health Checker Agent (Claude)
  → Strategy Reviewer Agent (Claude)
  → Grafana dashboard

Phase 5 — Live
  → Agents sentiment + on-chain
  → Passage en real money
  → Alertes Telegram
```
