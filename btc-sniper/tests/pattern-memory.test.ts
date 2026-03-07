import { describe, it, expect } from "vitest";
import { PatternMemory, type SetupRecord } from "../src/learning/pattern-memory.js";

describe("PatternMemory", () => {
  it("stores and retrieves setups", () => {
    const mem = new PatternMemory(false);
    mem.record({
      events: ["RSI_EXTREME", "VOLUME_SPIKE"],
      indicators_1m: { rsi: 22 },
      indicators_5m: { rsi: 35 },
      direction: "LONG",
      conviction: 0.8,
    });
    expect(mem.totalSetups()).toBe(1);
  });

  it("finds similar setups", () => {
    const mem = new PatternMemory(false);
    mem.record({ events: ["RSI_EXTREME"], indicators_1m: { rsi: 22 }, indicators_5m: { rsi: 30 }, direction: "LONG", conviction: 0.7 });
    mem.record({ events: ["RSI_EXTREME", "VOLUME_SPIKE"], indicators_1m: { rsi: 24 }, indicators_5m: { rsi: 32 }, direction: "LONG", conviction: 0.8 });
    mem.record({ events: ["EMA_CROSS"], indicators_1m: { rsi: 55 }, indicators_5m: { rsi: 60 }, direction: "SHORT", conviction: 0.6 });
    mem.recordOutcome(0, { won: true, pnl_pct: 0.5, duration_s: 200, exit_reason: "trailing_stop" });
    mem.recordOutcome(1, { won: true, pnl_pct: 0.8, duration_s: 300, exit_reason: "trailing_stop" });
    const similar = mem.findSimilar(["RSI_EXTREME"], { rsi: 23 }, 5);
    expect(similar.length).toBe(2);
    expect(similar[0].events).toContain("RSI_EXTREME");
  });

  it("computes win rate for event type", () => {
    const mem = new PatternMemory(false);
    mem.record({ events: ["RSI_EXTREME"], indicators_1m: { rsi: 22 }, indicators_5m: {}, direction: "LONG", conviction: 0.8 });
    mem.record({ events: ["RSI_EXTREME"], indicators_1m: { rsi: 24 }, indicators_5m: {}, direction: "LONG", conviction: 0.7 });
    mem.record({ events: ["RSI_EXTREME"], indicators_1m: { rsi: 20 }, indicators_5m: {}, direction: "LONG", conviction: 0.9 });
    mem.recordOutcome(0, { won: true, pnl_pct: 1.0, duration_s: 100, exit_reason: "trailing_stop" });
    mem.recordOutcome(1, { won: false, pnl_pct: -0.5, duration_s: 60, exit_reason: "trailing_stop" });
    mem.recordOutcome(2, { won: true, pnl_pct: 0.8, duration_s: 150, exit_reason: "trailing_stop" });
    const stats = mem.eventStats("RSI_EXTREME");
    expect(stats.total).toBe(3);
    expect(stats.wins).toBe(2);
    expect(stats.winRate).toBeCloseTo(0.667, 1);
  });
});
