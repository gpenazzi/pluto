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

## Web UI

```
cd web && npm install && npm run build   # once, and after UI changes
uv run pluto serve                       # serves web/dist and the API
```

For UI development run `uv run pluto serve --no-open` and `cd web && npm run dev`; Vite
proxies `/api` to the Python server.
