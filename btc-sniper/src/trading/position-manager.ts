export interface Position {
  direction: "LONG" | "SHORT";
  entryPrice: number;
  stake: number;
  atr: number;
  stopMultiplier: number;
  stopDistance: number;
  stopLoss: number;
  highestPrice: number;
  lowestPrice: number;
  leverage: number;
  openTime: number;
}

export interface TickResult {
  action: "HOLD" | "CLOSE";
  reason?: string;
  pnl?: number;
}

export class PositionManager {
  position: Position | null = null;

  get hasPosition(): boolean {
    return this.position !== null;
  }

  open(
    direction: "LONG" | "SHORT",
    price: number,
    stake: number,
    atr: number,
    stopMultiplier = 0.8,
    leverage = 20
  ): void {
    const stopDistance = atr * stopMultiplier;
    this.position = {
      direction,
      entryPrice: price,
      stake,
      atr,
      stopMultiplier,
      stopDistance,
      leverage,
      stopLoss: direction === "LONG" ? price - stopDistance : price + stopDistance,
      highestPrice: direction === "LONG" ? price : 0,
      lowestPrice: direction === "SHORT" ? price : Infinity,
      openTime: Date.now(),
    };
  }

  tick(price: number): TickResult {
    if (!this.position) return { action: "HOLD" };
    const pos = this.position;

    if (pos.direction === "LONG") {
      if (price > pos.highestPrice) {
        pos.highestPrice = price;
        pos.stopLoss = Math.max(pos.stopLoss, price - pos.stopDistance);
      }
      if (price <= pos.stopLoss) {
        return { action: "CLOSE", reason: "trailing_stop", pnl: this.unrealizedPnl(price) };
      }
    } else {
      if (price < pos.lowestPrice) {
        pos.lowestPrice = price;
        pos.stopLoss = Math.min(pos.stopLoss, price + pos.stopDistance);
      }
      if (price >= pos.stopLoss) {
        return { action: "CLOSE", reason: "trailing_stop", pnl: this.unrealizedPnl(price) };
      }
    }

    return { action: "HOLD", pnl: this.unrealizedPnl(price) };
  }

  unrealizedPnl(price: number): number {
    if (!this.position) return 0;
    const { direction, entryPrice, stake, leverage } = this.position;
    const pct =
      direction === "LONG"
        ? (price - entryPrice) / entryPrice
        : (entryPrice - price) / entryPrice;
    return Math.round(stake * pct * leverage * 10000) / 10000;
  }

  close(price: number, reason = "manual") {
    if (!this.position) throw new Error("No position");
    const pnl = this.unrealizedPnl(price);
    const result = {
      pnl,
      duration_s: Math.round((Date.now() - this.position.openTime) / 1000),
      direction: this.position.direction,
      entryPrice: this.position.entryPrice,
      exitPrice: price,
      reason,
    };
    this.position = null;
    return result;
  }

  getState(): Position | null {
    return this.position ? { ...this.position } : null;
  }
}
