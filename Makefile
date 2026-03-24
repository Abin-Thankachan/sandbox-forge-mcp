.PHONY: setup test run docker-up docker-down docker-logs docker-ps

setup:
	uv sync --dev

test:
	uv run pytest -q

run:
	uv run sandboxforge-mcp-server

docker-up:
	./scripts/docker-deploy.sh deploy

docker-down:
	./scripts/docker-deploy.sh down

docker-logs:
	./scripts/docker-deploy.sh logs

docker-ps:
	./scripts/docker-deploy.sh ps
