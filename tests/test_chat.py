from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

import httpx
import pytest
import respx

from pluto.chat.prompt import system_prompt
from pluto.chat.providers.scripted import ScriptedProvider
from pluto.chat.session import Transcript
from pluto.chat.tools import ChatContext, ToolRegistry
from pluto.core.demo import demo_portfolio
from pluto.core.model import AssetClass
from pluto.market.providers.openfigi import SEARCH_URL as FIGI_SEARCH_URL
from pluto.market.providers.openfigi import URL as FIGI_URL
from pluto.market.providers.yahoo import CHART_URL, SEARCH_URL
from pluto.market.resolver import InstrumentResolver
from pluto.market.service import QuoteService
from pluto.store.versions import VersionStore
from tests.test_market import chart


@pytest.fixture
async def ctx(tmp_path: Path):
    store = VersionStore(tmp_path / "p")
    store.commit(demo_portfolio(), "Demo")
    async with httpx.AsyncClient() as client:
        svc = QuoteService(client, ttl=timedelta(0))
        yield ChatContext(store=store, quotes=svc, resolver=InstrumentResolver(client))


@pytest.fixture
def registry(ctx: ChatContext) -> ToolRegistry:
    return ToolRegistry(ctx)


async def test_get_portfolio_and_schema(registry: ToolRegistry):
    res = await registry.call("get_portfolio", {})
    assert res.ok and res.data["version"] == 1 and len(res.data["positions"]) == 6
    assert res.data["cash"]["EUR"] == D("4953.90")
    schema = registry.specs["add_transaction"].json_schema()
    assert schema["properties"]["type"]["$ref"].endswith("TxType") or "enum" in str(schema)
    assert "title" not in schema


async def test_unknown_tool_and_bad_args(registry: ToolRegistry):
    assert not (await registry.call("nope", {})).ok
    res = await registry.call("add_transaction", {"type": "buy"})
    assert not res.ok and "needs an instrument" in res.data["error"]
    res = await registry.call("add_transaction", {"type": "purchase"})
    assert not res.ok and res.data["error"] == "invalid arguments"


async def test_add_transaction_for_known_symbol_creates_version(
    registry: ToolRegistry, ctx: ChatContext
):
    res = await registry.call(
        "add_transaction",
        {
            "type": "buy",
            "instrument": "vwce.mi",
            "quantity": "10",
            "price": "166.2",
            "fees": "2.95",
            "date": "2026-09-09",
        },
    )
    assert res.ok, res.data
    assert res.data["version"] == 2 and res.data["position_after"]["quantity"] == D("190")
    assert res.data["transaction"]["source"] == "chat"
    assert ctx.store.head() == 2
    txt = res.as_text()
    assert '"quantity": "190"' in txt  # Decimals serialise as strings


async def test_sell_more_than_held_is_reported_not_raised(registry: ToolRegistry):
    res = await registry.call(
        "add_transaction", {"type": "sell", "instrument": "AAPL", "quantity": "999", "price": "1"}
    )
    assert not res.ok and "cannot sell" in res.data["error"]


async def test_deposit_defaults_to_base_currency(registry: ToolRegistry):
    res = await registry.call("add_transaction", {"type": "deposit", "amount": "1000"})
    assert res.ok and res.data["transaction"]["currency"] == "EUR"


async def test_undo_and_revert(registry: ToolRegistry, ctx: ChatContext):
    await registry.call("add_transaction", {"type": "deposit", "amount": "1000"})
    await registry.call("add_transaction", {"type": "deposit", "amount": "2000"})
    assert ctx.store.head() == 3
    res = await registry.call("undo", {})
    assert res.ok and res.data["version"] == 4
    assert len(ctx.store.load().transactions) == len(demo_portfolio().transactions) + 1
    res = await registry.call("revert_to_version", {"version": 1})
    assert res.ok and len(ctx.store.load().transactions) == len(demo_portfolio().transactions)
    versions = (await registry.call("list_versions", {})).data
    assert versions["head"] == 5 and versions["versions"][-1]["reverted_from"] == 1


@respx.mock
async def test_add_transaction_with_new_isin_resolves_and_adds_instrument(
    registry: ToolRegistry, ctx: ChatContext
):
    isin = "IE00B4L5Y983"
    respx.post(FIGI_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "data": [
                        {
                            "ticker": "SWDA",
                            "exchCode": "IM",
                            "securityType": "ETP",
                            "name": "ISHARES CORE MSCI WORLD",
                        },
                    ]
                }
            ],
        )
    )
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"quotes": []}))
    respx.get(CHART_URL.format(symbol="SWDA.MI")).mock(
        return_value=httpx.Response(
            200,
            json=chart(
                110.5,
                "EUR",
                exchangeName="MIL",
                longName="iShares Core MSCI World UCITS ETF USD (Acc)",
            ),
        )
    )
    res = await registry.call(
        "add_transaction", {"type": "buy", "instrument": isin, "quantity": "5", "price": "110"}
    )
    assert res.ok, res.data
    p = ctx.store.load()
    assert p.instruments[isin].name.startswith("iShares Core MSCI World")
    assert p.instruments[isin].preferred_symbol == "SWDA.MI"


@respx.mock
async def test_resolve_and_add_instrument_by_symbol(registry: ToolRegistry, ctx: ChatContext):
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "quotes": [
                    {
                        "symbol": "NVDA",
                        "exchange": "NMS",
                        "quoteType": "EQUITY",
                        "longname": "NVIDIA Corporation",
                    },
                    {
                        "symbol": "NVD.DE",
                        "exchange": "GER",
                        "quoteType": "EQUITY",
                        "longname": "NVIDIA Corporation",
                    },
                ]
            },
        )
    )
    respx.post(FIGI_SEARCH_URL).mock(return_value=httpx.Response(200, json={"data": []}))
    respx.get(CHART_URL.format(symbol="NVDA")).mock(
        return_value=httpx.Response(
            200,
            json=chart(
                180,
                "USD",
                exchangeName="NMS",
                instrumentType="EQUITY",
                longName="NVIDIA Corporation",
            ),
        )
    )
    respx.get(CHART_URL.format(symbol="NVD.DE")).mock(
        return_value=httpx.Response(
            200,
            json=chart(
                155,
                "EUR",
                exchangeName="GER",
                instrumentType="EQUITY",
                longName="NVIDIA Corporation",
            ),
        )
    )
    res = await registry.call("resolve_instrument", {"query": "Nvidia"})
    assert res.ok and res.data["unique"]["preferred_symbol"] == "NVDA"  # US company: NASDAQ
    # an explicit symbol is enough for add_transaction: instrument added on the fly
    res = await registry.call(
        "add_transaction", {"type": "buy", "instrument": "NVDA", "quantity": "10", "price": "180"}
    )
    assert res.ok, res.data
    assert res.data["transaction"]["currency"] == "USD" and res.data["version"] == 2
    res = await registry.call("add_instrument", {"symbol": "NVDA"})
    assert res.ok and not res.data["added"] and res.data["instrument"]["id"] == "NVDA"
    assert ctx.store.load().instruments["NVDA"].currency == "USD"
    res = await registry.call(
        "add_transaction", {"type": "buy", "instrument": "XYZNOPE", "quantity": "1", "price": "1"}
    )
    assert not res.ok and "could not be resolved" in res.data["error"]


@respx.mock
async def test_get_valuation_and_get_quote(registry: ToolRegistry):
    prices = {
        "VWCE.MI": 166.0,
        "CSSPX.MI": 600.0,
        "AGGH.MI": 5.0,
        "AAPL": 200.0,
        "MSFT": 500.0,
        "ENI.MI": 24.0,
    }
    for sym, px in prices.items():
        cur = "USD" if sym in ("AAPL", "MSFT") else "EUR"
        respx.get(CHART_URL.format(symbol=sym)).mock(
            return_value=httpx.Response(200, json=chart(px, cur))
        )
    respx.get(CHART_URL.format(symbol="USDEUR=X")).mock(
        return_value=httpx.Response(200, json=chart(0.5, "EUR"))
    )
    res = await registry.call("get_valuation", {})
    assert res.ok and not res.data["missing_prices"]
    assert res.data["breakdown"]["asset_class"][0]["label"] == "equity"
    res = await registry.call("get_quote", {"instrument": "AAPL"})
    assert res.ok and res.data["price"] == D("200") and res.data["source"] == "yahoo"


async def test_scripted_provider_end_to_end(
    registry: ToolRegistry, ctx: ChatContext, tmp_path: Path
):
    prov = ScriptedProvider(
        registry,
        [
            [
                ("tool", "get_portfolio", {}),
                (
                    "tool",
                    "add_transaction",
                    {"type": "buy", "instrument": "ENI.MI", "quantity": "100", "price": "24"},
                ),
                ("text", "Recorded: bought 100 Eni at 24 EUR (version 2)."),
            ]
        ],
    )
    transcript = Transcript("t", tmp_path / "chat.jsonl")
    transcript.user("bought 100 eni at 24")
    events = []
    async for ev in prov.send("bought 100 eni at 24"):
        transcript.event(ev)
        events.append(ev)
    assert [e.type for e in events] == [
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "text",
        "done",
    ]
    assert all(e.ok for e in events if e.type == "tool_result")
    assert ctx.store.head() == 2
    lines = (tmp_path / "chat.jsonl").read_text().splitlines()
    assert len(lines) == 7 and '"role": "user"' in lines[0]


def test_system_prompt_mentions_rules():
    sp = system_prompt(demo_portfolio(), 3)
    assert "version 3" in sp and "Never guess between candidates" in sp


@respx.mock
async def test_set_instrument_listing_switches_quote_source(
    registry: ToolRegistry, ctx: ChatContext
):
    respx.get(CHART_URL.format(symbol="VWRA.L")).mock(
        return_value=httpx.Response(200, json=chart(193.3, "USD", exchangeName="LSE"))
    )
    res = await registry.call(
        "set_instrument_listing", {"instrument": "IE00BK5BQT80", "symbol": "vwra.l"}
    )
    assert res.ok, res.data
    assert (
        res.data["instrument"]["symbols"][0] == "VWRA.L" and res.data["quote"]["currency"] == "USD"
    )
    assert "converted" in res.data["note"]
    ins = ctx.store.load().instruments["IE00BK5BQT80"]
    assert ins.preferred_symbol == "VWRA.L" and ins.currency == "EUR"  # transactions untouched
    respx.get(CHART_URL.format(symbol="NOPE.XX")).mock(return_value=httpx.Response(404))
    res = await registry.call("set_instrument_listing", {"instrument": "AAPL", "symbol": "NOPE.XX"})
    assert not res.ok and "no price" in res.data["error"]


async def test_set_asset_class_and_reclassify(registry: ToolRegistry, ctx: ChatContext):
    res = await registry.call(
        "set_asset_class", {"instrument": "AGGH.MI", "asset_class": "money_market"}
    )
    assert res.ok and res.data["instrument"]["asset_class"] == "money_market"
    assert res.data["instrument"]["asset_class_confirmed"] is True
    res = await registry.call("set_asset_class", {"instrument": "AAPL", "asset_class": "shares"})
    assert not res.ok
    # a user-set class survives reclassification; a wrong guess gets fixed
    p = ctx.store.load()
    p.instruments["IE00BDBRDM35"].asset_class_confirmed = True
    p.instruments["IE00BK5BQT80"].asset_class = AssetClass.BOND
    ctx.store.commit(p, "tamper")
    res = await registry.call("reclassify", {})
    assert res.ok and res.data["changed"] == [
        {"instrument": "Vanguard FTSE All-World UCITS ETF (Acc)", "from": "bond", "to": "equity"}
    ]
    assert (
        res.data["classes"]["iShares Core Global Aggregate Bond UCITS ETF EUR Hedged (Acc)"]
        == "money_market (user)"
    )
    res = await registry.call("reclassify", {})
    assert res.ok and res.data["changed"] == [] and res.data["version"] is None


@respx.mock
async def test_get_performance_tool(registry: ToolRegistry, ctx: ChatContext, tmp_path: Path):
    from datetime import date, timedelta

    from pluto.market.history import HistoryService
    from tests.test_history import chart_history

    ctx.history = HistoryService(ctx.quotes.client, tmp_path / "hist")
    start = date.today() - timedelta(days=400)
    n = 401
    for sym in ("VWCE.MI", "CSSPX.MI", "AGGH.MI", "AAPL", "MSFT", "ENI.MI", "USDEUR=X"):
        cur = "USD" if sym in ("AAPL", "MSFT") else "EUR"
        closes: list[float | None] = [
            1.0 if sym == "USDEUR=X" else 100.0 + i * 0.1 for i in range(n)
        ]
        respx.get(CHART_URL.format(symbol=sym)).mock(
            return_value=httpx.Response(200, json=chart_history(start, closes, cur))
        )
    respx.get(url__regex=r".*/v8/finance/chart/.*").mock(return_value=httpx.Response(404))
    res = await registry.call("get_performance", {"period": "1y"})
    assert res.ok, res.data
    d = res.data
    assert d["benchmark"]["symbol"] == "VWCE.MI" and d["missing_history"] == []
    actual, comp = d["actual"], d["composition"]
    assert actual["end"] == date.today().isoformat() and len(actual["series"]) > 100
    assert actual["twr_pct"] is not None and actual["volatility_pct"] is not None
    assert comp["twr_pct"] is not None and comp["max_drawdown_pct"] is not None
    assert comp["contributions"] and comp["contributions"][0]["name"]
    assert all(row[2] is not None for row in comp["series"])  # benchmark line present
    res = await registry.call("get_performance", {"period": "2w"})
    assert not res.ok
    # composition "all": bounded by price history, not by the (recent) transactions
    res = await registry.call("get_performance", {"period": "all"})
    assert res.ok, res.data
    comp = res.data["composition"]
    assert comp["start"] < (date.today() - timedelta(days=380)).isoformat()
    assert "excluded" not in comp  # all holdings are short: the window shrinks instead
    assert "no earlier price history" in comp["note"]


@respx.mock
async def test_get_performance_excludes_short_history_holdings(
    registry: ToolRegistry, ctx: ChatContext, tmp_path: Path
):
    from datetime import date, timedelta

    from pluto.market.history import HistoryService
    from tests.test_history import chart_history

    ctx.history = HistoryService(ctx.quotes.client, tmp_path / "hist")
    start = date.today() - timedelta(days=400)
    for sym in ("VWCE.MI", "CSSPX.MI", "AGGH.MI", "AAPL", "MSFT", "USDEUR=X"):
        cur = "USD" if sym in ("AAPL", "MSFT") else "EUR"
        closes: list[float | None] = [1.0 if sym == "USDEUR=X" else 100.0] * 401
        respx.get(CHART_URL.format(symbol=sym)).mock(
            return_value=httpx.Response(200, json=chart_history(start, closes, cur))
        )
    # Eni: a single bar today, like a thin listing
    respx.get(CHART_URL.format(symbol="ENI.MI")).mock(
        return_value=httpx.Response(200, json=chart_history(date.today(), [24.0], "EUR"))
    )
    respx.get(url__regex=r".*/v8/finance/chart/.*").mock(return_value=httpx.Response(404))
    res = await registry.call("get_performance", {"period": "1y"})
    assert res.ok, res.data
    comp = res.data["composition"]
    assert comp["start"] < (date.today() - timedelta(days=360)).isoformat()  # window kept
    assert [e["name"] for e in comp["excluded"]] == ["Eni S.p.A."]
    assert comp["excluded"][0]["weight_pct"] is not None and "left out" in comp["note"]
    assert all(c["name"] != "Eni S.p.A." for c in comp["contributions"])


@respx.mock
async def test_exposure_and_risk_report_unavailability_plainly(
    registry: ToolRegistry, ctx: ChatContext, tmp_path: Path
):
    from pluto.market.exposure import ExposureCache, ExposureService
    from pluto.market.history import HistoryService

    # quotes work (needed for weights), every look-through source is down
    for sym, px, cur in [
        ("VWCE.MI", 166, "EUR"),
        ("CSSPX.MI", 600, "EUR"),
        ("AGGH.MI", 5, "EUR"),
        ("AAPL", 200, "USD"),
        ("MSFT", 500, "USD"),
        ("ENI.MI", 24, "EUR"),
        ("USDEUR=X", 0.5, "EUR"),
    ]:
        respx.get(CHART_URL.format(symbol=sym), params__contains={"range": "1d"}).mock(
            return_value=httpx.Response(200, json=chart(px, cur))
        )
    respx.get("https://www.justetf.com/en/etf-profile.html").mock(
        side_effect=httpx.ConnectError("down")
    )
    respx.get("https://fc.yahoo.com").mock(side_effect=httpx.ConnectError("down"))
    ctx.exposure = ExposureService(ctx.quotes.client, ExposureCache(tmp_path / "exp.json"))
    res = await registry.call("get_exposure", {})
    assert res.ok
    # stocks still get a country from their ISIN, so the view is partial, not unavailable
    assert res.data["available"] is True
    names_unknown = {u["name"] for u in res.data["unknown"]}
    assert "Vanguard FTSE All-World UCITS ETF (Acc)" in names_unknown
    assert any("unreachable" in u["reason"] for u in res.data["unknown"])
    assert any(r["source"] == "isin" for r in res.data["instruments"])

    # risk: no history at all -> plain reason
    respx.get(url__regex=r".*/v8/finance/chart/.*").mock(return_value=httpx.Response(500))
    ctx.history = HistoryService(ctx.quotes.client, tmp_path / "hist")
    res = await registry.call("get_risk", {"period": "1y"})
    assert res.ok and res.data["available"] is False
    assert "unable to retrieve" in res.data["reason"]
