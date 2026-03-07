import Anthropic from "@anthropic-ai/sdk";
import { CONFIG, type EventType } from "../config/default.js";
import type { DetectedEvent, MultiTFIndicators } from "../engine/event-detector.js";
import type { SetupRecord } from "../learning/pattern-memory.js";

const SYSTEM_PROMPT = `You are an elite BTC futures trader. You receive market events and multi-timeframe context.
You ONLY trade when the setup is high quality. You are a sniper — patient, precise, deadly.

RESPOND ONLY WITH JSON (no markdown, no extra text):
{
  "action": "TRADE" | "SKIP",
  "direction": "LONG" | "SHORT",
  "conviction": <float 0.0-1.0>,
  "reasoning": "<2-3 sentences explaining your decision>",
  "suggested_stop_atr_mult": <float 0.5-2.0 or null>
}

Rules:
- SKIP if signals conflict or setup is weak
- conviction > 0.8 only when 3+ signals align perfectly
- conviction 0.6-0.8 for solid setups with 2+ confirming signals
- conviction < 0.6 = usually SKIP
- Always consider the pattern memory — if similar setups lost money, be cautious
- Funding rate is CONTRARIAN: positive funding = crowded longs = bearish pressure
- Fear & Greed is CONTRARIAN: extreme fear = buying opportunity
- Multi-timeframe confluence is key: 1m signal confirmed on 5m/15m is much stronger`;

export interface BrainDecision {
  action: "TRADE" | "SKIP";
  direction: "LONG" | "SHORT";
  conviction: number;
  reasoning: string;
  suggested_stop_atr_mult: number | null;
}

export class ClaudeBrain {
  private client: Anthropic;

  constructor() {
    this.client = new Anthropic();
  }

  async analyze(
    events: DetectedEvent[],
    indicators: MultiTFIndicators,
    context: {
      funding_rate: number;
      long_short_ratio: number;
      ob_ratio: number;
      fear_greed: number;
      fear_greed_label: string;
      similar_setups: SetupRecord[];
      bankroll: number;
      has_position: boolean;
    }
  ): Promise<BrainDecision> {
    const eventsList = events
      .map(
        (e) =>
          `${e.type} (${e.direction}, strength=${e.strength.toFixed(2)}): ${e.detail}`
      )
      .join("\n");

    const similarText =
      context.similar_setups.length > 0
        ? context.similar_setups
            .map((s) => {
              const result = s.outcome
                ? `${s.outcome.won ? "WIN" : "LOSS"} ${s.outcome.pnl_pct > 0 ? "+" : ""}${s.outcome.pnl_pct.toFixed(2)}%`
                : "pending";
              return `- ${s.events.join("+")} → ${s.direction} (conviction ${s.conviction.toFixed(2)}) → ${result}`;
            })
            .join("\n")
        : "No similar setups recorded yet.";

    const prompt = `EVENTS DETECTED (confluence: ${events.length}):
${eventsList}

MULTI-TIMEFRAME CONTEXT:
- 1min: RSI=${indicators["1m"].rsi.toFixed(1)}, EMA12${indicators["1m"].ema12 > indicators["1m"].ema26 ? ">" : "<"}EMA26, MACD hist=${indicators["1m"].macd_hist.toFixed(2)}, price=${indicators["1m"].price.toFixed(2)}, BB=[${indicators["1m"].bb.lower.toFixed(0)}-${indicators["1m"].bb.upper.toFixed(0)}]
- 5min: RSI=${indicators["5m"].rsi.toFixed(1)}, EMA12${indicators["5m"].ema12 > indicators["5m"].ema26 ? ">" : "<"}EMA26, MACD hist=${indicators["5m"].macd_hist.toFixed(2)}
- 15min: RSI=${indicators["15m"].rsi.toFixed(1)}, EMA12${indicators["15m"].ema12 > indicators["15m"].ema26 ? ">" : "<"}EMA26, MACD hist=${indicators["15m"].macd_hist.toFixed(2)}

MARKET DATA:
- Funding rate: ${(context.funding_rate * 100).toFixed(4)}% ${context.funding_rate > 0 ? "(longs pay → bearish pressure)" : "(shorts pay → bullish pressure)"}
- Long/Short ratio: ${context.long_short_ratio.toFixed(2)}
- Order book: ${(context.ob_ratio * 100).toFixed(0)}% bid / ${((1 - context.ob_ratio) * 100).toFixed(0)}% ask
- Fear & Greed: ${context.fear_greed} (${context.fear_greed_label})

PATTERN MEMORY (similar past setups):
${similarText}

PORTFOLIO: Bankroll $${context.bankroll.toFixed(2)} | Position: ${context.has_position ? "ALREADY OPEN — SKIP" : "None"}

Your decision:`;

    try {
      const response = await this.client.messages.create({
        model: CONFIG.claude.model,
        max_tokens: CONFIG.claude.max_tokens,
        system: SYSTEM_PROMPT,
        messages: [{ role: "user", content: prompt }],
      });

      const text =
        response.content[0].type === "text" ? response.content[0].text : "";
      return JSON.parse(text.trim()) as BrainDecision;
    } catch (e) {
      console.error("[BRAIN] Claude error, using fallback:", e);
      return this.fallback(events, indicators);
    }
  }

  private fallback(
    events: DetectedEvent[],
    _indicators: MultiTFIndicators
  ): BrainDecision {
    const bullish = events.filter((e) => e.direction === "bullish");
    const bearish = events.filter((e) => e.direction === "bearish");

    if (bullish.length > bearish.length && bullish.length >= 2) {
      return {
        action: "TRADE",
        direction: "LONG",
        conviction: 0.6 + bullish.length * 0.05,
        reasoning: `Fallback: ${bullish.length} bullish events detected`,
        suggested_stop_atr_mult: null,
      };
    }

    if (bearish.length > bullish.length && bearish.length >= 2) {
      return {
        action: "TRADE",
        direction: "SHORT",
        conviction: 0.6 + bearish.length * 0.05,
        reasoning: `Fallback: ${bearish.length} bearish events detected`,
        suggested_stop_atr_mult: null,
      };
    }

    return {
      action: "SKIP",
      direction: "LONG",
      conviction: 0,
      reasoning: "Fallback: insufficient confluence",
      suggested_stop_atr_mult: null,
    };
  }
}
