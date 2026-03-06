import asyncio
import json
import httpx
import anthropic
from shared.redis_client import RedisClient

STREAM = "signals:onchain"

SYSTEM_PROMPT = """You are Viktor Petrov, senior on-chain analyst based in Zurich.
15 years analyzing blockchain flows, derivatives data, and whale movements.
You are cold, data-driven, and follow the smart money. You never trust price — you trust flows.

You receive on-chain and derivatives data for BTC and must interpret it.

RESPOND ONLY WITH THIS JSON (no markdown, no extra text):
{
  "score": <int 0-100>,
  "direction": "<up|down|neutral>",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<1-2 sentences in English explaining what the smart money is doing>"
}

Key principles:
- High positive funding + rising OI = overleveraged longs, bearish
- Negative funding + shorts getting liquidated = short squeeze brewing, bullish
- High long/short ratio (>2.0) = crowded trade, contrarian bearish
- Low long/short ratio (<0.7) = contrarian bullish
- Massive liquidations on one side = that side is getting flushed, opposite direction likely"""


class OnChainAgent:
    def __init__(self, redis_url: str = "redis://localhost:6379", fake_redis: bool = False):
        self.rc = RedisClient(url=redis_url, fake=fake_redis)
        self.client = anthropic.AsyncAnthropic()

    async def fetch_binance_futures(self) -> dict:
        defaults = {
            "funding_rate": 0.0001,
            "long_short_ratio": 1.0,
            "open_interest": 0,
            "liquidations": {"long": 0, "short": 0},
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                fr_r, ls_r, oi_r = await asyncio.gather(
                    client.get("https://fapi.binance.com/fapi/v1/fundingRate",
                               params={"symbol": "BTCUSDT", "limit": 1}),
                    client.get("https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
                               params={"symbol": "BTCUSDT", "period": "5m", "limit": 1}),
                    client.get("https://fapi.binance.com/fapi/v1/openInterest",
                               params={"symbol": "BTCUSDT"}),
                    return_exceptions=True,
                )
            result = dict(defaults)
            if not isinstance(fr_r, Exception) and fr_r.status_code == 200:
                data = fr_r.json()
                if data:
                    result["funding_rate"] = float(data[0].get("fundingRate", 0.0001))
            if not isinstance(ls_r, Exception) and ls_r.status_code == 200:
                data = ls_r.json()
                if data:
                    result["long_short_ratio"] = float(data[0].get("longShortRatio", 1.0))
            if not isinstance(oi_r, Exception) and oi_r.status_code == 200:
                data = oi_r.json()
                result["open_interest"] = float(data.get("openInterest", 0))
            return result
        except Exception:
            return defaults

    async def scrape_coinglass_liquidations(self) -> dict:
        defaults = {"long": 0, "short": 0}
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto("https://www.coinglass.com/LiquidationData", timeout=15000)
                await page.wait_for_timeout(5000)
                rows = await page.query_selector_all("table tbody tr")
                for row in rows:
                    text = await row.inner_text()
                    if "BTC" in text:
                        cells = await row.query_selector_all("td")
                        if len(cells) >= 4:
                            try:
                                long_val = (await cells[2].inner_text()).strip()
                                short_val = (await cells[3].inner_text()).strip()
                                def parse_val(s):
                                    s = s.replace("$", "").replace(",", "").strip()
                                    if "M" in s: return float(s.replace("M", "")) * 1e6
                                    if "K" in s: return float(s.replace("K", "")) * 1e3
                                    return float(s) if s else 0
                                await browser.close()
                                return {"long": parse_val(long_val), "short": parse_val(short_val)}
                            except (ValueError, IndexError):
                                pass
                await browser.close()
        except Exception:
            pass
        return defaults

    async def interpret(self, data: dict) -> dict:
        liqs = data.get("liquidations", {"long": 0, "short": 0})
        prompt = f"""BTC On-Chain & Derivatives Data:
- Funding Rate: {data['funding_rate']:.6f} ({'+' if data['funding_rate'] > 0 else ''}{data['funding_rate']*100:.4f}%)
- Long/Short Ratio: {data['long_short_ratio']:.2f}
- Open Interest: {data['open_interest']:,.0f} BTC
- 24h Liquidations: Long=${liqs['long']:,.0f} / Short=${liqs['short']:,.0f}"""

        try:
            response = await self.client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=256,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            return json.loads(response.content[0].text.strip())
        except Exception:
            return self._fallback_score(data)

    def _fallback_score(self, data: dict) -> dict:
        fr = data.get("funding_rate", 0.0001)
        ls = data.get("long_short_ratio", 1.0)
        if fr > 0.0003:
            fr_score = 25
        elif fr > 0.0001:
            fr_score = 40
        elif fr > -0.0001:
            fr_score = 50
        elif fr > -0.0003:
            fr_score = 62
        else:
            fr_score = 75
        if ls > 2.0:
            ls_score = 30
        elif ls > 1.5:
            ls_score = 40
        elif ls > 0.8:
            ls_score = 50
        else:
            ls_score = 65
        score = max(0, min(100, int(0.55 * fr_score + 0.45 * ls_score)))
        direction = "up" if score >= 58 else "down" if score <= 42 else "neutral"
        return {
            "score": score,
            "direction": direction,
            "confidence": 0.5,
            "reasoning": f"Fallback: funding={fr:.4%}, L/S ratio={ls:.2f}"
        }

    async def run(self):
        await self.rc.connect()
        try:
            while True:
                futures_data = await self.fetch_binance_futures()
                liqs = await self.scrape_coinglass_liquidations()
                futures_data["liquidations"] = liqs
                interpretation = await self.interpret(futures_data)
                await self.rc.publish(STREAM, {
                    **interpretation,
                    "funding_rate": futures_data["funding_rate"],
                    "long_short_ratio": futures_data["long_short_ratio"],
                    "open_interest": futures_data["open_interest"],
                    "liquidations": json.dumps(liqs),
                })
                await asyncio.sleep(45)
        finally:
            await self.rc.close()
