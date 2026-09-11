from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import respx

from pluto.api.app import create_app
from pluto.chat.providers.scripted import ScriptedProvider
from pluto.core.demo import demo_portfolio
from pluto.market.providers.yahoo import CHART_URL
from pluto.store import paths
from pluto.store.versions import VersionStore
from tests.test_market import chart


@pytest.fixture
async def client(pluto_home: Path):
    VersionStore(paths.portfolio_dir("t")).commit(demo_portfolio("t"), "Demo")
    app = create_app("t", static_dir=None)
    app.state.pluto.ctx.quotes.ttl = timedelta(0)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            c.app = app  # type: ignore[attr-defined]
            yield c


async def test_portfolio_transactions_versions(client: httpx.AsyncClient):
    r = await client.get("/api/portfolio")
    assert r.status_code == 200 and r.json()["version"] == 1
    r = await client.get("/api/transactions?last=5")
    assert len(r.json()["transactions"]) == 5
    r = await client.get("/api/versions")
    assert r.json()["head"] == 1


async def test_add_remove_revert_flow(client: httpx.AsyncClient):
    r = await client.post(
        "/api/transactions",
        json={"type": "buy", "instrument": "ENI.MI", "quantity": "10", "price": "24"},
    )
    assert r.status_code == 200 and r.json()["version"] == 2
    assert r.json()["transaction"]["source"] == "gui"
    tx_id = r.json()["transaction"]["id"]
    r = await client.post(
        "/api/transactions",
        json={"type": "sell", "instrument": "ENI.MI", "quantity": "9999", "price": "1"},
    )
    assert r.status_code == 400 and "cannot sell" in r.json()["error"]
    r = await client.delete(f"/api/transactions/{tx_id}")
    assert r.status_code == 200 and r.json()["version"] == 3
    r = await client.post("/api/versions/2/revert")
    assert r.json()["version"] == 4
    r = await client.post("/api/undo")
    assert r.json()["version"] == 5
    assert (await client.get("/api/portfolio")).json()["transactions_count"] == len(
        demo_portfolio().transactions
    )


@respx.mock
async def test_valuation_reports_provider_health(client: httpx.AsyncClient):
    respx.get(url__regex=r"https://query1\.finance\.yahoo\.com/.*").mock(
        return_value=httpx.Response(500)
    )
    respx.get(url__regex=r"https://www\.justetf\.com/.*").mock(return_value=httpx.Response(503))
    respx.get(url__regex=r"https://api\.frankfurter\.dev/.*").mock(return_value=httpx.Response(503))
    r = await client.get("/api/valuation")
    assert r.status_code == 200
    body = r.json()
    assert len(body["missing_prices"]) == 6
    assert {p["name"] for p in body["providers"]} == {"yahoo", "justetf", "frankfurter"}
    assert any(p["failures"] > 0 for p in body["providers"])


@respx.mock
async def test_valuation_ok(client: httpx.AsyncClient):
    for sym, px, cur in [
        ("VWCE.MI", 166, "EUR"),
        ("CSSPX.MI", 600, "EUR"),
        ("AGGH.MI", 5, "EUR"),
        ("AAPL", 200, "USD"),
        ("MSFT", 500, "USD"),
        ("ENI.MI", 24, "EUR"),
        ("USDEUR=X", 0.5, "EUR"),
    ]:
        respx.get(CHART_URL.format(symbol=sym)).mock(
            return_value=httpx.Response(200, json=chart(px, cur))
        )
    body = (await client.get("/api/valuation")).json()
    assert body["missing_prices"] == [] and float(body["total_value"]) > 0
    assert body["breakdown"]["asset_class"][0]["label"] == "equity"


async def test_chat_streams_events_and_keeps_history(client: httpx.AsyncClient):
    state = client.app.state.pluto  # type: ignore[attr-defined]
    state.provider = ScriptedProvider(
        state.registry,
        [
            [
                ("tool", "add_transaction", {"type": "deposit", "amount": "500"}),
                ("text", "Deposited 500 EUR (version 2)."),
            ]
        ],
    )
    async with client.stream("POST", "/api/chat", json={"message": "deposit 500"}) as r:
        assert r.status_code == 200
        raw = "".join([chunk async for chunk in r.aiter_text()])
    assert "event: tool_call" in raw and "event: text" in raw and "event: done" in raw
    assert "\r" not in raw and "\n\n" in raw  # framing the web client parses
    hist = (await client.get("/api/chat/messages")).json()
    assert [m["role"] for m in hist["messages"]] == ["user", "assistant", "assistant", "assistant"]
    assert hist["messages"][-1]["text"].startswith("Deposited")
    assert (await client.get("/api/versions")).json()["head"] == 2
