from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _parse_bool(raw: str, default: bool) -> bool:
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


@dataclass
class ServerConfig:
    max_instances: int = 3
    default_ttl_minutes: int = 30
    max_ttl_minutes: int = 120
    sweeper_interval_seconds: int = 60
    db_path: Path = Path("state/leases.db")
    http_host: str = "127.0.0.1"
    http_port: int = 8765
    enable_http: bool = True

    @classmethod
    def from_env(cls) -> "ServerConfig":
        host = os.getenv("MCP_HTTP_HOST", "127.0.0.1")
        if host != "127.0.0.1":
            # v1 scope only allows loopback exposure.
            host = "127.0.0.1"

        return cls(
            max_instances=int(os.getenv("MAX_INSTANCES", "3")),
            default_ttl_minutes=int(os.getenv("DEFAULT_TTL_MINUTES", "30")),
            max_ttl_minutes=int(os.getenv("MAX_TTL_MINUTES", "120")),
            sweeper_interval_seconds=int(os.getenv("LIMA_SWEEPER_INTERVAL_SECONDS", "60")),
            db_path=Path(os.getenv("LEASE_DB_PATH", "state/leases.db")),
            http_host=host,
            http_port=int(os.getenv("MCP_HTTP_PORT", "8765")),
            enable_http=_parse_bool(os.getenv("MCP_ENABLE_HTTP", "1"), True),
        )
