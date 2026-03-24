.PHONY: setup test run

setup:
	uv sync --dev

test:
	uv run pytest -q

run:
	uv run sandboxforge-mcp-server
