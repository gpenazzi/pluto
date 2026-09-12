# Pluto

Pluto is a local, AI-first portfolio manager. You tell it what you did ("Bought 20 units of
Vanguard All-World at 166.10") and it keeps the ledger, values the portfolio intraday and
shows how it is allocated, how it performed and where the risk sits. Everything runs on your
machine: the portfolio is a small versioned JSON file in `~/.pluto`, quotes come from free
public endpoints, and the chat runs on your Claude Code subscription login, so no API key is
required.

- **Chat first.** Describe transactions in plain language. Pluto records them when the
  instrument, quantity, price and date are unambiguous and asks otherwise. A form does the
  same for anyone who prefers clicking.
- **Every change is revertible.** The portfolio is stored as an append-only version history.
  Revert or undo never deletes anything.
- **Reliable quotes.** A chain of providers (Yahoo Finance, justETF, Frankfurter for FX) with
  caching, health tracking and explicit staleness. Every price shows its source and age.
- **Analytics.** Allocation by instrument, asset class, type and currency; performance
  (time- and money-weighted, drawdown, benchmark) for your actual history and for a backtest
  of today's composition; look-through by region, country and sector from ETF holdings data;
  risk decomposition with volatility, beta and correlations.

> **Disclaimer.** Pluto is not a financial advisor and does not give investment advice. The
> figures it shows come from unofficial data sources and can be wrong, stale or incomplete.
> Please seek professional advice before making investment decisions.

## Getting started

Requires Python 3.13 with [uv](https://docs.astral.sh/uv/), Node 20+, and a logged-in
[Claude Code](https://claude.com/claude-code) CLI for the chat.

```
uv sync --all-extras
cd web && npm install && npm run build && cd ..
uv run pluto demo        # create the demo portfolio in ~/.pluto
uv run pluto serve       # opens the web UI at http://127.0.0.1:8321
```

The web UI shows the allocation donut, the holdings with quote freshness, the performance,
look-through and risk cards, the transaction form, the transaction list and the version
history, with the chat panel beside them. The header has a portfolio picker with **New** and
**Delete**.

The demo portfolio holds eight instruments (world and S&P 500 equity ETFs, a bond ETF, a
gold ETC, a property ETF, three stocks in EUR and USD) bought since January 2023, so every
view has something to show. `uv run pluto demo --force` recreates it. Create your own with
**New** in the UI or `uv run pluto init <name>`.

## How it works

- `src/pluto/core`: domain model and ledger maths, no I/O. Holdings, cost basis, valuation,
  analytics and risk are pure functions over the portfolio and price data.
- `src/pluto/store`: versioned persistence in `~/.pluto/portfolios/<name>/`.
- `src/pluto/market`: quote providers, FX, daily price history, instrument resolution
  (ISIN or name to listings) and ETF look-through, all cached under `~/.pluto/cache`.
- `src/pluto/chat`: one tool registry shared by the LLM and the JSON API; two providers
  (`claude_agent` on the subscription login, `anthropic_api` on an API key).
- `src/pluto/api`: FastAPI app serving the JSON API, the chat stream and the built UI.
- `web`: Vite + React + TypeScript frontend.

`docs/PLAN.md` holds the architecture, milestones and decisions in detail.

## Configuration

**LLM backend.** Default is your Claude Code subscription login (`claude_agent`, no key). To
use the Anthropic API instead:

```
export ANTHROPIC_API_KEY=...
uv run pluto chat --provider anthropic_api
# or: export PLUTO_LLM_PROVIDER=anthropic_api
```

`PLUTO_MODEL` overrides the model for either backend (API default `claude-opus-5`);
`PLUTO_EFFORT` sets the API backend's effort (`low` … `max`, default `medium`).
`PLUTO_HOME` moves the data directory away from `~/.pluto`.

**Cash.** Cash is tracked only after you record a deposit or withdrawal; until then buys just
create positions. `pluto cash on|off|auto` overrides that. A negative cash balance is shown as
a warning and never subtracted from the total.

## Development

```
make check                 # ruff + pyright + pytest
uv run pytest -m live      # opt-in tests against the real quote endpoints
cd web && npm test         # frontend unit tests
cd web && npm run dev      # Vite dev server; run `uv run pluto serve --no-open` alongside
```

After a frontend change, rebuild with `npm run build` and restart `pluto serve`.

## Command line reference

Everything the UI does is also a command. `uv run pluto --help` lists them all; the main ones:

```
uv run pluto chat                       # talk to Pluto in the terminal (REPL)
uv run pluto chat -m "Bought 20 units of Vanguard All World, ISIN IE00BK5BQT80, at 166.10"
uv run pluto show                       # holdings (quantities and cost)
uv run pluto value                      # intraday valuation and allocation
uv run pluto perf --period 1y           # performance: actual history and composition backtest
uv run pluto exposure                   # look-through by region, country and sector
uv run pluto risk                       # volatility, beta, correlations
uv run pluto add buy --instrument IE00BK5BQT80 --quantity 20 --price 166.10 --on 2026-09-12
uv run pluto transactions               # list transactions (ids for `remove`)
uv run pluto history                    # version history
uv run pluto revert 3                   # go back (as a new version; nothing is deleted)
uv run pluto undo                       # revert to the version before HEAD
uv run pluto resolve "Vanguard FTSE All-World"   # find an instrument and its listings
uv run pluto listing <instrument> <symbol>       # quote from a different exchange
uv run pluto classify <instrument> bond          # set an asset class explicitly
uv run pluto quotes doctor              # check every instrument against every provider
```

Portfolios:

```
uv run pluto init savings --base-currency EUR   # new empty portfolio, becomes the default
uv run pluto list                               # * marks the default
uv run pluto use demo                           # switch the default
uv run pluto delete demo                        # moved to ~/.pluto/trash, never erased
```

The last remaining portfolio cannot be deleted.

## License

MIT, see `LICENSE`.
