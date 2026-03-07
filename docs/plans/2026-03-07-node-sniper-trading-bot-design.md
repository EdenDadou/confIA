# Sniper BTC Trading Bot — Design Document

> **Pour Claude:** REQUIRED SUB-SKILL: Use superpowers:writing-plans to create implementation plan from this design.

**Goal:** Rebuild the trading bot as a structured Node.js/TypeScript project with event-driven "sniper" strategy — trade only on high-quality setups detected in real-time, use Claude for contextual analysis, learn from every outcome.

**Target:** Profitable over 24h continuous simulation with live Binance data.

---

## 1. Architecture

```
btc-sniper/
  src/
    engine/
      market-engine.ts        # Binance WS, candle builder, multi-timeframe
      event-detector.ts       # Pattern detection, triggers
      indicators.ts           # RSI, EMA, MACD, Bollinger, ATR, OBV, divergences
    brain/
      claude-brain.ts         # Claude contextual analysis — TRADE or SKIP
      pattern-memory.ts       # Store past setups + outcomes, feed to Claude
    trading/
      position-manager.ts     # Open, trailing stop, close
      risk-manager.ts         # Kelly sizing, circuit breakers, max drawdown
    agents/
      technical.ts            # Multi-TF technical analysis
      sentiment.ts            # Fear & Greed index
      onchain.ts              # Funding rate, liquidations, OI
      orderflow.ts            # Order book depth, bid/ask imbalance
    learning/
      optimizer.ts            # Adjust thresholds, weights, stop distance
      trade-journal.ts        # Persist all trades + agent signals at entry
    dashboard/
      server.ts               # Express/Fastify server
      public/
        index.html            # Jarvis HUD (same design, improved panels)
        js/app.js             # Frontend logic
        css/style.css         # Jarvis theme
    config/
      default.ts              # All configurable params
    index.ts                  # Entry point, wires everything
  data/
    trades.json               # Trade history (local persistence)
    patterns.json             # Pattern memory for Claude context
    optimizer.json            # Learned parameters
  tests/
  package.json
  tsconfig.json
```

## 2. Market Engine

**Multi-timeframe candle builder:**
- Binance WS `btcusdt@kline_1m` pour les bougies 1min live
- Construit des bougies 5min et 15min par agrégation
- Buffer: 500 candles par timeframe
- Calcule tous les indicateurs sur chaque timeframe à chaque nouvelle bougie

**Indicateurs calculés (par timeframe):**
- RSI(14)
- EMA(12), EMA(26), EMA(50)
- MACD (12, 26, 9)
- Bollinger Bands (20, 2)
- ATR(14)
- OBV (On-Balance Volume)
- Volume SMA(20) pour détecter les spikes

## 3. Event Detector

Le coeur du système. Ne réveille Claude que quand un **événement significatif** se produit.

**Événements détectés :**

| ID | Événement | Condition | Cooldown |
|---|---|---|---|
| `RSI_EXTREME` | RSI(14) < 25 ou > 75 sur 1min ET confirmé sur 5min | RSI cross threshold | 5min |
| `EMA_CROSS` | EMA12 croise EMA26 sur 5min | Cross détecté | 15min |
| `DIVERGENCE` | Prix new low + RSI higher low (ou inverse) | 3+ candles | 10min |
| `VOLUME_SPIKE` | Volume > 3x SMA(20) sur 1min | Spike détecté | 3min |
| `BB_BREAKOUT` | Prix sort des Bollinger puis rentre | Réentrée détectée | 5min |
| `FUNDING_FLIP` | Funding rate change de signe | API check 30s | 30min |
| `LIQUIDATION_WAVE` | Grosse liquidation (> $5M un côté) | API check 30s | 15min |

**Cooldown par événement** pour ne pas spammer Claude sur le même signal.

**Score de confluence :** Si plusieurs événements se déclenchent en même temps (ex: RSI extreme + volume spike + divergence), le score de confluence monte → Claude reçoit un signal plus fort.

## 4. Claude Brain

Quand un événement se déclenche, Claude reçoit un prompt structuré :

```
ÉVÉNEMENT DÉTECTÉ: RSI_EXTREME + VOLUME_SPIKE (confluence: 2)

CONTEXTE MULTI-TIMEFRAME:
- 1min: RSI=22, EMA12<EMA26, MACD histogram=-45, prix sous BB lower
- 5min: RSI=35, EMA12>EMA26, MACD histogram=12, prix sur BB middle
- 15min: RSI=48, tendance haussière, prix au-dessus EMA50

ORDER BOOK: 65% bid / 35% ask — forte pression acheteuse
FUNDING RATE: -0.02% — shorts paient, contrarian bullish
FEAR & GREED: 28 (Fear) — contrarian bullish

PATTERN MEMORY (situations similaires passées):
- RSI<25 + volume spike: 7 occurrences, 5 wins (71%), avg gain +0.8%
- RSI<25 sans volume: 12 occurrences, 4 wins (33%), avg gain +0.3%

POSITION ACTUELLE: Aucune

Décision: TRADE (LONG/SHORT + conviction 0-1) ou SKIP (+ raison)
```

**Claude utilise claude-sonnet-4-6** (pas Haiku — on veut de l'intelligence, pas de la vitesse).

**Réponse attendue (JSON):**
```json
{
  "action": "TRADE",
  "direction": "LONG",
  "conviction": 0.82,
  "reasoning": "RSI oversold avec volume spike = capitulation vendeuse. Funding négatif confirme. Pattern memory montre 71% win rate dans cette config. 15min encore bullish = le dip est une opportunité.",
  "suggested_stop_atr_mult": 1.2,
  "take_profit_pct": null
}
```

`conviction` drive le sizing via Kelly. Pas de take profit pour les sniper trades — trailing stop only.

## 5. Pattern Memory

Chaque setup est enregistré avec sa signature :

```json
{
  "id": "setup_142",
  "timestamp": 1709830000,
  "events": ["RSI_EXTREME", "VOLUME_SPIKE"],
  "confluence": 2,
  "indicators_1m": { "rsi": 22, "ema_cross": false, ... },
  "indicators_5m": { "rsi": 35, "ema_cross": true, ... },
  "indicators_15m": { "rsi": 48, ... },
  "funding_rate": -0.0002,
  "ob_ratio": 0.65,
  "fear_greed": 28,
  "decision": "TRADE",
  "direction": "LONG",
  "conviction": 0.82,
  "outcome": {
    "won": true,
    "pnl_pct": 0.8,
    "duration_s": 340,
    "exit_reason": "trailing_stop"
  }
}
```

Quand un nouveau setup arrive, on cherche les **5 setups les plus similaires** (même événements, indicateurs proches) et on les envoie à Claude comme contexte. C'est ça le vrai apprentissage — Claude voit ce qui a marché et ce qui n'a pas marché dans des situations similaires.

## 6. Position Manager

**Sniper mode :**
- Trailing stop à ATR × `stop_multiplier` (appris par l'optimizer, initial 0.8)
- Pas de take profit fixe — let winners run
- Pas de durée max — le trailing stop gère la sortie
- Une seule position à la fois

**Signal exit :** Si Claude détecte un événement contraire fort (confluence >= 2 dans la direction opposée), on peut sortir avant le trailing stop.

## 7. Risk Manager

- **Kelly sizing :** stake = bankroll × kelly_fraction × conviction
- **Max stake :** jamais plus de `max_stake_pct` du bankroll (appris, initial 25%)
- **Circuit breakers :**
  - 5 pertes consécutives → pause 1h
  - Drawdown > 15% → pause 24h
  - 3 SKIP consécutifs de Claude → log warning (marché incertain)
- **Leverage :** dynamique 5-50x basé sur conviction + ATR

## 8. Learning Engine (Optimizer)

**Ce qui est appris :**
- `min_conviction` — seuil minimum de conviction Claude pour trader (initial 0.6)
- `stop_multiplier` — distance du trailing stop en multiple d'ATR (initial 0.8)
- `max_stake_pct` — taille max de position (initial 0.25)
- `agent_weights` — poids de chaque type de donnée dans l'analyse
- `event_reliability` — fiabilité de chaque type d'événement (basé sur win rate historique)

**Comment :**
- Après chaque trade, on met à jour les stats par type d'événement
- Si un type d'événement a un win rate < 40% sur les 20 derniers, on baisse son poids
- Si un type a > 60% win rate, on monte son poids
- Le `stop_multiplier` s'ajuste selon le ratio avg_win/avg_loss

## 9. Dashboard

**Même design Jarvis HUD** avec améliorations :
- Chart avec indicateurs techniques (EMA, BB, RSI sub-panel)
- Markers visuels sur le chart quand un événement est détecté
- Panel "Pattern Memory" montrant les setups similaires trouvés
- Panel "Event Feed" montrant les événements détectés en temps réel
- Panel agents avec raisons détaillées
- Trade log chronologique avec leverage, conviction, événements triggers
- Stats d'apprentissage (fiabilité par type d'événement, poids agents)

## 10. Stack Technique

- **Runtime:** Node.js 20+ avec TypeScript
- **WebSocket:** `ws` pour Binance
- **HTTP:** `fastify` (plus rapide qu'Express)
- **Claude:** `@anthropic-ai/sdk`
- **Frontend:** HTML/CSS/JS vanilla (LightweightCharts + GridStack)
- **Persistence:** JSON files (local), PostgreSQL ready (pour prod)
- **Tests:** Vitest

## 11. Métriques de Succès (validation 24h)

- [ ] Win rate > 50%
- [ ] Ratio avg_win / avg_loss > 1.5
- [ ] Drawdown max < 10%
- [ ] PnL positif sur 24h
- [ ] < 20 trades (preuve qu'on est sélectif)
- [ ] Pattern memory contient au moins 30 setups enregistrés
