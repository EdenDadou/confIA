import { describe, it, expect } from "vitest";
import { EventDetector, type DetectedEvent } from "../src/engine/event-detector.js";

describe("EventDetector", () => {
  it("detects RSI extreme", () => {
    const detector = new EventDetector();
    const indicators = {
      "1m": { rsi: 22, ema12: 100, ema26: 101, macd_hist: -1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 200, volumeSma: 100, price: 96 },
      "5m": { rsi: 30, ema12: 100, ema26: 101, macd_hist: -1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 100, volumeSma: 100, price: 96 },
      "15m": { rsi: 45, ema12: 100, ema26: 99, macd_hist: 1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 100, volumeSma: 100, price: 100 },
    };
    const events = detector.detect(indicators);
    expect(events.some((e) => e.type === "RSI_EXTREME")).toBe(true);
  });

  it("respects cooldown", () => {
    const detector = new EventDetector();
    const indicators = {
      "1m": { rsi: 22, ema12: 100, ema26: 101, macd_hist: -1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 200, volumeSma: 100, price: 96 },
      "5m": { rsi: 30, ema12: 100, ema26: 101, macd_hist: -1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 100, volumeSma: 100, price: 96 },
      "15m": { rsi: 45, ema12: 100, ema26: 99, macd_hist: 1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 100, volumeSma: 100, price: 100 },
    };
    const events1 = detector.detect(indicators);
    expect(events1.length).toBeGreaterThan(0);
    const events2 = detector.detect(indicators);
    expect(events2.filter((e) => e.type === "RSI_EXTREME")).toHaveLength(0);
  });

  it("detects volume spike", () => {
    const detector = new EventDetector();
    const indicators = {
      "1m": { rsi: 50, ema12: 100, ema26: 99, macd_hist: 1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 400, volumeSma: 100, price: 100 },
      "5m": { rsi: 50, ema12: 100, ema26: 99, macd_hist: 1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 100, volumeSma: 100, price: 100 },
      "15m": { rsi: 50, ema12: 100, ema26: 99, macd_hist: 1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 100, volumeSma: 100, price: 100 },
    };
    const events = detector.detect(indicators);
    expect(events.some((e) => e.type === "VOLUME_SPIKE")).toBe(true);
  });

  it("calculates confluence score", () => {
    const detector = new EventDetector();
    const indicators = {
      "1m": { rsi: 22, ema12: 100, ema26: 101, macd_hist: -1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 400, volumeSma: 100, price: 94 },
      "5m": { rsi: 30, ema12: 100, ema26: 101, macd_hist: -1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 100, volumeSma: 100, price: 94 },
      "15m": { rsi: 45, ema12: 100, ema26: 99, macd_hist: 1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 100, volumeSma: 100, price: 100 },
    };
    const events = detector.detect(indicators);
    expect(events.length).toBeGreaterThanOrEqual(2);
  });
});
