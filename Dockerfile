FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MCP_ENABLE_HTTP=1 \
    MCP_HTTP_HOST=0.0.0.0 \
    MCP_HTTP_ALLOW_NON_LOOPBACK=1 \
    MCP_HTTP_PORT=8765 \
    LEASE_DB_PATH=/app/state/leases.db \
    SANDBOX_SWEEPER_INTERVAL_SECONDS=60

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --upgrade pip && \
    pip install .

RUN mkdir -p /app/state

EXPOSE 8765

CMD ["sandboxforge-mcp-server"]
