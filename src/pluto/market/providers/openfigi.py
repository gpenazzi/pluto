"""OpenFIGI: ISIN -> listings (ticker + exchange code). No key needed at low volume."""

from __future__ import annotations

import httpx

from pluto.market.providers.base import request_json
from pluto.market.types import Candidate

URL = "https://api.openfigi.com/v3/mapping"

# OpenFIGI exchange code -> (Yahoo suffix, currency). Only exchanges Yahoo quotes well.
EXCHANGES: dict[str, tuple[str, str]] = {
    "IM": (".MI", "EUR"),  # Borsa Italiana
    "GR": (".DE", "EUR"),  # Xetra
    "GY": (".DE", "EUR"),  # Xetra (stocks)
    "NA": (".AS", "EUR"),  # Euronext Amsterdam
    "FP": (".PA", "EUR"),  # Euronext Paris
    "BB": (".BR", "EUR"),  # Euronext Brussels
    "SM": (".MC", "EUR"),  # Madrid
    "PL": (".LS", "EUR"),  # Lisbon
    "ID": (".IR", "EUR"),  # Dublin
    "AV": (".VI", "EUR"),  # Vienna
    "XD": (".XD", "EUR"),  # Cboe Europe (DXE)
    "LN": (".L", "GBP"),  # London
    "SW": (".SW", "CHF"),  # SIX
    "SE": (".SW", "CHF"),
    "SS": (".ST", "SEK"),
    "DC": (".CO", "DKK"),
    "NO": (".OL", "NOK"),
    "US": ("", "USD"),
    "UN": ("", "USD"),  # NYSE
    "UW": ("", "USD"),  # Nasdaq GS
    "UQ": ("", "USD"),
    "UA": ("", "USD"),  # NYSE American
    "UP": ("", "USD"),  # NYSE Arca
    "CN": (".TO", "CAD"),
    "CT": (".TO", "CAD"),
    "JT": (".T", "JPY"),
    "JP": (".T", "JPY"),
    "HK": (".HK", "HKD"),
    "AU": (".AX", "AUD"),
}

SECURITY_TYPES = {"ETP": "ETF", "Common Stock": "EQUITY", "ADR": "EQUITY", "REIT": "EQUITY"}


class OpenFigiProvider:
    name = "openfigi"

    def __init__(self, client: httpx.AsyncClient, api_key: str | None = None):
        self.client = client
        self.api_key = api_key

    async def map_isin(self, isin: str) -> list[Candidate]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-OPENFIGI-APIKEY"] = self.api_key
        body = [{"idType": "ID_ISIN", "idValue": isin}]
        jobs = await request_json(self.client, "POST", URL, json=body, headers=headers)
        out: list[Candidate] = []
        seen: set[str] = set()
        for row in (jobs[0] if jobs else {}).get("data") or []:
            exch = row.get("exchCode")
            if exch not in EXCHANGES:
                continue
            suffix, currency = EXCHANGES[exch]
            ticker = (row.get("ticker") or "").replace(" ", "-").replace("*", "")
            if not ticker or "/" in ticker:
                continue
            symbol = f"{ticker}{suffix}"
            if symbol in seen:
                continue
            seen.add(symbol)
            out.append(
                Candidate(
                    symbol=symbol,
                    exchange=exch,
                    currency=currency,
                    name=row.get("name"),
                    quote_type=SECURITY_TYPES.get(row.get("securityType") or ""),
                    isin=isin,
                    source=self.name,
                )
            )
        return out


SEARCH_URL = "https://api.openfigi.com/v3/search"


async def figi_search(provider: OpenFigiProvider, query: str, limit: int = 20) -> list[Candidate]:
    """Fuzzy name search. Names are abbreviated (e.g. 'VANG FTSE AW USDA'); symbols must be
    verified against a quote source before use."""
    headers = {"Content-Type": "application/json"}
    if provider.api_key:
        headers["X-OPENFIGI-APIKEY"] = provider.api_key
    data = await request_json(
        provider.client, "POST", SEARCH_URL, json={"query": query}, headers=headers
    )
    out: list[Candidate] = []
    seen: set[str] = set()
    for row in (data or {}).get("data") or []:
        exch = row.get("exchCode")
        sec = row.get("securityType") or ""
        if exch not in EXCHANGES or sec not in SECURITY_TYPES:
            continue
        suffix, currency = EXCHANGES[exch]
        ticker = (row.get("ticker") or "").replace(" ", "-").replace("*", "")
        if not ticker or "/" in ticker:
            continue
        symbol = f"{ticker}{suffix}"
        if symbol in seen:
            continue
        seen.add(symbol)
        out.append(
            Candidate(
                symbol=symbol,
                exchange=exch,
                currency=currency,
                name=row.get("name"),
                quote_type=SECURITY_TYPES[sec],
                source=provider.name,
            )
        )
        if len(out) >= limit:
            break
    return out
