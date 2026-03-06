import json
from typing import Optional
import redis.asyncio as aioredis


class RedisClient:
    def __init__(self, url: str = "redis://localhost:6379", fake: bool = False):
        self._url = url
        self._fake = fake
        self._r: Optional[aioredis.Redis] = None

    async def connect(self):
        if self._fake:
            import fakeredis.aioredis
            self._r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        else:
            self._r = aioredis.from_url(self._url, decode_responses=True)

    async def close(self):
        if self._r:
            await self._r.aclose()

    async def publish(self, stream: str, data: dict):
        flat = {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in data.items()}
        await self._r.xadd(stream, flat, maxlen=1000)

    async def read(self, stream: str, last_id: str = "$", count: int = 10) -> list[dict]:
        try:
            exists = await self._r.exists(stream)
            if not exists:
                return []
            results = await self._r.xrange(stream, min=last_id, count=count) if last_id != "$" else []
            return [entry for _, entry in results]
        except Exception:
            return []

    async def read_latest(self, stream: str) -> Optional[dict]:
        try:
            results = await self._r.xrevrange(stream, count=1)
            if results:
                return results[0][1]
            return None
        except Exception:
            return None

    async def set_state(self, key: str, data: dict):
        await self._r.set(key, json.dumps(data))

    async def get_state(self, key: str) -> Optional[dict]:
        raw = await self._r.get(key)
        if raw:
            return json.loads(raw)
        return None
