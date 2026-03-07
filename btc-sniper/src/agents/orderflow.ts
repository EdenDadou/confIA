export interface OrderFlowData { ob_ratio: number; best_bid: number; best_ask: number; spread: number; bids: [number, number][]; asks: [number, number][]; }

export class OrderFlowAgent {
  private lastData: OrderFlowData = { ob_ratio: 0.5, best_bid: 0, best_ask: 0, spread: 0, bids: [], asks: [] };
  private lastFetch = 0;

  async fetch(): Promise<OrderFlowData> {
    if (Date.now() - this.lastFetch < 5_000) return this.lastData;
    try {
      const res = await fetch("https://api.binance.com/api/v3/depth?symbol=BTCUSDT&limit=20");
      const data = (await res.json()) as any;
      const bids: [number, number][] = (data.bids || []).map((b: string[]) => [parseFloat(b[0]), parseFloat(b[1])]);
      const asks: [number, number][] = (data.asks || []).map((a: string[]) => [parseFloat(a[0]), parseFloat(a[1])]);
      const bidVol = bids.reduce((s, b) => s + b[1], 0);
      const askVol = asks.reduce((s, a) => s + a[1], 0);
      const total = bidVol + askVol;
      this.lastData = { ob_ratio: total > 0 ? bidVol / total : 0.5, best_bid: bids[0]?.[0] ?? 0, best_ask: asks[0]?.[0] ?? 0, spread: (asks[0]?.[0] ?? 0) - (bids[0]?.[0] ?? 0), bids, asks };
      this.lastFetch = Date.now();
    } catch { /* keep last */ }
    return this.lastData;
  }

  get data(): OrderFlowData { return this.lastData; }
}
