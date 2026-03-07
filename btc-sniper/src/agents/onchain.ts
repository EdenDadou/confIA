export interface OnChainData { funding_rate: number; long_short_ratio: number; open_interest: number; }

export class OnChainAgent {
  private lastData: OnChainData = { funding_rate: 0.0001, long_short_ratio: 1.0, open_interest: 0 };
  private lastFetch = 0;
  private prevFundingRate = 0.0001;

  async fetch(): Promise<OnChainData> {
    if (Date.now() - this.lastFetch < 30_000) return this.lastData;
    this.prevFundingRate = this.lastData.funding_rate;
    try {
      const [frRes, lsRes, oiRes] = await Promise.allSettled([
        fetch("https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1"),
        fetch("https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol=BTCUSDT&period=5m&limit=1"),
        fetch("https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT"),
      ]);
      if (frRes.status === "fulfilled" && frRes.value.ok) {
        const d = (await frRes.value.json()) as any[];
        if (d?.[0]) this.lastData.funding_rate = parseFloat(d[0].fundingRate);
      }
      if (lsRes.status === "fulfilled" && lsRes.value.ok) {
        const d = (await lsRes.value.json()) as any[];
        if (d?.[0]) this.lastData.long_short_ratio = parseFloat(d[0].longShortRatio);
      }
      if (oiRes.status === "fulfilled" && oiRes.value.ok) {
        const d = (await oiRes.value.json()) as any;
        if (d) this.lastData.open_interest = parseFloat(d.openInterest);
      }
      this.lastFetch = Date.now();
    } catch { /* keep last */ }
    return this.lastData;
  }

  get data(): OnChainData { return this.lastData; }
  get previousFundingRate(): number { return this.prevFundingRate; }
}
