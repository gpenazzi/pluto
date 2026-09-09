# Pluto

Local, AI-first portfolio manager. Read `docs/PLAN.md` first: it holds the architecture,
the milestone order (strict) and the decisions log. Keep both up to date.

## Commands
- `uv sync --all-extras` installs everything.
- `uv run pluto --help` for the CLI. `uv run pluto demo` loads the demo portfolio.
- `make check` = ruff + pyright + pytest. Run before claiming anything works.
- `uv run pytest -m live` runs the opt-in tests that hit real quote endpoints.
- Web UI: `cd web && npm run build` then `uv run pluto serve`. Dev: `npm run dev` + `pluto serve --no-open`.
  `npm run build` runs `tsc -b`, so it is also the frontend type check.
- `uv run pluto chat -m "..."` is the quickest live check of the LLM layer (uses the
  Claude Code subscription login, no API key). Point `PLUTO_HOME` at a scratch dir first.

## Rules
- `src/pluto/core` has no I/O and no network. Keep it that way.
- Portfolio changes go through `VersionStore.commit`; never mutate a saved version.
- Every quote carries source, as_of and staleness. Never return a bare number.
- User data lives in `~/.pluto`, never in the repo. Tests use `tmp_path`.
- Network in tests is mocked with `respx`; live tests are marked `@pytest.mark.live`.
- The JSON API (`src/pluto/api/app.py`) calls the same tool registry as the chat. Add a
  capability to the registry once; both the LLM and the GUI get it.
- LLM tools live in `src/pluto/chat/tools.py` only; providers adapt that registry and
  contain no portfolio logic. Test tool behaviour with `ScriptedProvider`, not a real LLM.
