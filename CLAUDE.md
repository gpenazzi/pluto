# Pluto

Local, AI-first portfolio manager. Read `docs/PLAN.md` first: it holds the architecture,
the milestone order (strict) and the decisions log. Keep both up to date.

## Commands
- `uv sync --all-extras` installs everything.
- `uv run pluto --help` for the CLI. `uv run pluto demo` loads the demo portfolio.
- `make check` = ruff + pyright + pytest. Run before claiming anything works.
- `uv run pytest -m live` runs the opt-in tests that hit real quote endpoints.

## Rules
- `src/pluto/core` has no I/O and no network. Keep it that way.
- Portfolio changes go through `VersionStore.commit`; never mutate a saved version.
- Every quote carries source, as_of and staleness. Never return a bare number.
- User data lives in `~/.pluto`, never in the repo. Tests use `tmp_path`.
- Network in tests is mocked with `respx`; live tests are marked `@pytest.mark.live`.
