from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from pluto.core.demo import DEMO_INSTRUMENTS
from pluto.market.history import HistoryService, PriceSeries
from pluto.market.providers.yahoo import CHART_URL

VWCE = DEMO_INSTRUMENTS[0]


def chart_history(start: date, closes: list[float | None], currency: str = "EUR") -> dict:
    import calendar

    ts = [
        calendar.timegm((start + timedelta(days=i)).timetuple()) + 8 * 3600
        for i in range(len(closes))
    ]
    return {
        "chart": {
            "result": [
                {
                    "meta": {"currency": currency},
                    "timestamp": ts,
                    "indicators": {
                        "quote": [{"close": closes}],
                        "adjclose": [{"adjclose": [c if c is None else c * 0.9 for c in closes]}],
                    },
                }
            ],
            "error": None,
        }
    }


@pytest.fixture
async def client():
    async with httpx.AsyncClient() as c:
        yield c


def test_price_series_forward_fill_and_json_roundtrip():
    ps = PriceSeries(symbol="X", currency="EUR")
    ps.merge([(date(2026, 1, 5), 10.0, 9.0), (date(2026, 1, 7), 12.0, 11.0)])
    assert ps.at(date(2026, 1, 4)) is None
    assert ps.at(date(2026, 1, 6)) == 10.0 and ps.at(date(2026, 1, 6), adjusted=True) == 9.0
    assert ps.at(date(2026, 1, 9)) == 12.0
    again = PriceSeries.from_json(ps.to_json())
    assert again.dates == ps.dates and again.adjclose == ps.adjclose
    ps.merge([(date(2026, 1, 7), 13.0, 12.0)])  # merge overwrites same-day bars
    assert ps.close == [10.0, 13.0]


@respx.mock
async def test_history_service_fetches_caches_and_refreshes(client, tmp_path: Path, monkeypatch):
    today = date.today()
    start = today - timedelta(days=10)
    route = respx.get(CHART_URL.format(symbol="VWCE.MI")).mock(
        return_value=httpx.Response(200, json=chart_history(start, [100, None, 102, 103]))
    )
    svc = HistoryService(client, tmp_path / "hist")
    ps = await svc.series(VWCE, start)
    assert ps is not None and ps.symbol == "VWCE.MI" and ps.close == [100, 102, 103]
    assert route.call_count == 1 and (tmp_path / "hist" / "VWCE.MI.json").exists()

    # a new service instance reads the cache; last bar is stale so it refetches incrementally
    route.mock(
        return_value=httpx.Response(
            200, json=chart_history(start + timedelta(days=3), [103.5, 104])
        )
    )
    svc2 = HistoryService(client, tmp_path / "hist")
    ps2 = await svc2.series(VWCE, start)
    assert ps2 is not None and ps2.close == [100, 102, 103.5, 104]
    assert route.call_count == 2
    req = route.calls.last.request
    assert int(req.url.params["period1"]) < int(req.url.params["period2"])


@respx.mock
async def test_history_service_falls_back_to_next_listing_and_cache_on_error(
    client, tmp_path: Path
):
    start = date.today() - timedelta(days=5)
    respx.get(CHART_URL.format(symbol="VWCE.MI")).mock(return_value=httpx.Response(404))
    respx.get(CHART_URL.format(symbol="VWCE.DE")).mock(
        return_value=httpx.Response(200, json=chart_history(start, [1, 2, 3, 4, 5, 6]))
    )
    svc = HistoryService(client, tmp_path / "hist")
    ps = await svc.series(VWCE, start)
    assert ps is not None and ps.symbol == "VWCE.DE"
    # provider outage: serve the cached series (other listings are tried and fail too)
    respx.get(CHART_URL.format(symbol="VWCE.DE")).mock(return_value=httpx.Response(500))
    respx.get(CHART_URL.format(symbol="VWRA.L")).mock(return_value=httpx.Response(404))
    (tmp_path / "hist" / "VWCE.DE.json").write_text(ps.to_json())
    svc2 = HistoryService(client, tmp_path / "hist")
    ps2 = await svc2.series(
        VWCE, start - timedelta(days=30)
    )  # cache starts too late -> fetch -> 500 -> cache
    assert ps2 is not None and ps2.close == ps.close


@respx.mock
async def test_for_portfolio_collects_fx(client, tmp_path: Path):
    start = date.today() - timedelta(days=5)
    for sym in ("VWCE.MI", "AAPL", "USDEUR=X"):
        cur = "USD" if sym == "AAPL" else "EUR"
        respx.get(CHART_URL.format(symbol=sym)).mock(
            return_value=httpx.Response(200, json=chart_history(start, [1, 1, 1, 1, 1, 1], cur))
        )
    svc = HistoryService(client, tmp_path / "hist")
    prices, fx, missing = await svc.for_portfolio([VWCE, DEMO_INSTRUMENTS[3]], "EUR", start)
    assert set(prices) == {"IE00BK5BQT80", "US0378331005"} and "USD/EUR" in fx and missing == []


@respx.mock
async def test_series_prefers_listing_with_longer_history(client, tmp_path: Path):
    start = date.today() - timedelta(days=30)
    respx.get(CHART_URL.format(symbol="VWCE.MI")).mock(
        return_value=httpx.Response(200, json=chart_history(date.today(), [166.0]))  # one bar
    )
    respx.get(CHART_URL.format(symbol="VWCE.DE")).mock(
        return_value=httpx.Response(200, json=chart_history(start, [100.0] * 31))
    )
    svc = HistoryService(client, tmp_path / "hist")
    ps = await svc.series(VWCE, start)
    assert ps is not None and ps.symbol == "VWCE.DE"
