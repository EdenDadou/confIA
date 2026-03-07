import Fastify from "fastify";
import fastifyStatic from "@fastify/static";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import { CONFIG } from "../config/default.js";
import { computeEMA, computeBollinger, computeRSI } from "../engine/indicators.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

export async function startDashboard(state: any) {
  const app = Fastify({ logger: false });

  await app.register(fastifyStatic, {
    root: join(__dirname, "public"),
  });

  app.get("/api/data", async () => {
    const pos = state.posManager.getState();
    const candles1m = state.market.getBuffer("1m").getAll();
    const closes = candles1m.map((c: any) => c.close);
    const stats = state.journal.stats();
    const sentiment = state.sentimentAgent.data;
    const onchain = state.onchainAgent.data;
    const orderflow = state.orderflowAgent.data;

    const ema12 = computeEMA(closes, 12);
    const ema26 = computeEMA(closes, 26);
    const bb = computeBollinger(closes, 20, 2);
    const rsi = computeRSI(closes, 14);

    return {
      price: state.price,
      candles: candles1m.slice(-300),
      chart_indicators: {
        ema12: ema12.map((v, i) => ({ time: candles1m[i]?.time, value: Math.round(v * 100) / 100 })),
        ema26: ema26.map((v, i) => ({ time: candles1m[i]?.time, value: Math.round(v * 100) / 100 })),
        bollinger: bb.map((b, i) => ({ time: candles1m[i + 19]?.time, upper: Math.round(b.upper * 100) / 100, lower: Math.round(b.lower * 100) / 100 })),
        rsi: rsi.map((v, i) => ({ time: candles1m[i + 14]?.time, value: Math.round(v * 10) / 10 })),
      },
      position: pos ? {
        ...pos,
        unrealized_pnl: state.posManager.unrealizedPnl(state.price),
        duration: Math.round((Date.now() - pos.openTime) / 1000),
      } : null,
      bankroll: state.riskManager.bankroll,
      initial: state.riskManager.initialBankroll,
      pnl: Math.round((state.riskManager.bankroll - state.riskManager.initialBankroll) * 100) / 100,
      trades: state.journal.getRecent(30),
      stats,
      events_log: state.eventsLog.slice(-20),
      sentiment: { fear_greed: sentiment.fear_greed, label: sentiment.fear_greed_label },
      onchain: { funding_rate: onchain.funding_rate, long_short_ratio: onchain.long_short_ratio, open_interest: onchain.open_interest },
      orderflow: { ob_ratio: orderflow.ob_ratio, bids: orderflow.bids.slice(0, 10), asks: orderflow.asks.slice(0, 10), spread: orderflow.spread },
      optimizer: state.optimizer.params,
      pattern_stats: state.patternMemory.allEventStats(),
      total_patterns: state.patternMemory.totalSetups(),
    };
  });

  await app.listen({ port: CONFIG.dashboard.port, host: CONFIG.dashboard.host });
  console.log(`[DASHBOARD] http://localhost:${CONFIG.dashboard.port}`);
}
