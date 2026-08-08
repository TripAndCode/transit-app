import os

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from api.clickhouse import get_ch_client
from api.deps import get_ch


@pytest.mark.skipif(os.environ.get("RUN_CH_INTEGRATION") != "1", reason="requires `make ch-test`")
@pytest.mark.asyncio
async def test_get_ch_dependency_returns_working_client():
    app = FastAPI()
    app.state.ch_client = await get_ch_client()

    @app.get("/probe")
    async def probe(ch=Depends(get_ch)):
        result = await ch.query("SELECT 1")
        return {"rows": [list(row) for row in result.result_rows]}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/probe")
    assert resp.status_code == 200
    assert resp.json() == {"rows": [[1]]}
    await app.state.ch_client.close()
