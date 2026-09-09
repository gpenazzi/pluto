.PHONY: check lint type test
check: lint type test
lint:
	uv run ruff check src tests && uv run ruff format --check src tests
type:
	uv run pyright
test:
	uv run pytest -q
