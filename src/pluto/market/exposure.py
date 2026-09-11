"""Look-through data: what an ETF or fund holds by country and sector.

Sources are unstable by nature (scraped pages, undocumented endpoints), so every result
carries its source and date, failures are reported as facts ("no data from X: reason"),
and a cache keeps the last good answer per instrument.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel
from pydantic import Field as PField

from pluto.core.model import AssetClass, AssetType, Instrument
from pluto.store import paths

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "Accept-Language": "en",
}
MAX_AGE = timedelta(days=30)


class Exposure(BaseModel):
    """Percentages (0-100) by country and by sector. Either may be empty."""

    countries: dict[str, float] = PField(default_factory=dict)
    sectors: dict[str, float] = PField(default_factory=dict)
    source: str = ""
    as_of: date | None = None
    note: str | None = None  # why something is missing, in plain words


class ExposureError(Exception):
    pass


# --- country -> region ------------------------------------------------------------------

REGIONS: dict[str, str] = {}
for _c in ("United States", "Canada"):
    REGIONS[_c] = "North America"
for _c in (
    "United Kingdom",
    "France",
    "Germany",
    "Switzerland",
    "Netherlands",
    "Sweden",
    "Denmark",
    "Italy",
    "Spain",
    "Finland",
    "Belgium",
    "Norway",
    "Ireland",
    "Austria",
    "Portugal",
    "Luxembourg",
    "Poland",
    "Greece",
    "Czech Republic",
    "Hungary",
    "Iceland",
    "Jersey",
    "Isle of Man",
    "Guernsey",
    "Liechtenstein",
    "Monaco",
    "Supranational",
):
    REGIONS[_c] = "Europe"
REGIONS["Japan"] = "Japan"
for _c in ("Australia", "Hong Kong", "Singapore", "New Zealand", "Macau"):
    REGIONS[_c] = "Asia-Pacific ex Japan"
for _c in (
    "China",
    "Taiwan",
    "India",
    "South Korea",
    "Korea",
    "Brazil",
    "South Africa",
    "Mexico",
    "Saudi Arabia",
    "Indonesia",
    "Thailand",
    "Malaysia",
    "United Arab Emirates",
    "Turkey",
    "Chile",
    "Philippines",
    "Qatar",
    "Kuwait",
    "Peru",
    "Colombia",
    "Egypt",
    "Vietnam",
    "Argentina",
    "Hungary",
    "Czechia",
    "Cayman Islands",
    "Bermuda",
    "Russia",
    "Kazakhstan",
):
    REGIONS[_c] = "Emerging markets"
REGIONS["Israel"] = "Europe"  # MSCI classification

ISIN_COUNTRY = {
    "US": "United States",
    "CA": "Canada",
    "GB": "United Kingdom",
    "DE": "Germany",
    "FR": "France",
    "IT": "Italy",
    "ES": "Spain",
    "NL": "Netherlands",
    "CH": "Switzerland",
    "SE": "Sweden",
    "DK": "Denmark",
    "FI": "Finland",
    "NO": "Norway",
    "BE": "Belgium",
    "AT": "Austria",
    "PT": "Portugal",
    "IE": "Ireland",
    "JP": "Japan",
    "AU": "Australia",
    "HK": "Hong Kong",
    "SG": "Singapore",
    "KR": "South Korea",
    "TW": "Taiwan",
    "CN": "China",
    "IN": "India",
    "BR": "Brazil",
    "ZA": "South Africa",
    "MX": "Mexico",
    "PL": "Poland",
}


def region_of(country: str) -> str:
    """justETF's own 'Other' row (the tail beyond the top countries) stays a country bucket."""
    if country == "Other":
        return "Other countries"
    return REGIONS.get(country, "Other countries")


# --- providers ----------------------------------------------------------------------------


def _parse_table(text: str, kind: str) -> dict[str, float] | None:
    m = re.search(rf'data-testid="etf-holdings_{kind}_table".*?</table>', text, re.S)
    if not m:
        return None
    out: dict[str, float] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(0), re.S):
        cells = [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", c))).strip()
            for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        ]
        cells = [c for c in cells if c]
        if len(cells) >= 2:
            pm = re.search(r"(-?\d+(?:[.,]\d+)?)\s*%", cells[1])
            if pm:
                out[cells[0]] = float(pm.group(1).replace(",", "."))
    return out or None


async def justetf_exposure(client: httpx.AsyncClient, isin: str) -> Exposure:
    """Countries and sectors from the justETF profile page. Empty tables mean justETF has
    no breakdown for this fund (money market, commodities, multi-asset)."""
    try:
        r = await client.get(
            "https://www.justetf.com/en/etf-profile.html",
            params={"isin": isin},
            headers=HEADERS,
            follow_redirects=True,
        )
    except httpx.HTTPError as e:
        raise ExposureError(f"justETF unreachable: {type(e).__name__}") from e
    if r.status_code != 200:
        raise ExposureError(f"justETF answered HTTP {r.status_code}")
    countries = _parse_table(r.text, "countries") or {}
    sectors = _parse_table(r.text, "sectors") or {}
    if not countries and not sectors:
        if "etf-profile" not in r.text and isin not in r.text:
            raise ExposureError("justETF page layout not recognised")
        return Exposure(
            source="justetf",
            as_of=date.today(),
            note="justETF has no country or sector breakdown for this fund",
        )
    return Exposure(countries=countries, sectors=sectors, source="justetf", as_of=date.today())


YAHOO_SECTORS = {
    "technology": "Technology",
    "financial_services": "Finance",
    "healthcare": "Healthcare",
    "consumer_cyclical": "Consumer Discretionary",
    "industrials": "Industrials",
    "communication_services": "Telecommunication",
    "consumer_defensive": "Consumer Staples",
    "energy": "Energy",
    "basic_materials": "Basic Materials",
    "utilities": "Utilities",
    "realestate": "Real Estate",
}


class YahooSession:
    """Yahoo's quoteSummary needs a cookie plus a crumb; both come from two GETs."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.crumb: str | None = None

    async def ensure(self) -> str:
        if self.crumb:
            return self.crumb
        try:
            await self.client.get("https://fc.yahoo.com", headers=HEADERS, follow_redirects=True)
            r = await self.client.get(
                "https://query2.finance.yahoo.com/v1/test/getcrumb", headers=HEADERS
            )
        except httpx.HTTPError as e:
            raise ExposureError(f"Yahoo unreachable: {type(e).__name__}") from e
        if r.status_code == 429:
            raise ExposureError("Yahoo rate-limited the request (HTTP 429); try again later")
        if r.status_code != 200 or not r.text or "<" in r.text:
            raise ExposureError(f"Yahoo did not issue a session crumb (HTTP {r.status_code})")
        self.crumb = r.text.strip()
        return self.crumb

    async def summary(self, symbol: str, modules: str) -> dict[str, Any]:
        crumb = await self.ensure()
        try:
            r = await self.client.get(
                f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}",
                params={"modules": modules, "crumb": crumb},
                headers=HEADERS,
            )
        except httpx.HTTPError as e:
            raise ExposureError(f"Yahoo unreachable: {type(e).__name__}") from e
        if r.status_code == 401:
            self.crumb = None
            raise ExposureError("Yahoo rejected the session crumb")
        if r.status_code != 200:
            raise ExposureError(f"Yahoo answered HTTP {r.status_code}")
        body = r.json().get("quoteSummary") or {}
        if body.get("error") or not body.get("result"):
            raise ExposureError(
                f"Yahoo: {(body.get('error') or {}).get('description') or 'no data'}"
            )
        return body["result"][0]


async def yahoo_exposure(session: YahooSession, instrument: Instrument) -> Exposure:
    """ETFs: sector weights from topHoldings (no countries). Stocks: the company's country and
    sector from assetProfile."""
    symbol = instrument.preferred_symbol or (
        instrument.listings[0].symbol if instrument.listings else None
    )
    if not symbol:
        raise ExposureError("no symbol to look up")
    if instrument.asset_type == AssetType.STOCK:
        res = await session.summary(symbol, "assetProfile")
        ap = res.get("assetProfile") or {}
        country: str | None = ap.get("country") or None
        sector: str | None = ap.get("sector") or None
        if not country and not sector:
            raise ExposureError("Yahoo has no profile for this company")
        countries: dict[str, float] = {country: 100.0} if country else {}
        sectors_one: dict[str, float] = {sector: 100.0} if sector else {}
        return Exposure(
            countries=countries, sectors=sectors_one, source="yahoo", as_of=date.today()
        )
    res = await session.summary(symbol, "topHoldings")
    th = res.get("topHoldings") or {}
    sectors: dict[str, float] = {}
    for entry in th.get("sectorWeightings") or []:
        for key, val in entry.items():
            raw = (val or {}).get("raw")
            if raw:
                name = YAHOO_SECTORS.get(str(key)) or str(key)
                sectors[name] = round(float(raw) * 100, 2)
    if not sectors:
        return Exposure(
            source="yahoo", as_of=date.today(), note="Yahoo has no sector breakdown for this fund"
        )
    return Exposure(sectors=sectors, source="yahoo", as_of=date.today())


def stock_country_fallback(instrument: Instrument) -> Exposure | None:
    """A stock's country from its ISIN prefix or its listing exchange. No sector."""
    if instrument.asset_type != AssetType.STOCK:
        return None
    # only the ISIN is trustworthy: a listing exchange says nothing about a cross-listed
    # company (E.ON on Milan is still German)
    country = ISIN_COUNTRY.get(instrument.isin[:2]) if instrument.isin else None
    if not country:
        return None
    return Exposure(
        countries={country: 100.0},
        source="isin",
        as_of=date.today(),
        note="country inferred from the ISIN; sector unknown",
    )


# --- cache + service ------------------------------------------------------------------


class ExposureCache:
    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict[str, Exposure] = {}
        if path and path.exists():
            try:
                raw = json.loads(path.read_text())
                self.data = {k: Exposure.model_validate(v) for k, v in raw.items()}
            except (ValueError, KeyError):
                self.data = {}

    def get(self, key: str) -> Exposure | None:
        return self.data.get(key)

    def put(self, key: str, exp: Exposure) -> None:
        self.data[key] = exp
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({k: v.model_dump(mode="json") for k, v in self.data.items()}, indent=1)
            )
            tmp.replace(self.path)


def default_exposure_path() -> Path:
    return paths.home() / "cache" / "exposure.json"


@dataclass
class ExposureResult:
    exposure: Exposure | None
    error: str | None = None  # why nothing could be fetched, in plain words
    from_cache: bool = False


@dataclass
class ExposureService:
    client: httpx.AsyncClient
    cache: ExposureCache
    yahoo: YahooSession = field(init=False)

    def __post_init__(self) -> None:
        self.yahoo = YahooSession(self.client)

    async def fetch(self, instrument: Instrument) -> ExposureResult:
        """justETF (countries + sectors) for funds with an ISIN, then Yahoo (sectors, or a
        company's country and sector), then the ISIN country for stocks. Every failure is
        collected into one readable reason."""
        reasons: list[str] = []
        exp: Exposure | None = None
        if instrument.asset_type != AssetType.STOCK and instrument.isin:
            try:
                exp = await justetf_exposure(self.client, instrument.isin)
            except ExposureError as e:
                reasons.append(str(e))
        if exp is None or (not exp.countries and not exp.sectors):
            try:
                y = await yahoo_exposure(self.yahoo, instrument)
                if y.countries or y.sectors:
                    exp = (
                        y
                        if exp is None
                        else Exposure(
                            countries=exp.countries or y.countries,
                            sectors=exp.sectors or y.sectors,
                            source=f"{exp.source}+yahoo",
                            as_of=date.today(),
                        )
                    )
                elif exp is None:
                    exp = y
            except ExposureError as e:
                reasons.append(str(e))
        if (exp is None or not exp.countries) and instrument.asset_type == AssetType.STOCK:
            fb = stock_country_fallback(instrument)
            if fb is not None:
                if reasons:
                    fb.note = "; ".join([*reasons, fb.note or ""]).strip("; ")
                exp = (
                    fb
                    if exp is None
                    else Exposure(
                        countries=fb.countries,
                        sectors=exp.sectors,
                        source=f"{exp.source}+isin",
                        as_of=date.today(),
                        note=exp.note,
                    )
                )
        if exp is None:
            return ExposureResult(
                None, error="; ".join(reasons) or "no source has data for this instrument"
            )
        if reasons and not exp.note:
            exp.note = "; ".join(reasons)
        return ExposureResult(exp)

    async def get(self, instrument: Instrument, *, refresh: bool = False) -> ExposureResult:
        key = instrument.id
        cached = self.cache.get(key)
        fresh = (
            cached is not None
            and cached.as_of is not None
            and date.today() - cached.as_of <= MAX_AGE
        )
        if cached is not None and fresh and not refresh:
            return ExposureResult(cached, from_cache=True)
        res = await self.fetch(instrument)
        if res.exposure is not None:
            self.cache.put(key, res.exposure)
            return res
        if cached is not None:  # stale but better than nothing, and say so
            return ExposureResult(cached, error=res.error, from_cache=True)
        return res


# --- look-through -------------------------------------------------------------------------

NO_GEOGRAPHY = {
    AssetClass.MONEY_MARKET: "Money market",
    AssetClass.COMMODITY: "Commodities",
    AssetClass.REAL_ESTATE: "Real estate",
    AssetClass.MULTI_ASSET: "Multi-asset",
    AssetClass.OTHER: "Other",
}


@dataclass
class LookThrough:
    by_region: dict[str, float]
    by_country: dict[str, float]
    by_sector: dict[str, float]
    covered_pct: float  # share of portfolio value with look-through data
    unknown: list[dict[str, Any]]  # holdings without data: name, weight_pct, reason
    notes: list[str]


def look_through(
    weights: dict[str, float],
    instruments: dict[str, Instrument],
    exposures: dict[str, ExposureResult],
) -> LookThrough:
    """Weights are portfolio shares (sum 1). Holdings with country data spread their weight
    across countries; those without geography (money market, gold, ...) become their own
    bucket, so the picture stays complete and honest."""
    region: dict[str, float] = {}
    country: dict[str, float] = {}
    sector: dict[str, float] = {}
    unknown: list[dict[str, Any]] = []
    notes: list[str] = []
    covered = 0.0

    def add(bucket: dict[str, float], key: str, val: float) -> None:
        bucket[key] = bucket.get(key, 0.0) + val

    for ins_id, w in weights.items():
        ins = instruments[ins_id]
        res = exposures.get(ins_id)
        exp = res.exposure if res else None
        label = NO_GEOGRAPHY.get(ins.asset_class)
        if exp and exp.countries:
            covered += w
            total = sum(exp.countries.values()) or 100.0
            for c, pct in exp.countries.items():
                share = w * pct / total
                add(country, c, share)
                add(region, region_of(c), share)
        elif label:
            add(region, label, w)
            add(country, label, w)
            covered += w  # a known non-geographic bucket is not "unknown"
        else:
            add(region, "Unknown", w)
            add(country, "Unknown", w)
            unknown.append(
                {
                    "instrument_id": ins_id,
                    "name": ins.name,
                    "weight_pct": round(w * 100, 1),
                    "reason": (
                        res.error if res and res.error else (exp.note if exp else "no data")
                    ),
                }
            )
        if exp and exp.sectors:
            total = sum(exp.sectors.values()) or 100.0
            for sct, pct in exp.sectors.items():
                add(sector, sct, w * pct / total)
        elif label:
            add(sector, label, w)
        else:
            add(sector, "Unknown", w)
        if res and res.error and exp is not None:
            notes.append(f"{ins.name}: using data from {exp.as_of} ({res.error})")

    def to_pct(b: dict[str, float]) -> dict[str, float]:
        return dict(sorted(((k, round(v * 100, 2)) for k, v in b.items()), key=lambda kv: -kv[1]))

    return LookThrough(
        to_pct(region), to_pct(country), to_pct(sector), round(covered * 100, 1), unknown, notes
    )


def now_utc() -> datetime:
    return datetime.now(UTC)
