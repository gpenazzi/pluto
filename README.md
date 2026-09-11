# Pluto

A local, AI-first portfolio manager. See `docs/PLAN.md`.

```
uv sync --all-extras
uv run pluto demo        # create the demo portfolio in ~/.pluto
uv run pluto show        # holdings
uv run pluto value       # intraday valuation and allocation
uv run pluto chat        # talk to Pluto (needs a logged-in Claude Code CLI)
uv run pluto serve       # web UI at http://127.0.0.1:8321 (build it first, see below)
uv run pluto chat -m "Bought 20 units of Vanguard All World, ISIN IE00BK5BQT80, at 166.10"
```

## Portfolios

In the web UI the header has a portfolio picker with **New** and **Delete**. From the terminal:

```
uv run pluto init savings --base-currency EUR   # new empty portfolio, becomes the default
uv run pluto list                               # * marks the default
uv run pluto use demo                           # switch the default
uv run pluto delete demo                        # moved to ~/.pluto/trash, never erased
```

The last remaining portfolio cannot be deleted.

Cash is tracked only after you record a deposit or withdrawal; until then buys just
create positions. `pluto cash on|off|auto` overrides that. A negative cash balance is
shown as a warning and never subtracted from the total.

## Web UI

```
cd web && npm install && npm run build   # once, and after UI changes
uv run pluto serve                       # serves web/dist and the API
```

For UI development run `uv run pluto serve --no-open` and `cd web && npm run dev`; Vite
proxies `/api` to the Python server.

## LLM backend

Default is your Claude Code subscription login (`claude_agent`, no key). To use the Anthropic
API instead:

```
export ANTHROPIC_API_KEY=...            # or `ant auth login`
uv run pluto chat --provider anthropic_api
# or: export PLUTO_LLM_PROVIDER=anthropic_api
```

`PLUTO_MODEL` overrides the model for either backend (API default `claude-opus-5`);
`PLUTO_EFFORT` sets the API backend's effort (`low` … `max`, default `medium`).
