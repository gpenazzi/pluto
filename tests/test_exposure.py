# ruff: noqa: E501
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from pluto.core.demo import DEMO_INSTRUMENTS
from pluto.core.model import AssetClass, AssetType, Instrument, Listing
from pluto.market.exposure import (
    Exposure,
    ExposureCache,
    ExposureResult,
    ExposureService,
    look_through,
    region_of,
)

VWCE, AGGH, AAPL, ENI = (
    DEMO_INSTRUMENTS[0],
    DEMO_INSTRUMENTS[2],
    DEMO_INSTRUMENTS[3],
    DEMO_INSTRUMENTS[5],
)
JUSTETF = "https://www.justetf.com/en/etf-profile.html"
CRUMB = "https://query2.finance.yahoo.com/v1/test/getcrumb"


def justetf_page(countries: dict[str, str] | None, sectors: dict[str, str] | None) -> str:
    def table(kind: str, rows: dict[str, str] | None) -> str:
        if rows is None:
            return ""
        body = "".join(
            f'<tr data-testid="etf-holdings_{kind}_row"><td data-testid="x">{k}</td><td><div><span>{v}</span></div></td></tr>'
            for k, v in rows.items()
        )
        return f'<table class="table" data-testid="etf-holdings_{kind}_table"><tbody>{body}</tbody></table>'

    return f"<html><body>etf-profile {table('countries', countries)} {table('sectors', sectors)}</body></html>"


def summary(symbol: str, result: dict) -> None:
    respx.get(f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}").mock(
        return_value=httpx.Response(200, json={"quoteSummary": {"result": [result], "error": None}})
    )


@pytest.fixture
async def svc(tmp_path: Path):
    async with httpx.AsyncClient() as client:
        yield ExposureService(client, ExposureCache(tmp_path / "exp.json"))


def test_region_map():
    assert region_of("United States") == "North America" and region_of("Japan") == "Japan"
    assert region_of("Taiwan") == "Emerging markets" and region_of("Atlantis") == "Other countries"


@respx.mock
async def test_justetf_countries_and_sectors_are_cached(svc: ExposureService, tmp_path: Path):
    route = respx.get(JUSTETF).mock(
        return_value=httpx.Response(
            200,
            text=justetf_page(
                {"United States": "69.07%", "Japan": "5,67%"}, {"Technology": "34.88%"}
            ),
        )
    )
    res = await svc.get(VWCE)
    assert res.exposure and res.exposure.countries == {"United States": 69.07, "Japan": 5.67}
    assert res.exposure.sectors == {"Technology": 34.88} and res.exposure.source == "justetf"
    assert route.call_count == 1
    res2 = await svc.get(VWCE)
    assert res2.from_cache and route.call_count == 1
    again = ExposureService(svc.client, ExposureCache(tmp_path / "exp.json"))
    assert (await again.get(VWCE)).from_cache


@respx.mock
async def test_yahoo_sectors_fill_in_when_justetf_has_nothing(svc: ExposureService):
    respx.get(JUSTETF).mock(return_value=httpx.Response(200, text=justetf_page(None, None)))
    respx.get("https://fc.yahoo.com").mock(return_value=httpx.Response(404))
    respx.get(CRUMB).mock(return_value=httpx.Response(200, text="abc123"))
    summary(
        "AGGH.MI",
        {
            "topHoldings": {
                "sectorWeightings": [
                    {"technology": {"raw": 0.3}},
                    {"financial_services": {"raw": 0.7}},
                ]
            }
        },
    )
    res = await svc.get(AGGH)
    assert res.exposure and res.exposure.sectors == {"Technology": 30.0, "Finance": 70.0}
    assert res.exposure.countries == {} and res.exposure.source == "justetf+yahoo"


@respx.mock
async def test_stock_uses_yahoo_profile_then_isin_fallback(svc: ExposureService):
    respx.get("https://fc.yahoo.com").mock(return_value=httpx.Response(404))
    respx.get(CRUMB).mock(return_value=httpx.Response(200, text="abc123"))
    summary("AAPL", {"assetProfile": {"country": "United States", "sector": "Technology"}})
    res = await svc.get(AAPL)
    assert (
        res.exposure
        and res.exposure.countries == {"United States": 100.0}
        and res.exposure.sectors == {"Technology": 100.0}
    )
    # Yahoo down: country from the ISIN, sector unknown, and the reason is reported
    respx.get("https://query2.finance.yahoo.com/v10/finance/quoteSummary/ENI.MI").mock(
        return_value=httpx.Response(500)
    )
    res = await svc.get(ENI)
    assert (
        res.exposure and res.exposure.countries == {"Italy": 100.0} and res.exposure.sectors == {}
    )
    assert res.exposure.source == "isin" and "HTTP 500" in (res.exposure.note or "")


@respx.mock
async def test_all_sources_down_gives_a_reason_and_stale_cache_when_present(svc: ExposureService):
    respx.get(JUSTETF).mock(side_effect=httpx.ConnectError("boom"))
    respx.get("https://fc.yahoo.com").mock(side_effect=httpx.ConnectError("boom"))
    res = await svc.get(VWCE)
    assert (
        res.exposure is None
        and "justETF unreachable" in (res.error or "")
        and "Yahoo unreachable" in (res.error or "")
    )
    stale = Exposure(
        countries={"United States": 60.0}, source="justetf", as_of=date.today() - timedelta(days=90)
    )
    svc.cache.put(VWCE.id, stale)
    res = await svc.get(VWCE)
    assert res.exposure is stale and res.from_cache and res.error


def test_look_through_buckets_and_unknowns():
    xeon = Instrument(
        id="XEON",
        name="Overnight",
        asset_type=AssetType.ETF,
        asset_class=AssetClass.MONEY_MARKET,
        currency="EUR",
        listings=[Listing(symbol="XEON.MI", exchange="MIL", currency="EUR")],
    )
    mystery = Instrument(
        id="M",
        name="Mystery ETF",
        asset_type=AssetType.ETF,
        currency="EUR",
        listings=[Listing(symbol="M.MI", exchange="MIL", currency="EUR")],
    )
    instruments = {VWCE.id: VWCE, AAPL.id: AAPL, "XEON": xeon, "M": mystery}
    weights = {VWCE.id: 0.5, AAPL.id: 0.2, "XEON": 0.2, "M": 0.1}
    exposures = {
        VWCE.id: ExposureResult(
            Exposure(
                countries={"United States": 60.0, "Japan": 40.0},
                sectors={"Technology": 100.0},
                source="justetf",
                as_of=date.today(),
            )
        ),
        AAPL.id: ExposureResult(
            Exposure(
                countries={"United States": 100.0},
                sectors={"Technology": 100.0},
                source="yahoo",
                as_of=date.today(),
            )
        ),
        "XEON": ExposureResult(Exposure(source="justetf", as_of=date.today(), note="no breakdown")),
        "M": ExposureResult(None, error="justETF unreachable: ConnectError"),
    }
    lt = look_through(weights, instruments, exposures)
    assert lt.by_region == {
        "North America": 50.0,
        "Japan": 20.0,
        "Money market": 20.0,
        "Unknown": 10.0,
    }
    assert lt.by_country["United States"] == 50.0 and lt.by_sector["Technology"] == 70.0
    assert lt.covered_pct == 90.0
    assert lt.unknown == [
        {
            "instrument_id": "M",
            "name": "Mystery ETF",
            "weight_pct": 10.0,
            "reason": "justETF unreachable: ConnectError",
        }
    ]
