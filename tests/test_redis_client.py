import pytest
from shared.redis_client import RedisClient


@pytest.fixture
async def rclient():
    rc = RedisClient(url="redis://localhost:6379", fake=True)
    await rc.connect()
    yield rc
    await rc.close()


@pytest.mark.asyncio
async def test_publish_and_read(rclient):
    await rclient.publish("test:stream", {"source": "market", "score": 75})
    messages = await rclient.read("test:stream", last_id="0")
    assert len(messages) >= 1
    data = messages[-1]
    assert data["source"] == "market"
    assert data["score"] == "75"


@pytest.mark.asyncio
async def test_set_get_state(rclient):
    await rclient.set_state("position:current", {"direction": "LONG", "entry": 95000})
    state = await rclient.get_state("position:current")
    assert state["direction"] == "LONG"


@pytest.mark.asyncio
async def test_read_empty_stream(rclient):
    messages = await rclient.read("empty:stream", last_id="0")
    assert messages == []
