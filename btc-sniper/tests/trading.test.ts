import { describe, it, expect } from "vitest";
import { PositionManager } from "../src/trading/position-manager.js";
import { RiskManager } from "../src/trading/risk-manager.js";

describe("PositionManager", () => {
  it("opens and tracks position", () => {
    const pm = new PositionManager();
    pm.open("LONG", 68000, 10, 150, 0.8);
    expect(pm.hasPosition).toBe(true);
    expect(pm.position!.direction).toBe("LONG");
    expect(pm.position!.stopLoss).toBeCloseTo(68000 - 150 * 0.8);
  });

  it("updates trailing stop on new high (LONG)", () => {
    const pm = new PositionManager();
    pm.open("LONG", 68000, 10, 150, 0.8);
    const r1 = pm.tick(68200);
    expect(r1.action).toBe("HOLD");
    expect(pm.position!.stopLoss).toBeCloseTo(68200 - 120);
    const r2 = pm.tick(68100);
    expect(r2.action).toBe("HOLD");
    expect(pm.position!.stopLoss).toBeCloseTo(68200 - 120);
  });

  it("closes on trailing stop hit", () => {
    const pm = new PositionManager();
    pm.open("LONG", 68000, 10, 150, 0.8);
    const result = pm.tick(67800);
    expect(result.action).toBe("CLOSE");
    expect(result.reason).toBe("trailing_stop");
  });

  it("calculates unrealized PnL", () => {
    const pm = new PositionManager();
    pm.open("LONG", 68000, 10, 150, 0.8, 20);
    const pnl = pm.unrealizedPnl(68200);
    expect(pnl).toBeCloseTo(0.588, 1);
  });
});

describe("RiskManager", () => {
  it("computes Kelly stake", () => {
    const rm = new RiskManager(100);
    const stake = rm.computeStake(0.8, 0.25);
    expect(stake).toBeGreaterThan(0);
    expect(stake).toBeLessThanOrEqual(25);
  });

  it("blocks trade after 5 consecutive losses", () => {
    const rm = new RiskManager(100);
    for (let i = 0; i < 5; i++) rm.recordLoss();
    expect(rm.canTrade()).toBe(false);
  });

  it("blocks trade after max drawdown", () => {
    const rm = new RiskManager(100);
    rm.updateBankroll(84);
    expect(rm.canTrade()).toBe(false);
  });

  it("computes leverage from conviction and ATR", () => {
    const rm = new RiskManager(100);
    const lev = rm.computeLeverage(0.8, 0.2);
    expect(lev).toBeGreaterThanOrEqual(5);
    expect(lev).toBeLessThanOrEqual(50);
  });
});
