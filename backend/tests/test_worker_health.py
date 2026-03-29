"""Test the worker health check HTTP server."""
import aiohttp
import pytest
from app.worker import start_health_server


@pytest.mark.asyncio
async def test_health_server_responds_200():
    """Health server should return 200 OK on GET /."""
    runner, site = await start_health_server(port=19999)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://localhost:19999/") as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "ok"
    finally:
        await runner.cleanup()
