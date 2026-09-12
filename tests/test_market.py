from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

import httpx
import pytest
import respx

from pluto.core.demo import DEMO_INSTRUMENTS, demo_portfolio
from pluto.core.model import AssetType, Instrument, Listing
from pluto.market.cache import QuoteCache
from pluto.market.providers.frankfurter import URL as FRANKFURTER_URL
from pluto.market.providers.justetf import JustEtfProvider
from pluto.market.providers.openfigi import SEARCH_URL as FIGI_SEARCH_URL
from pluto.market.providers.openfigi import URL as FIGI_URL
from pluto.market.providers.yahoo import CHART_URL, SEARCH_URL, YahooProvider
from pluto.market.resolver import InstrumentResolver, guess_asset_class, is_isin
from pluto.market.service import QuoteService
from pluto.market.types import ProviderHealth

VWCE = DEMO_INSTRUMENTS[0]
AAPL = DEMO_INSTRUMENTS[3]


def chart(price: float, currency: str = "EUR", ts: int = 1788968116, **meta) -> dict:
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "currency": currency,
                        "regularMarketPrice": price,
                        "regularMarketTime": ts,
                        "previousClose": price - 1,
                        "exchangeName": "MIL",
                        "instrumentType": "ETF",
                        "longName": "Vanguard FTSE All-World UCITS ETF USD Accumulation",
                        **meta,
                    }
                }
            ],
            "error": None,
        }
    }


NOT_FOUND = {"chart": {"result": None, "error": {"code": "Not Found", "description": "No data"}}}


@pytest.fixture
async def client():
    async with httpx.AsyncClient() as c:
        yield c


@respx.mock
async def test_yahoo_tries_listings_in_order_and_normalises_pence(client):
    respx.get(CHART_URL.format(symbol="VWCE.MI")).mock(
        return_value=httpx.Response(404, json=NOT_FOUND)
    )
    respx.get(CHART_URL.format(symbol="VWCE.DE")).mock(
        return_value=httpx.Response(200, json=chart(16612, "GBp"))
    )
    q = await YahooProvider(client).quote(VWCE)
    assert q.symbol == "VWCE.DE"
    assert q.price == D("166.12") and q.currency == "GBP"
    assert q.previous_close == D("166.11")
    assert q.as_of == datetime(2026, 9, 9, 15, 35, 16, tzinfo=UTC)
    assert q.source.startswith("yahoo")


@respx.mock
async def test_justetf_quote(client):
    respx.get(f"https://www.justetf.com/api/etfs/{VWCE.isin}/quote").mock(
        return_value=httpx.Response(
            200,
            json={
                "latestQuote": {"raw": 166.12},
                "latestQuoteDate": "2026-09-09",
                "previousQuote": {"raw": 167.62},
                "quoteTradingVenue": "XETRA",
            },
        )
    )
    q = await JustEtfProvider(client).quote(VWCE)
    assert q.price == D("166.12") and q.currency == "EUR" and q.source == "justetf"
    assert not JustEtfProvider(client).supports(AAPL)


@respx.mock
async def test_service_falls_back_to_justetf_then_cache(client, tmp_path: Path):
    for sym in ("VWCE.MI", "VWCE.DE", "VWRA.L"):
        respx.get(CHART_URL.format(symbol=sym)).mock(return_value=httpx.Response(500))
    je = respx.get(f"https://www.justetf.com/api/etfs/{VWCE.isin}/quote").mock(
        return_value=httpx.Response(
            200, json={"latestQuote": {"raw": 100}, "latestQuoteDate": "2026-09-09"}
        )
    )
    cache = QuoteCache(tmp_path / "q.json")
    svc = QuoteService(client, cache=cache, ttl=timedelta(0))
    q = await svc.quote(VWCE)
    assert q is not None and q.source == "justetf" and not q.is_stale
    assert svc.health["yahoo"].consecutive_failures == 1
    assert svc.health["justetf"].consecutive_failures == 0

    je.mock(return_value=httpx.Response(503))
    q2 = await svc.quote(VWCE)
    assert q2 is not None and q2.is_stale and q2.price == D("100")

    # cache survives on disk
    svc.cache.save()
    again = QuoteCache(tmp_path / "q.json")
    assert again.get_quote(VWCE.id) is not None


@respx.mock
async def test_service_respects_ttl(client):
    route = respx.get(CHART_URL.format(symbol="VWCE.MI")).mock(
        return_value=httpx.Response(200, json=chart(166.0))
    )
    svc = QuoteService(client, ttl=timedelta(minutes=5))
    await svc.quote(VWCE)
    await svc.quote(VWCE)
    assert route.call_count == 1
    await svc.quote(VWCE, force=True)
    assert route.call_count == 2


async def test_health_cooldown():
    h = ProviderHealth("x")
    for _ in range(3):
        h.record(False, "boom")
    assert not h.available()
    assert h.available(datetime.now(UTC) + timedelta(seconds=61))
    h.record(True)
    assert h.available() and h.consecutive_failures == 0


@respx.mock
async def test_fx_chain_yahoo_then_frankfurter(client):
    respx.get(CHART_URL.format(symbol="USDEUR=X")).mock(return_value=httpx.Response(500))
    respx.get(FRANKFURTER_URL).mock(
        return_value=httpx.Response(
            200, json={"base": "USD", "date": "2026-09-09", "rates": {"EUR": 0.85822}}
        )
    )
    svc = QuoteService(client, ttl=timedelta(0))
    r = await svc.fx("USD", "EUR")
    assert r is not None and r.rate == D("0.85822") and r.source == "frankfurter"
    assert await svc.fx("EUR", "EUR") is None


@respx.mock
async def test_value_demo_portfolio_end_to_end(client):
    prices = {
        "VWCE.MI": 166.0,
        "CSSPX.MI": 600.0,
        "AGGH.MI": 5.0,
        "AAPL": 200.0,
        "MSFT": 500.0,
        "ENI.MI": 24.0,
        "SGLD.MI": 300.0,
        "IWDP.MI": 20.0,
    }
    for sym, px in prices.items():
        cur = "USD" if sym in ("AAPL", "MSFT") else "EUR"
        respx.get(CHART_URL.format(symbol=sym)).mock(
            return_value=httpx.Response(200, json=chart(px, cur))
        )
    respx.get(CHART_URL.format(symbol="USDEUR=X")).mock(
        return_value=httpx.Response(200, json=chart(0.5, "EUR"))
    )
    svc = QuoteService(client)
    v = await svc.value(demo_portfolio())
    assert not v.missing and not v.stale
    by_id = {vp.instrument.id: vp for vp in v.positions}
    assert by_id["US0378331005"].market_value == D("15") * D("200") * D("0.5")
    assert v.cash_value == 0 and not v.cash_tracked  # no deposits: cash is not tracked
    assert sum((s.weight for s in v.breakdown("asset_class")), D(0)).quantize(D("0.001")) == 1


@respx.mock
async def test_doctor_reports_per_provider(client):
    respx.get(CHART_URL.format(symbol="VWCE.MI")).mock(
        return_value=httpx.Response(200, json=chart(166.0))
    )
    respx.get(f"https://www.justetf.com/api/etfs/{VWCE.isin}/quote").mock(
        return_value=httpx.Response(500)
    )
    rows = await QuoteService(client).doctor([VWCE])
    assert {(r.provider, r.ok) for r in rows} == {("yahoo", True), ("justetf", False)}


# --- resolver ---------------------------------------------------------------------------


def test_isin_and_asset_class_helpers():
    assert is_isin("ie00bk5bqt80") and not is_isin("VWCE")
    assert guess_asset_class("iShares Core Global Aggregate Bond").value == "bond"
    assert guess_asset_class("Vanguard FTSE All-World").value == "equity"
    assert guess_asset_class("NVIDIA Corporation", AssetType.STOCK).value == "equity"
    assert guess_asset_class("iShares $ Corporate Bond").value == "bond"


def figi_row(ticker: str, exch: str, sec: str = "ETP") -> dict:
    return {"ticker": ticker, "exchCode": exch, "securityType": sec, "name": "VANG FTSE AW USDA"}


@respx.mock
async def test_resolve_by_isin_builds_instrument_with_preferred_eur_listing(client):
    respx.post(FIGI_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "data": [
                        figi_row("VWRA", "LN"),
                        figi_row("VWCE", "GR"),
                        figi_row("VWCE", "IM"),
                        figi_row("VWCE", "XX"),
                    ]
                }
            ],
        )
    )
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "quotes": [
                    {
                        "symbol": "VWRA.L",
                        "exchange": "LSE",
                        "quoteType": "ETF",
                        "longname": "Vanguard FTSE All-World UCITS ETF USD Accumulation",
                    },
                    {"symbol": "IE00BK5BQT80.SG", "exchange": "STU", "quoteType": "MUTUALFUND"},
                ]
            },
        )
    )
    respx.get(CHART_URL.format(symbol="VWRA.L")).mock(
        return_value=httpx.Response(200, json=chart(193.3, "USD", exchangeName="LSE"))
    )
    respx.get(CHART_URL.format(symbol="VWCE.DE")).mock(
        return_value=httpx.Response(200, json=chart(166.2, "EUR", exchangeName="GER"))
    )
    respx.get(CHART_URL.format(symbol="VWCE.MI")).mock(
        return_value=httpx.Response(200, json=chart(166.2, "EUR", exchangeName="MIL"))
    )
    res = await InstrumentResolver(client).resolve(isin="ie00bk5bqt80")
    u = res.unique
    assert u is not None and u.isin == "IE00BK5BQT80" and u.asset_type == AssetType.ETF
    assert u.preferred_symbol == "VWCE.MI" and u.currency == "EUR"
    assert [ls.symbol for ls in u.listings] == ["VWCE.MI", "VWCE.DE", "VWRA.L"]
    ins = u.to_instrument()
    assert ins.id == "IE00BK5BQT80" and ins.name.startswith("Vanguard")


@respx.mock
async def test_resolve_by_name_is_ambiguous_when_two_instruments_match(client):
    def q(symbol, exchange, name):
        return {"symbol": symbol, "exchange": exchange, "quoteType": "ETF", "longname": name}

    acc = "Vanguard FTSE All-World UCITS ETF USD Accumulation"
    dist = "Vanguard FTSE All-World UCITS ETF"
    exus = "Vanguard FTSE All-World ex-US"
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "quotes": [
                    q("VWCE.DE", "GER", acc),
                    q("VWRL.SW", "EBS", dist),
                    q("VEU", "PCX", exus),
                    q("VEUX", "PNK", exus),  # OTC listing: dropped
                ]
            },
        )
    )
    # OpenFIGI's fuzzy search adds a listing Yahoo search did not return
    respx.post(FIGI_SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json={"data": [figi_row("VWRL", "NA"), figi_row("VWRL", "XD")]}
        )
    )

    def meta(sym, price, cur, exch, name):
        respx.get(CHART_URL.format(symbol=sym)).mock(
            return_value=httpx.Response(
                200, json=chart(price, cur, exchangeName=exch, longName=name)
            )
        )

    meta("VWCE.DE", 166, "EUR", "GER", acc)
    meta("VWRL.SW", 120, "CHF", "EBS", dist)
    meta("VWRL.AS", 120, "EUR", "AMS", dist)
    meta("VEU", 60, "USD", "PCX", exus)
    meta("VWRL.XD", 120, "EUR", "DXE", "Vanguard FTSE All-World UCITS E")  # truncated name
    veux = respx.get(CHART_URL.format(symbol="VEUX")).mock(return_value=httpx.Response(404))

    res = await InstrumentResolver(client).resolve(query="Vanguard All World")
    assert res.unique is None and len(res.matches) == 3
    assert any("ask which one" in n for n in res.notes)
    vwrl = next(m for m in res.matches if m.name == dist)
    assert [ls.symbol for ls in vwrl.listings] == ["VWRL.AS", "VWRL.XD", "VWRL.SW"]  # EUR first
    assert vwrl.preferred_symbol == "VWRL.AS" and vwrl.currency == "EUR"
    assert not veux.called


@respx.mock
async def test_resolve_by_name_single_match_has_no_isin(client):
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "quotes": [
                    {
                        "symbol": "AAPL",
                        "exchange": "NMS",
                        "quoteType": "EQUITY",
                        "longname": "Apple Inc.",
                    },
                ]
            },
        )
    )
    respx.get(CHART_URL.format(symbol="AAPL")).mock(
        return_value=httpx.Response(
            200,
            json=chart(
                200, "USD", exchangeName="NMS", instrumentType="EQUITY", longName="Apple Inc."
            ),
        )
    )
    res = await InstrumentResolver(client).resolve(query="Apple")
    u = res.unique
    assert u is not None and u.isin is None and u.asset_type == AssetType.STOCK
    assert u.to_instrument().id == "AAPL"


@respx.mock
async def test_resolve_exact_symbol_query_is_unique(client):
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "quotes": [
                    {
                        "symbol": "AMZN",
                        "exchange": "NMS",
                        "quoteType": "EQUITY",
                        "longname": "Amazon.com, Inc.",
                    },
                    {
                        "symbol": "AMZ.DE",
                        "exchange": "GER",
                        "quoteType": "EQUITY",
                        "longname": "Amazon.com, Inc.",
                    },
                    {
                        "symbol": "AMZN.NE",
                        "exchange": "NEO",
                        "quoteType": "EQUITY",
                        "longname": "Amazon CDR",
                    },
                ]
            },
        )
    )
    respx.post(FIGI_SEARCH_URL).mock(return_value=httpx.Response(200, json={"data": []}))
    respx.get(CHART_URL.format(symbol="AMZN")).mock(
        return_value=httpx.Response(
            200,
            json=chart(
                220, "USD", exchangeName="NMS", instrumentType="EQUITY", longName="Amazon.com, Inc."
            ),
        )
    )
    respx.get(CHART_URL.format(symbol="AMZ.DE")).mock(
        return_value=httpx.Response(
            200,
            json=chart(
                190, "EUR", exchangeName="GER", instrumentType="EQUITY", longName="Amazon.com, Inc."
            ),
        )
    )
    respx.get(CHART_URL.format(symbol="AMZN.NE")).mock(
        return_value=httpx.Response(
            200,
            json=chart(
                30, "CAD", exchangeName="NEO", instrumentType="EQUITY", longName="Amazon CDR"
            ),
        )
    )
    res = await InstrumentResolver(client).resolve(query="amzn")
    u = res.unique
    assert u is not None and u.name == "Amazon.com, Inc." and len(res.matches) == 2
    assert u.preferred_symbol == "AMZN"  # US company: quoted from its home exchange


def test_instrument_listing_helper():
    ins = Instrument(
        id="X",
        name="x",
        asset_type=AssetType.STOCK,
        currency="EUR",
        listings=[
            Listing(symbol="A", exchange="E", currency="EUR"),
            Listing(symbol="B", exchange="E", currency="EUR"),
        ],
        preferred_symbol="B",
    )
    assert ins.symbols_in_order() == ["B", "A"]


@respx.mock
async def test_us_company_prefers_home_exchange_by_name(client):
    def q(symbol, exchange, name):
        return {"symbol": symbol, "exchange": exchange, "quoteType": "EQUITY", "longname": name}

    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "quotes": [
                    q("SNPS", "NMS", "Synopsys, Inc."),
                    q("1SNPS.MI", "MIL", "Synopsys, Inc."),
                    q("ENI.MI", "MIL", "Eni S.p.A."),
                    q("E", "NYQ", "Eni S.p.A."),
                ]
            },
        )
    )
    respx.post(FIGI_SEARCH_URL).mock(return_value=httpx.Response(200, json={"data": []}))
    respx.get(CHART_URL.format(symbol="SNPS")).mock(
        return_value=httpx.Response(
            200,
            json=chart(
                397, "USD", exchangeName="NMS", instrumentType="EQUITY", longName="Synopsys, Inc."
            ),
        )
    )
    respx.get(CHART_URL.format(symbol="1SNPS.MI")).mock(
        return_value=httpx.Response(
            200,
            json=chart(
                338, "EUR", exchangeName="MIL", instrumentType="EQUITY", longName="Synopsys, Inc."
            ),
        )
    )
    respx.get(CHART_URL.format(symbol="ENI.MI")).mock(
        return_value=httpx.Response(
            200,
            json=chart(
                24, "EUR", exchangeName="MIL", instrumentType="EQUITY", longName="Eni S.p.A."
            ),
        )
    )
    respx.get(CHART_URL.format(symbol="E")).mock(
        return_value=httpx.Response(
            200,
            json=chart(
                30, "USD", exchangeName="NYQ", instrumentType="EQUITY", longName="Eni S.p.A."
            ),
        )
    )
    res = await InstrumentResolver(client).resolve(query="anything")
    by_name = {m.name: m for m in res.matches}
    assert by_name["Synopsys, Inc."].preferred_symbol == "SNPS"  # US company: home exchange
    assert (
        by_name["Eni S.p.A."].preferred_symbol == "ENI.MI"
    )  # European company: EUR listing, not the ADR


@respx.mock
async def test_isin_path_us_stock_prefers_us_exchange(client):
    respx.post(FIGI_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "data": [
                        figi_row("AAPL", "US", "Common Stock"),
                        figi_row("APC", "GR", "Common Stock"),
                    ]
                }
            ],
        )
    )
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"quotes": []}))
    respx.get(CHART_URL.format(symbol="AAPL")).mock(
        return_value=httpx.Response(
            200,
            json=chart(
                315, "USD", exchangeName="NMS", instrumentType="EQUITY", longName="Apple Inc."
            ),
        )
    )
    respx.get(CHART_URL.format(symbol="APC.DE")).mock(
        return_value=httpx.Response(
            200,
            json=chart(
                270, "EUR", exchangeName="GER", instrumentType="EQUITY", longName="Apple Inc."
            ),
        )
    )
    u = (await InstrumentResolver(client).resolve(isin="US0378331005")).unique
    assert u is not None and u.preferred_symbol == "AAPL" and u.currency == "USD"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Xtrackers II EUR Overnight Rate Swap UCITS ETF 1C", "money_market"),
        ("Xtrackers Portfolio UCITS ETF 1C", "multi_asset"),
        ("Vanguard LifeStrategy 60% Equity UCITS ETF", "multi_asset"),
        ("GRUNDBESITZ EUROPA-RC", "real_estate"),
        ("iShares $ Treasury Bond 0-1yr UCITS ETF USD (Acc)", "bond"),
        ("iShares € Aggregate Bond ESG SRI UCITS ETF EUR (Dist)", "bond"),
        ("Xtrackers II EUR Corporate Bond UCITS ETF 1C", "bond"),
        ("Xetra-Gold", "commodity"),
        ("iShares Core MSCI World UCITS ETF USD (Acc)", "equity"),
        ("Vanguard FTSE All-World UCITS ETF", "equity"),
        ("iShares MSCI Europe Quality Dividend Advanced UCITS ETF EUR (Dist)", "equity"),
        (
            "Xtrackers (IE) Public Limited Company - Xtrackers MSCI Emerging Markets UCITS ETF",
            "equity",
        ),
        ("iShares Global Real Estate UCITS ETF", "real_estate"),
        ("Amundi Prime Global UCITS ETF", "other"),  # no evidence: not silently equity
    ],
)
def test_asset_class_guess(name, expected):
    assert guess_asset_class(name).value == expected


def test_stocks_are_always_equity():
    assert guess_asset_class("Grundbesitz Holding AG", AssetType.STOCK).value == "equity"
