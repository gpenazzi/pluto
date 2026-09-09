# Pluto

A local, AI-first portfolio manager. See `docs/PLAN.md`.

```
uv sync --all-extras
uv run pluto demo        # create the demo portfolio in ~/.pluto
uv run pluto show        # holdings
uv run pluto value       # intraday valuation and allocation
uv run pluto chat        # talk to Pluto (needs a logged-in Claude Code CLI)
uv run pluto chat -m "Bought 20 units of Vanguard All World, ISIN IE00BK5BQT80, at 166.10"
```
