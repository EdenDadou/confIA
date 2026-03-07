import asyncio
import json
import httpx
import anthropic
from shared.redis_client import RedisClient

STREAM = "signals:sentiment"

SYSTEM_PROMPT = """You are Sarah Mitchell, behavioral finance expert based in London.
PhD in Market Psychology from LSE, 12 years at top hedge funds analyzing crowd behavior.
You are contrarian by nature — when the crowd panics, you see opportunity. When euphoria reigns, you prepare for the fall.

You receive the Crypto Fear & Greed Index (0=Extreme Fear, 100=Extreme Greed) and must interpret it.

RESPOND ONLY WITH THIS JSON (no markdown, no extra text):
{
  "score": <int 0-100>,
  "direction": "<up|down|neutral>",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<1-2 sentences in English explaining your contrarian analysis>"
}

Score guide: 50=neutral, >60=bullish (you see fear as buying opportunity), <40=bearish (you see greed as sell signal).
Extreme readings (below 20 or above 80) are your strongest signals. Moderate readings (30-70) mean less conviction."""


class SentimentAgent:
    def __init__(self, redis_url: str = "redis://localhost:6379", fake_redis: bool = False):
        self.rc = RedisClient(url=redis_url, fake=fake_redis)
        self.client = anthropic.AsyncAnthropic()

    async def fetch_fear_greed(self) -> int:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get("https://api.alternative.me/fng/?limit=1")
                return int(r.json()["data"][0]["value"])
        except Exception:
            return 50

    async def interpret(self, fear_greed: int) -> dict:
        prompt = f"""Current Crypto Fear & Greed Index: {fear_greed}/100
Classification: {"Extreme Fear" if fear_greed < 20 else "Fear" if fear_greed < 40 else "Neutral" if fear_greed < 60 else "Greed" if fear_greed < 80 else "Extreme Greed"}"""

        try:
            response = await self.client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=256,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            return json.loads(response.content[0].text.strip())
        except Exception:
            return self._fallback_score(fear_greed)

    def _fallback_score(self, fear_greed: int) -> dict:
        # Contrarian: fear = bullish, greed = bearish
        if fear_greed <= 25:
            score = int(80 - fear_greed * 0.6)
        elif fear_greed <= 45:
            score = int(65 - (fear_greed - 25) * 0.5)
        elif fear_greed <= 55:
            score = 50
        elif fear_greed <= 75:
            score = int(45 - (fear_greed - 55) * 0.5)
        else:
            score = int(35 - (fear_greed - 75) * 0.6)
        score = max(0, min(100, score))
        direction = "up" if score >= 52 else "down" if score <= 48 else "neutral"
        return {
            "score": score,
            "direction": direction,
            "confidence": 0.5,
            "reasoning": f"Fallback contrarian: F&G={fear_greed}, {'fear=buy signal' if fear_greed < 40 else 'greed=sell signal' if fear_greed > 60 else 'neutral zone'}"
        }

    async def run(self):
        await self.rc.connect()
        try:
            while True:
                fg = await self.fetch_fear_greed()
                interpretation = await self.interpret(fg)
                await self.rc.publish(STREAM, {
                    **interpretation,
                    "fear_greed": fg,
                })
                await asyncio.sleep(3600)
        finally:
            await self.rc.close()
