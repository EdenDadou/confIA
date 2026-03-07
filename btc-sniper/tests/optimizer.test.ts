import { describe, it, expect } from "vitest";
import { Optimizer } from "../src/learning/optimizer.js";

describe("Optimizer", () => {
  it("adjusts min_conviction up after losses", () => {
    const opt = new Optimizer(false);
    const initial = opt.params.min_conviction;
    for (let i = 0; i < 8; i++) opt.recordOutcome(false, -0.5, ["RSI_EXTREME"]);
    for (let i = 0; i < 2; i++) opt.recordOutcome(true, 1.0, ["RSI_EXTREME"]);
    expect(opt.params.min_conviction).toBeGreaterThan(initial);
  });

  it("adjusts event reliability", () => {
    const opt = new Optimizer(false);
    for (let i = 0; i < 5; i++) opt.recordOutcome(true, 0.5, ["RSI_EXTREME"]);
    opt.recordOutcome(false, -0.3, ["RSI_EXTREME"]);
    opt.recordOutcome(true, 0.5, ["EMA_CROSS"]);
    for (let i = 0; i < 5; i++) opt.recordOutcome(false, -0.3, ["EMA_CROSS"]);
    expect(opt.params.event_reliability["RSI_EXTREME"]).toBeGreaterThan(
      opt.params.event_reliability["EMA_CROSS"]
    );
  });
});
