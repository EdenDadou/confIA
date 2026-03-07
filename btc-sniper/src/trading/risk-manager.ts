import { CONFIG } from "../config/default.js";

export class RiskManager {
  bankroll: number;
  initialBankroll: number;
  consecutiveLosses = 0;
  winStreak = 0;
  pauseUntil = 0;

  constructor(bankroll: number) {
    this.bankroll = bankroll;
    this.initialBankroll = bankroll;
  }

  canTrade(): boolean {
    if (Date.now() < this.pauseUntil) return false;

    if (this.consecutiveLosses >= CONFIG.circuit.max_consecutive_losses) {
      this.pauseUntil = Date.now() + CONFIG.circuit.pause_after_losses_ms;
      return false;
    }

    const drawdown = (this.initialBankroll - this.bankroll) / this.initialBankroll;
    if (drawdown >= CONFIG.circuit.max_drawdown_pct) {
      this.pauseUntil = Date.now() + CONFIG.circuit.pause_after_drawdown_ms;
      return false;
    }

    return true;
  }

  computeStake(conviction: number, maxStakePct: number): number {
    const p = 0.5 + conviction * 0.3;
    const b = 1.5;
    const q = 1 - p;
    const kelly = Math.max(0, (p * b - q) / b) / 2;
    return Math.round(this.bankroll * Math.min(kelly, maxStakePct) * 100) / 100;
  }

  computeLeverage(conviction: number, atrPct: number): number {
    const base =
      CONFIG.trading.leverage_min +
      conviction * (CONFIG.trading.leverage_max - CONFIG.trading.leverage_min);
    const volFactor = Math.max(0.3, 1 - atrPct * 2);
    const lev = Math.round(base * volFactor);
    return Math.max(CONFIG.trading.leverage_min, Math.min(CONFIG.trading.leverage_max, lev));
  }

  recordWin(): void {
    this.consecutiveLosses = 0;
    this.winStreak++;
  }

  recordLoss(): void {
    this.consecutiveLosses++;
    this.winStreak = 0;
  }

  updateBankroll(newBankroll: number): void {
    this.bankroll = newBankroll;
  }
}
