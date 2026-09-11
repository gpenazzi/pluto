"""Turn a user's description (name and/or ISIN) into concrete, quotable instruments."""

from __future__ import annotations

import asyncio
import re

import httpx
from pydantic import BaseModel, Field

from pluto.core.model import AssetClass, AssetType, Instrument, Listing
from pluto.market.providers.openfigi import OpenFigiProvider, figi_search
from pluto.market.providers.yahoo import YahooProvider
from pluto.market.types import Candidate, ProviderError

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

# Yahoo exchange codes in the order a EUR investor usually prefers them.
EXCHANGE_PRIORITY = [
    "MIL",
    "GER",
    "AMS",
    "PAR",
    "BRU",
    "MCE",
    "VIE",
    "LSE",
    "EBS",
    "NMS",
    "NYQ",
    "PCX",
]
MAX_VERIFY = 10
OTC_EXCHANGES = {"PNK", "OTC", "OQB", "OQX", "OEM"}
US_EXCHANGES = {"NMS", "NYQ", "NGM", "NCM", "PCX", "ASE", "BTS", "NAS"}

# Asset class from the name. Equity needs positive evidence (an index or equity word); a
# name that says nothing is "other", never silently "equity".
BOND_RE = re.compile(
    r"\b(bond|bonds|aggregate|treasury|treasuries|gilt|gilts|btp|bund|fixed income|govt|"
    r"government|corporate|corp bond|credit|high yield|inflation.linked|tips|"
    r"\d+\s*-\s*\d+\s*yr|\d+\s*-\s*\d+\s*y|obbligazion\w*|anleihen?|staatsanleihen?|rente)\b"
)
MONEY_MARKET_RE = re.compile(
    r"\b(overnight|money market|cash|€str|ester|estr|sofr|sonia|eonia|ultrashort|"
    r"ultra short|floating rate|liquidity|geldmarkt)\b"
)
COMMODITY_RE = re.compile(
    r"\b(gold|silver|platinum|palladium|commodity|commodities|oil|physical|metals?|rohstoff\w*)\b"
)
REAL_ESTATE_RE = re.compile(
    r"\b(reit|reits|property|real estate|immobili\w*|grundbesitz|wohnen|epra|nareit)\b"
)
MULTI_ASSET_RE = re.compile(
    r"\b(portfolio|multi.asset|multi asset|balanced|allocation|lifestrategy|"
    r"\d{2}/\d{2}|defensive|moderate|dynamic|mixed|bilanciat\w*|mischfonds)\b"
)
EQUITY_RE = re.compile(
    r"\b(equity|equities|stock|stocks|shares|share|dividend|msci|ftse|s&p|stoxx|nasdaq|"
    r"dow jones|russell|nikkei|topix|dax|mdax|sdax|tecdax|cac|ibex|smi|aex|omx|"
    r"all.world|world|emerging markets|small cap|mid cap|large cap|value|growth|"
    r"momentum|quality|minimum volatility|azionari\w*|aktien)\b"
)


def guess_asset_class(name: str, asset_type: AssetType = AssetType.ETF) -> AssetClass:
    """Only ETFs get a keyword guess; a stock is equity whatever its name says.
    Order matters: money market before bond ("EUR Overnight"), real estate and commodity
    before equity ("Global Real Estate", "Gold"), multi-asset before equity ("Portfolio")."""
    if asset_type == AssetType.STOCK:
        return AssetClass.EQUITY
    n = name.lower().replace("-", " ")
    if MONEY_MARKET_RE.search(n):
        return AssetClass.MONEY_MARKET
    if MULTI_ASSET_RE.search(n):
        return AssetClass.MULTI_ASSET
    bond = bool(BOND_RE.search(n))
    equity = bool(EQUITY_RE.search(n))
    if bond and equity:
        return AssetClass.MULTI_ASSET
    if bond:
        return AssetClass.BOND
    if COMMODITY_RE.search(n):
        return AssetClass.COMMODITY
    if REAL_ESTATE_RE.search(n):
        return AssetClass.REAL_ESTATE
    if equity:
        return AssetClass.EQUITY
    return AssetClass.OTHER


def is_isin(s: str) -> bool:
    return bool(ISIN_RE.match(s.upper().strip()))


class ResolvedInstrument(BaseModel):
    name: str
    isin: str | None
    asset_type: AssetType
    asset_class: AssetClass
    listings: list[Listing]
    preferred_symbol: str
    currency: str
    confidence: float  # 0..1
    notes: list[str] = Field(default_factory=list)

    def to_instrument(self) -> Instrument:
        return Instrument(
            id=self.isin or self.preferred_symbol,
            isin=self.isin,
            name=self.name,
            asset_type=self.asset_type,
            asset_class=self.asset_class,
            currency=self.currency,
            listings=self.listings,
            preferred_symbol=self.preferred_symbol,
        )


class ResolveResult(BaseModel):
    query: str | None
    isin: str | None
    matches: list[ResolvedInstrument]
    notes: list[str] = Field(default_factory=list)

    @property
    def unique(self) -> ResolvedInstrument | None:
        strong = [m for m in self.matches if m.confidence >= 0.8]
        return strong[0] if len(strong) == 1 else None


class InstrumentResolver:
    def __init__(
        self,
        client: httpx.AsyncClient,
        yahoo: YahooProvider | None = None,
        openfigi: OpenFigiProvider | None = None,
    ):
        self.yahoo = yahoo or YahooProvider(client)
        self.openfigi = openfigi or OpenFigiProvider(client)

    async def resolve(
        self, query: str | None = None, isin: str | None = None, prefer_currency: str = "EUR"
    ) -> ResolveResult:
        notes: list[str] = []
        if query and not isin and is_isin(query):
            isin, query = query.upper().strip(), None
        if isin:
            isin = isin.upper().strip()
            if not is_isin(isin):
                return ResolveResult(
                    query=query, isin=isin, matches=[], notes=[f"{isin!r} is not a valid ISIN"]
                )
            m = await self._by_isin(isin, query, prefer_currency, notes)
            return ResolveResult(query=query, isin=isin, matches=[m] if m else [], notes=notes)
        if query:
            ms = await self._by_name(query, prefer_currency, notes)
            return ResolveResult(query=query, isin=None, matches=ms, notes=notes)
        return ResolveResult(query=None, isin=None, matches=[], notes=["nothing to resolve"])

    # --- ISIN path -------------------------------------------------------------------
    async def _by_isin(
        self, isin: str, hint: str | None, prefer: str, notes: list[str]
    ) -> ResolvedInstrument | None:
        figi: list[Candidate] = []
        ysearch: list[Candidate] = []
        r1, r2 = await asyncio.gather(
            self.openfigi.map_isin(isin), self.yahoo.search(isin), return_exceptions=True
        )
        if isinstance(r1, BaseException):
            notes.append(f"openfigi unavailable: {r1}")
        else:
            figi = r1
        if isinstance(r2, BaseException):
            notes.append(f"yahoo search unavailable: {r2}")
        else:
            ysearch = [
                c for c in r2 if not c.symbol.startswith(isin)
            ]  # drop fund-fact pseudo symbols
        symbols: list[str] = []
        for c in [*ysearch, *figi]:
            if c.symbol not in symbols:
                symbols.append(c.symbol)
        if not symbols:
            notes.append(f"no listing found for ISIN {isin}")
            return None
        listings, meta_names, meta_types = await self._verify(symbols[:MAX_VERIFY], prefer)
        if not listings:
            notes.append(
                f"found symbols {symbols[:MAX_VERIFY]} for {isin} but none returned a price"
            )
            return None
        figi_type = next((c.quote_type for c in figi if c.quote_type), None)
        asset_type = _asset_type(meta_types, figi_type)
        name = _best_name(meta_names, [c.name for c in ysearch] + [c.name for c in figi], hint)
        preferred = _pick_preferred(
            listings, prefer, us_company=asset_type == AssetType.STOCK and isin.startswith("US")
        )
        return ResolvedInstrument(
            name=name,
            isin=isin,
            asset_type=asset_type,
            asset_class=guess_asset_class(name),
            listings=listings,
            preferred_symbol=preferred.symbol,
            currency=preferred.currency,
            confidence=1.0,
            notes=[],
        )

    # --- name path -------------------------------------------------------------------
    async def _by_name(self, query: str, prefer: str, notes: list[str]) -> list[ResolvedInstrument]:
        """Yahoo search is literal ("Vanguard All World" misses "FTSE All-World"), so OpenFIGI's
        fuzzy search is consulted too. Every symbol is verified with a live quote, then grouped
        by the verified long name: one group = one instrument, its members = the listings."""
        r1, r2 = await asyncio.gather(
            self.yahoo.search(query, limit=12),
            figi_search(self.openfigi, query),
            return_exceptions=True,
        )
        cands: list[Candidate] = []
        yahoo_hits: list[Candidate] = []
        if isinstance(r1, BaseException):
            notes.append(f"yahoo search unavailable: {r1}")
        else:
            yahoo_hits = [c for c in r1 if c.quote_type in ("ETF", "EQUITY")]
            cands += yahoo_hits
        if isinstance(r2, BaseException):
            notes.append(f"openfigi search unavailable: {r2}")
        else:
            cands += r2
        symbols: list[str] = []
        for c in cands:
            if c.symbol not in symbols and c.exchange not in OTC_EXCHANGES:
                symbols.append(c.symbol)
        if not symbols:
            notes.append(
                f"no ETF or stock matches {query!r}; try the official fund name or the ISIN"
            )
            return []
        verified = await self._verify_all(symbols[:MAX_VERIFY])
        groups = _group_by_name(verified)
        # Yahoo lists a company's home listing first: a US company's first hit is on a US exchange
        first_hit = {_norm(c.name or ""): c.exchange for c in reversed(yahoo_hits)}
        out: list[ResolvedInstrument] = []
        for members in groups.values():
            listings = [m[0] for m in members]
            name = max((m[1] for m in members), key=len)
            asset_type = _asset_type([m[2] for m in members], None)
            us_company = (
                asset_type == AssetType.STOCK and first_hit.get(_norm(name)) in US_EXCHANGES
            )
            preferred = _pick_preferred(listings, prefer, us_company=us_company)
            out.append(
                ResolvedInstrument(
                    name=name,
                    isin=None,
                    asset_type=asset_type,
                    asset_class=guess_asset_class(name, asset_type),
                    listings=listings,
                    preferred_symbol=preferred.symbol,
                    currency=preferred.currency,
                    confidence=0.0,
                    notes=["ISIN unknown: ask the user for it to be sure"],
                )
            )
        out.sort(key=lambda m: -len(m.listings))
        exact = [
            m for m in out if any(ls.symbol.upper() == query.strip().upper() for ls in m.listings)
        ]
        if len(out) == 1:
            out[0].confidence = 0.85
        elif len(exact) == 1:  # the query is literally one listing's symbol
            for m in out:
                m.confidence = 0.3
            exact[0].confidence = 0.9
            exact[0].notes.append(f"query {query!r} is exactly this instrument's symbol")
            out.remove(exact[0])
            out.insert(0, exact[0])
        else:
            for m in out:
                m.confidence = 0.4
            notes.append(f"{len(out)} different instruments match {query!r}; ask which one")
        if not out:
            notes.append(f"found symbols for {query!r} but none returned a price")
        return out

    async def _verify_all(self, symbols: list[str]) -> list[tuple[Listing, str, str]]:
        async def one(sym: str):
            try:
                return sym, await self.yahoo.chart_meta(sym)
            except ProviderError:
                return sym, None

        out: list[tuple[Listing, str, str]] = []
        for sym, meta in await asyncio.gather(*(one(s) for s in symbols)):
            if not meta or meta.get("exchangeName") in OTC_EXCHANGES:
                continue
            cur = meta.get("currency") or "?"
            if cur in ("GBp", "GBX"):
                cur = "GBP"
            listing = Listing(symbol=sym, exchange=meta.get("exchangeName") or "?", currency=cur)
            name = meta.get("longName") or meta.get("shortName") or sym
            out.append((listing, name, meta.get("instrumentType") or ""))
        return out

    async def _verify(
        self, symbols: list[str], prefer: str
    ) -> tuple[list[Listing], list[str], list[str]]:
        async def one(sym: str):
            try:
                return sym, await self.yahoo.chart_meta(sym)
            except ProviderError:
                return sym, None

        results = await asyncio.gather(*(one(s) for s in symbols))
        listings: list[Listing] = []
        names: list[str] = []
        types: list[str] = []
        for sym, meta in results:
            if not meta or meta.get("exchangeName") in OTC_EXCHANGES:
                continue
            cur = meta.get("currency") or "?"
            if cur in ("GBp", "GBX"):
                cur = "GBP"
            listings.append(
                Listing(symbol=sym, exchange=meta.get("exchangeName") or "?", currency=cur)
            )
            if meta.get("longName"):
                names.append(meta["longName"])
            elif meta.get("shortName"):
                names.append(meta["shortName"])
            if meta.get("instrumentType"):
                types.append(meta["instrumentType"])
        listings.sort(key=lambda ls: (ls.currency != prefer, _exchange_rank(ls.exchange)))
        return listings, names, types


def _group_by_name(
    verified: list[tuple[Listing, str, str]],
) -> dict[str, list[tuple[Listing, str, str]]]:
    """Group listings by normalised name. Yahoo truncates some names ("... UCITS E"), so a
    key that is a prefix of a longer key is merged into the longer one."""
    keys = {_norm(name) for _, name, _ in verified}

    def truncated_prefix(short: str, long: str) -> bool:
        """'... ucits e' is a truncation of '... ucits etf usd acc'; '... ucits etf' is not
        (a complete name that happens to be a prefix, e.g. the Dist vs Acc share class)."""
        st, lt = short.split(), long.split()
        if len(st) < 3 or len(lt) < len(st) or lt[: len(st) - 1] != st[:-1]:
            return False
        return lt[len(st) - 1] != st[-1] and lt[len(st) - 1].startswith(st[-1])

    def canonical(key: str) -> str:
        # shortest completion first: a truncation is attributed to the most conservative match
        for longer in sorted(keys, key=len):
            if len(longer) > len(key) and truncated_prefix(key, longer):
                return longer
        return key

    groups: dict[str, list[tuple[Listing, str, str]]] = {}
    for listing, name, kind in verified:
        groups.setdefault(canonical(_norm(name)), []).append((listing, name, kind))
    return groups


def _exchange_rank(exchange: str) -> int:
    return EXCHANGE_PRIORITY.index(exchange) if exchange in EXCHANGE_PRIORITY else 99


def _pick_preferred(listings: list[Listing], prefer: str, *, us_company: bool = False) -> Listing:
    """Sort listings in place by quoting preference and return the first. A US company is
    quoted from its home US exchange (liquid, intraday); everything else from the portfolio's
    currency first, then the exchange priority list."""

    def rank(ls: Listing) -> tuple[int, bool, int]:
        home = 0 if (us_company and ls.exchange in US_EXCHANGES) else 1
        return (home, ls.currency != prefer, _exchange_rank(ls.exchange))

    listings.sort(key=rank)
    return listings[0]


def _asset_type(meta_types: list[str], fallback: str | None) -> AssetType:
    kinds = set(meta_types) | ({fallback} if fallback else set())
    if "ETF" in kinds:
        return AssetType.ETF
    if "EQUITY" in kinds:
        return AssetType.STOCK
    return AssetType.ETF if "MUTUALFUND" in kinds else AssetType.STOCK


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _best_name(meta_names: list[str], other: list[str | None], hint: str | None) -> str:
    cands = [n for n in meta_names if n] + [n for n in other if n]
    if not cands:
        return hint or "?"
    return max(cands, key=len)  # Yahoo longName is the most descriptive one
