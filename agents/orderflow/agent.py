import asyncio
import json
import httpx
import anthropic
from shared.redis_client import RedisClient

STREAM = "signals:orderflow"

SYSTEM_PROMPT = """You are Diego Ramirez, former pit trader from Chicago, now electronic market maker.
18 years reading order flow, tape, and microstructure. You feel the market pressure before it moves.
You are instinctive but disciplined — you read the book like a poker player reads tells.

You receive MEXC BTC/USDT perpetual order book data and must interpret the flow.

RESPOND ONLY WITH THIS JSON (no markdown, no extra text):
{
  "score": <int 0-100>,
  "direction": "<up|down|neutral>",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<1-2 sentences in English about what the order flow tells you>"
}

Key principles:
- OB ratio >0.6 = strong bid support, buyers are aggressive, bullish
- OB ratio <0.4 = sellers dominating, bearish
- High positive funding = longs are paying, potential squeeze down
- Negative funding = shorts paying, short squeeze potential
- Tight spread = healthy market, wide spread = uncertainty"""


class OrderFlowAgent:
    def __init__(self, redis_url: str = "redis://localhost:6379", fake_redis: bool = False):
        self.rc = RedisClient(url=redis_url, fake=fake_redis)
        self.client = anthropic.AsyncAnthropic()

    async def fetch_mexc_data(self) -> dict:
        BASE = "https://contract.mexc.com/api/v1/contract"
        default = {"ob_ratio": 0.5, "funding_rate": 0.0001, "spread_bps": 2.0,
                   "bids": [], "asks": [], "mark_price": 0, "index_price": 0,
                   "bid_vol": 0, "ask_vol": 0}
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                ticker_r, depth_r, funding_r = await asyncio.gather(
                    client.get(f"{BASE}/ticker", params={"symbol": "BTC_USDT"}),
                    client.get(f"{BASE}/depth/BTC_USDT", params={"limit": 8}),
                    client.get(f"{BASE}/funding_rate/BTC_USDT"),
                    return_exceptions=True,
                )
            result = dict(default)
            if not isinstance(ticker_r, Exception) and ticker_r.status_code == 200:
                td = ticker_r.json().get("data", {})
                result["mark_price"] = float(td.get("fairPrice", 0) or 0)
                result["index_price"] = float(td.get("indexPrice", 0) or 0)
                result["funding_rate"] = float(td.get("fundingRate", 0.0001) or 0.0001)
            if not isinstance(depth_r, Exception) and depth_r.status_code == 200:
                dd = depth_r.json().get("data", {})
                bids = [(float(r[0]), float(r[1])) for r in (dd.get("bids") or [])]
                asks = [(float(r[0]), float(r[1])) for r in (dd.get("asks") or [])]
                bid_vol = sum(v for _, v in bids)
                ask_vol = sum(v for _, v in asks)
                total = bid_vol + ask_vol
                result["bids"] = bids[:6]
                result["asks"] = asks[:6]
                result["bid_vol"] = bid_vol
                result["ask_vol"] = ask_vol
                result["ob_ratio"] = round(bid_vol / total, 3) if total > 0 else 0.5
                if bids and asks:
                    spread = asks[0][0] - bids[0][0]
                    mid = (asks[0][0] + bids[0][0]) / 2
                    result["spread_bps"] = round(spread / mid * 10000, 2) if mid else 2.0
            if not isinstance(funding_r, Exception) and funding_r.status_code == 200:
                fd = funding_r.json().get("data", {})
                if fd.get("fundingRate") is not None:
                    result["funding_rate"] = float(fd["fundingRate"])
            return result
        except Exception:
            return default

    async def interpret(self, data: dict) -> dict:
        prompt = f"""MEXC BTC/USDT Perp Order Flow:
- Order Book Ratio: {data['ob_ratio']*100:.1f}% bids / {(1-data['ob_ratio'])*100:.1f}% asks
- Bid Volume: {data['bid_vol']:,.0f} | Ask Volume: {data['ask_vol']:,.0f}
- Spread: {data['spread_bps']:.1f} bps
- Funding Rate: {data['funding_rate']:.6f} ({'+' if data['funding_rate'] > 0 else ''}{data['funding_rate']*100:.4f}%)
- Mark Price: ${data['mark_price']:,.2f}"""

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
        ob = data.get("ob_ratio", 0.5)
        fr = data.get("funding_rate", 0.0001)
        ob_score = int(ob * 100)
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
        score = max(0, min(100, int(0.60 * ob_score + 0.40 * fr_score)))
        direction = "up" if score >= 52 else "down" if score <= 48 else "neutral"
        return {
            "score": score,
            "direction": direction,
            "confidence": 0.5,
            "reasoning": f"Fallback: OB ratio={ob:.1%}, funding={fr:.4%}"
        }

    async def run(self):
        await self.rc.connect()
        try:
            while True:
                data = await self.fetch_mexc_data()
                interpretation = await self.interpret(data)
                await self.rc.publish(STREAM, {
                    **interpretation,
                    "ob_ratio": data["ob_ratio"],
                    "funding_rate": data["funding_rate"],
                    "spread_bps": data["spread_bps"],
                    "mark_price": data["mark_price"],
                    "index_price": data["index_price"],
                    "bids": json.dumps(data["bids"]),
                    "asks": json.dumps(data["asks"]),
                })
                await asyncio.sleep(5)
        finally:
            await self.rc.close()
