# Pluto — plan

Pluto is a local, AI-first portfolio manager. You talk to it ("Bought 100 units of
Vanguard All World, ISIN IE00BK5BQT80"), it keeps the ledger, values the portfolio
intraday and shows how it is allocated. A GUI exists as a fallback for everything the
chat can do.

This is a restart. The previous attempt is not reused. Three things drive the design:

1. **Intraday quotes must be reliable.** No single free source is dependable, so quotes
   come from a chain of providers with caching, health tracking and explicit staleness.
2. **The LLM backend is swappable.** First backend is the Claude subscription (Claude
   Agent SDK driving the local Claude Code CLI, no API key). The Anthropic API is the
   second. Both sit behind one `ChatProvider` interface and share one tool registry.
3. **Every change to the portfolio is revertible.** The portfolio is a small JSON
   document stored as an immutable, linear version history. Revert never deletes.

## Scope of the first deliverable

- Load a demo portfolio (ETFs + stocks, EUR base currency).
- Modify it via chat (LLM adds transactions when confident, asks when ambiguous) or
  via GUI form.
- Save; browse versions; revert to any earlier version.
- Intraday valuation and a composition pie chart (by instrument, asset type, currency).

Out of scope for now: performance analytics (TWR, XIRR), risk, benchmarks, document
import, multi-portfolio, broker sync. They come after the first deliverable.

## Architecture

```
src/pluto/
  core/        domain model + ledger maths, no I/O (pydantic models, pure functions)
    model.py       Instrument, Listing, Transaction, Portfolio
    holdings.py    positions, average cost, cost basis from transactions
    valuation.py   market value, weights, allocation breakdowns
    demo.py        the demo portfolio
  store/       versioned persistence in ~/.pluto/portfolios/<name>/
    versions.py    VersionStore: append-only versions + HEAD pointer, revert = new version
  market/      quotes, FX, instrument resolution
    types.py       Quote, FxRate, Candidate
    providers/     yahoo.py (chart API), justetf.py (ISIN ETF quotes), frankfurter.py (FX),
                   openfigi.py (ISIN -> listings)
    service.py     QuoteService: provider chain, cache, health, staleness
    resolver.py    InstrumentResolver: ISIN/name -> candidates with confidence
  chat/        LLM layer
    tools.py       provider-agnostic tool registry (schema + handler)
    providers/     base.py (ChatProvider protocol + events), claude_agent.py,
                   anthropic_api.py, scripted.py (for tests)
    session.py     conversation persistence
  api/         FastAPI app (JSON + SSE chat stream), serves the built web UI
  cli.py       typer CLI: demo, show, value, add, history, revert, quotes-doctor, chat, serve
web/           Vite + React + TypeScript UI (dashboard, pie chart, chat, form, versions)
tests/         pytest; network calls are mocked with respx, live checks are opt-in
```

Data layout on disk (outside the repo, by design):

```
~/.pluto/
  portfolios/<name>/versions/000001.json ...   immutable portfolio snapshots
  portfolios/<name>/HEAD                       current version number
  cache/quotes.json                            last known quotes (survive outages)
  chats/                                       conversation transcripts
```

### Quotes: how reliability is achieved

- Each instrument carries several **listings** (symbol per exchange) plus its ISIN.
  Providers get every listing to try, not a single ticker.
- **Provider chain**, tried in order until one succeeds:
  1. Yahoo Finance chart endpoint (`/v8/finance/chart`, no auth, true intraday, broad
     coverage incl. Milan/Xetra/LSE). Verified working 2026-09-09.
  2. justETF quote API (ISIN-based, ETFs only, XETRA, ~15 min delay). Verified.
  3. Cached last-known quote, marked **stale** with its age.
- Every `Quote` carries `source`, `as_of`, `currency`, `is_stale`. The UI shows all of it.
- Per-provider **health**: consecutive failures put a provider in cooldown so one broken
  source does not slow every refresh.
- FX via Frankfurter (ECB, daily) with Yahoo `EURUSD=X` as intraday fallback.
- `pluto quotes doctor` checks every portfolio instrument against every provider and
  prints a matrix, so a breakage is visible immediately rather than as a silent zero.
- Adding a provider is one file implementing `QuoteProvider`; Twelve Data / Finnhub
  (API-key sources) are planned as optional extras.

### Instrument resolution (what the chat relies on)

`resolve_instrument(query, isin=None)` returns candidates with a confidence score:
- ISIN given: OpenFIGI mapping (ISIN -> all listings) + Yahoo search by ISIN + justETF
  for ETF metadata. An ISIN identifies the instrument uniquely; only the preferred
  listing (exchange/currency) may remain to be chosen, defaulting to the portfolio's
  base-currency exchange.
- Name only: Yahoo search. If more than one plausible candidate, the tool says so and
  the LLM must ask the user before adding anything.

### Chat contract

The LLM sees these tools: `get_portfolio`, `resolve_instrument`, `add_transaction`,
`list_transactions`, `revert_to_version`, `get_quote`. The system prompt states the
rule the user gave: add a transaction only when instrument, quantity, price and date
are unambiguous; otherwise ask. Every `add_transaction` creates a new version, so a
wrong add is one revert away.

## Milestones

| # | Milestone | Done when |
|---|-----------|-----------|
| M0 | Scaffold | uv project, package layout, ruff + pyright + pytest green, this plan, CLAUDE.md |
| M1 | Ledger core | model, holdings maths, versioned store with revert, demo portfolio, CLI `demo/show/add/history/revert`, unit tests |
| M2 | Market data | Yahoo + justETF + Frankfurter + OpenFIGI providers, QuoteService chain + cache + health, resolver, valuation + allocation, CLI `value` and `quotes doctor`, mocked tests + opt-in live test |
| M3 | Chat | tool registry, ChatProvider protocol, Claude Agent SDK provider, scripted provider, CLI `chat`, end-to-end test with scripted provider, manual live test |
| M4 | Web UI | FastAPI API, React dashboard: pie chart, holdings table with quote freshness, transaction form, version list + revert, chat panel with streaming. **First deliverable complete.** |
| M5 | API backend | `AnthropicApiProvider` (claude-opus-5, tool loop), provider selected by config/env |
| M6+ | Analytics | performance (TWR/XIRR), allocation by region/sector via ETF metadata, benchmarks, risk |

Execution order is strict: a milestone is finished (tests green, checked by hand)
before the next starts.

### Status

- 2026-09-09: M0, M1, M2 done. `pluto demo/show/add/remove/history/revert/undo`,
  `pluto value`, `pluto quotes doctor`, `pluto resolve` all verified against live endpoints.
- 2026-09-09: M3 done. `pluto chat` (REPL or `-m "one message"`) runs on the Claude
  Agent SDK with the subscription login; 11 tools behind one registry; scripted provider
  for tests; transcripts in `~/.pluto/chats/`. Verified live: valuation question, ISIN
  purchase recorded without confirmation, ambiguous name triggers a question, multi-turn
  disambiguation then undo.
- 2026-09-09: M4 done. `pluto serve` runs FastAPI (JSON API over the same tool registry
  as the chat, SSE chat stream) and serves the built React UI from `web/dist`: hero
  total, allocation donut with breakdown switch and legend, holdings table with quote
  source/age/staleness and provider health, transaction form + instrument search, transaction
  list with remove, version list with revert, streaming chat panel. **First deliverable
  complete.** Next: M5 (Anthropic API provider).

### Known limits to revisit

- Name-only resolution depends on Yahoo's literal search plus OpenFIGI's fuzzy search;
  "Vanguard All World" does not surface VWCE, "Vanguard FTSE All-World" does. The chat
  layer should retry with the official fund name and otherwise ask for the ISIN.
- Instruments resolved by name have no ISIN; their id is the preferred symbol. If the
  user later provides the ISIN, the instrument must be re-keyed (not yet implemented).
- justETF's quote is end-of-day/15-min delayed and only for ETFs; Yahoo is the only
  intraday source so far. A third intraday source (Twelve Data, key required) is the
  planned next step for the provider chain.

## Decisions log

- 2026-09-09 Python 3.13 via uv; single package, src layout. Web UI in TypeScript
  (Vite + React) because Node 26 is already installed and a chart + chat UI is simplest there.
- 2026-09-09 Versioned JSON documents instead of SQLite. Portfolios are tiny; a linear
  history of full snapshots is trivially correct, human-readable and diffable.
- 2026-09-09 Call Yahoo's chart endpoint directly with httpx instead of depending on
  `yfinance`: fewer moving parts, one endpoint to monitor, no crumb/cookie dance
  (the `/v7/finance/quote` endpoint needs one and is not used).
- 2026-09-09 Subscription backend = `claude-agent-sdk` (`ClaudeSDKClient` with an
  in-process MCP tool server, built-in tools disabled). API backend = `anthropic` SDK.
