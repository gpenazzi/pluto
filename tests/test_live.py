"""Opt-in checks against the real endpoints: `uv run pytest -m live`."""

import httpx
import pytest

from pluto.core.demo import DEMO_INSTRUMENTS
from pluto.market.resolver import InstrumentResolver
from pluto.market.service import QuoteService

pytestmark = pytest.mark.live


@pytest.fixture
async def client():
    async with httpx.AsyncClient(timeout=15) as c:
        yield c


async def test_every_demo_instrument_has_a_yahoo_quote(client):
    rows = await QuoteService(client).doctor(DEMO_INSTRUMENTS)
    failed = [r for r in rows if r.provider == "yahoo" and not r.ok]
    assert not failed, [(r.instrument_id, r.error) for r in failed]


async def test_justetf_quotes_every_demo_etf(client):
    rows = await QuoteService(client).doctor(DEMO_INSTRUMENTS)
    je = [r for r in rows if r.provider == "justetf" and r.error != "not supported"]
    assert je and all(r.ok for r in je), [(r.instrument_id, r.error) for r in je]


async def test_fx_usd_eur_from_both_providers(client):
    svc = QuoteService(client)
    for prov in svc.fx_providers:
        r = await prov.rate("USD", "EUR")
        assert 0.5 < float(r.rate) < 1.5, prov.name


async def test_resolve_vwce_by_isin(client):
    res = await InstrumentResolver(client).resolve(isin="IE00BK5BQT80")
    u = res.unique
    assert u is not None and u.currency == "EUR" and u.preferred_symbol.startswith("VWCE")
