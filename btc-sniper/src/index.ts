import "dotenv/config";
import { CONFIG } from "./config/default.js";
import { MarketEngine } from "./engine/market-engine.js";
import { EventDetector, type MultiTFIndicators } from "./engine/event-detector.js";
import { TechnicalAgent } from "./agents/technical.js";
import { SentimentAgent } from "./agents/sentiment.js";
import { OnChainAgent } from "./agents/onchain.js";
import { OrderFlowAgent } from "./agents/orderflow.js";
import { ClaudeBrain } from "./brain/claude-brain.js";
import { PatternMemory } from "./learning/pattern-memory.js";
import { TradeJournal } from "./learning/trade-journal.js";
import { PositionManager } from "./trading/position-manager.js";
import { RiskManager } from "./trading/risk-manager.js";
import { Optimizer } from "./learning/optimizer.js";

const market = new MarketEngine();
const eventDetector = new EventDetector();
const techAgent = new TechnicalAgent();
const sentimentAgent = new SentimentAgent();
const onchainAgent = new OnChainAgent();
const orderflowAgent = new OrderFlowAgent();
const brain = new ClaudeBrain();
const patternMemory = new PatternMemory();
const journal = new TradeJournal();
const posManager = new PositionManager();
const riskManager = new RiskManager(CONFIG.trading.initial_bankroll);
const optimizer = new Optimizer();

let currentPatternId: number | undefined;
let eventsLog: { time: number; events: string[]; action: string }[] = [];

export const state = {
  market, eventDetector, techAgent, sentimentAgent, onchainAgent,
  orderflowAgent, brain, patternMemory, journal, posManager,
  riskManager, optimizer, eventsLog,
  get price() { return market.price; },
};

function closePosition(price: number, reason: string) {
  const result = posManager.close(price, reason);
  const won = result.pnl > 0;
  riskManager.updateBankroll(riskManager.bankroll + result.pnl);
  if (won) riskManager.recordWin(); else riskManager.recordLoss();
  const openTrades = journal.getOpen();
  const openTrade = openTrades[0];
  if (openTrade) {
    journal.recordClose(openTrade.id, price, result.reason);
  }
  if (currentPatternId !== undefined) {
    patternMemory.recordOutcome(currentPatternId, {
      won, pnl_pct: result.pnl / (openTrade?.stake ?? 1) * 100,
      duration_s: result.duration_s, exit_reason: result.reason,
    });
  }
  optimizer.recordOutcome(won, result.pnl, (openTrade?.events ?? []) as import("./config/default.js").EventType[]);
  currentPatternId = undefined;
  console.log(`[TRADE] CLOSED ${result.direction} pnl=$${result.pnl.toFixed(2)} reason=${reason} bankroll=$${riskManager.bankroll.toFixed(2)}`);
}

async function onCandle() {
  const price = market.price;
  if (price <= 0) return;

  const indicators: MultiTFIndicators = {
    "1m": techAgent.analyze(market.getBuffer("1m").getAll()),
    "5m": techAgent.analyze(market.getBuffer("5m").getAll()),
    "15m": techAgent.analyze(market.getBuffer("15m").getAll()),
  };

  if (posManager.hasPosition) {
    const tick = posManager.tick(price);
    if (tick.action === "CLOSE") closePosition(price, tick.reason!);
    return;
  }

  const events = eventDetector.detect(indicators);
  const onchain = await onchainAgent.fetch();
  const fundingEvent = eventDetector.detectFundingFlip(onchainAgent.previousFundingRate, onchain.funding_rate);
  if (fundingEvent) events.push(fundingEvent);

  if (events.length === 0) return;

  eventsLog.push({ time: Date.now(), events: events.map((e) => e.type), action: "detecting" });
  if (eventsLog.length > 100) eventsLog.shift();
  console.log(`[EVENT] ${events.map((e) => `${e.type}(${e.direction})`).join(" + ")} — confluence: ${events.length}`);

  if (!riskManager.canTrade()) {
    console.log("[RISK] Circuit breaker active — skipping");
    return;
  }

  const sentiment = await sentimentAgent.fetch();
  const orderflow = await orderflowAgent.fetch();
  const similar = patternMemory.findSimilar(events.map((e) => e.type), { rsi: indicators["1m"].rsi }, 5);

  const decision = await brain.analyze(events, indicators, {
    funding_rate: onchain.funding_rate, long_short_ratio: onchain.long_short_ratio,
    ob_ratio: orderflow.ob_ratio, fear_greed: sentiment.fear_greed,
    fear_greed_label: sentiment.fear_greed_label, similar_setups: similar,
    bankroll: riskManager.bankroll, has_position: posManager.hasPosition,
  });

  console.log(`[BRAIN] ${decision.action} ${decision.direction} conviction=${decision.conviction.toFixed(2)} — ${decision.reasoning}`);
  eventsLog[eventsLog.length - 1].action = decision.action;

  if (decision.action === "TRADE" && decision.conviction >= optimizer.params.min_conviction) {
    const atr = techAgent.computeATR(market.getBuffer("1m").getAll());
    const atrPct = atr / price;
    const stopMult = decision.suggested_stop_atr_mult ?? optimizer.params.stop_multiplier;
    const stake = riskManager.computeStake(decision.conviction, optimizer.params.max_stake_pct);
    const leverage = riskManager.computeLeverage(decision.conviction, atrPct);

    posManager.open(decision.direction as "LONG" | "SHORT", price, stake, atr, stopMult, leverage);

    patternMemory.record({
      events: events.map((e) => e.type), direction: decision.direction,
      indicators_1m: { rsi: indicators["1m"].rsi, ema12: indicators["1m"].ema12, ema26: indicators["1m"].ema26 },
      indicators_5m: { rsi: indicators["5m"].rsi, ema12: indicators["5m"].ema12, ema26: indicators["5m"].ema26 },
      conviction: decision.conviction,
    });
    currentPatternId = patternMemory.totalSetups() - 1;

    journal.recordOpen({
      direction: decision.direction, entry_price: price,
      stake, leverage, conviction: decision.conviction,
      events: events.map((e) => e.type),
    });

    console.log(`[TRADE] OPENED ${decision.direction} $${stake.toFixed(2)} @${price.toFixed(2)} lev=${leverage}x stop=${posManager.position!.stopLoss.toFixed(2)}`);
  }
}

async function main() {
  console.log(`
  ╔══════════════════════════════════════╗
  ║   BTC SNIPER — Event-Driven Bot     ║
  ║   Mode: Simulation                   ║
  ║   Dashboard: http://localhost:${CONFIG.dashboard.port}   ║
  ╚══════════════════════════════════════╝
  `);

  await market.seedHistorical();
  market.connect();
  market.on("candle", () => onCandle());

  market.on("tick", (price: number) => {
    if (posManager.hasPosition) {
      const tick = posManager.tick(price);
      if (tick.action === "CLOSE") closePosition(price, tick.reason!);
    }
  });

  setInterval(async () => {
    await sentimentAgent.fetch();
    await onchainAgent.fetch();
    await orderflowAgent.fetch();
  }, 30_000);

  try {
    const { startDashboard } = await import("./dashboard/server.js");
    await startDashboard(state);
  } catch (e) {
    console.log("[DASHBOARD] Not available yet, skipping...", e);
  }
}

main().catch(console.error);
