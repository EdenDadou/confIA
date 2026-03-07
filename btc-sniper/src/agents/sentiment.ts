export interface SentimentData { fear_greed: number; fear_greed_label: string; }

export class SentimentAgent {
  private lastValue: SentimentData = { fear_greed: 50, fear_greed_label: "Neutral" };
  private lastFetch = 0;

  async fetch(): Promise<SentimentData> {
    if (Date.now() - this.lastFetch < 5 * 60_000) return this.lastValue;
    try {
      const res = await fetch("https://api.alternative.me/fng/?limit=1");
      const data = (await res.json()) as any;
      if (data?.data?.[0]) {
        const val = parseInt(data.data[0].value, 10);
        this.lastValue = { fear_greed: val, fear_greed_label: data.data[0].value_classification };
      }
      this.lastFetch = Date.now();
    } catch { /* keep last */ }
    return this.lastValue;
  }

  get data(): SentimentData { return this.lastValue; }
}
