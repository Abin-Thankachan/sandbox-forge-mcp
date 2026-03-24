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
    backend: str = "auto"
    hyperv_switch_name: str = "Default Switch"
    hyperv_base_vhdx: Path | None = None
    hyperv_storage_dir: Path = Path("state/hyperv")
    hyperv_ssh_user: str = "ubuntu"
    hyperv_ssh_key_path: Path | None = None
    hyperv_ssh_port: int = 22
    hyperv_boot_timeout_seconds: int = 180

    @classmethod
    def from_env(cls) -> "ServerConfig":
        host = os.getenv("MCP_HTTP_HOST", "127.0.0.1")
        allow_non_loopback = _parse_bool(os.getenv("MCP_HTTP_ALLOW_NON_LOOPBACK", "0"), False)
        if host != "127.0.0.1" and not allow_non_loopback:
            # Default behavior is loopback-only for safety.
            # Explicit non-loopback binds (for example Docker) require MCP_HTTP_ALLOW_NON_LOOPBACK=1.
            host = "127.0.0.1"

        return cls(
            max_instances=int(os.getenv("MAX_INSTANCES", "3")),
            default_ttl_minutes=int(os.getenv("DEFAULT_TTL_MINUTES", "30")),
            max_ttl_minutes=int(os.getenv("MAX_TTL_MINUTES", "120")),
            sweeper_interval_seconds=int(os.getenv("SANDBOX_SWEEPER_INTERVAL_SECONDS", "60")),
            db_path=Path(os.getenv("LEASE_DB_PATH", "state/leases.db")),
            http_host=host,
            http_port=int(os.getenv("MCP_HTTP_PORT", "8765")),
            enable_http=_parse_bool(os.getenv("MCP_ENABLE_HTTP", "1"), True),
            backend=os.getenv("SANDBOX_BACKEND", "auto"),
            hyperv_switch_name=os.getenv("HYPERV_SWITCH_NAME", "Default Switch"),
            hyperv_base_vhdx=(
                Path(os.getenv("HYPERV_BASE_VHDX", "")).expanduser()
                if os.getenv("HYPERV_BASE_VHDX", "").strip()
                else None
            ),
            hyperv_storage_dir=Path(os.getenv("HYPERV_STORAGE_DIR", "state/hyperv")).expanduser(),
            hyperv_ssh_user=os.getenv("HYPERV_SSH_USER", "ubuntu"),
            hyperv_ssh_key_path=(
                Path(os.getenv("HYPERV_SSH_KEY_PATH", "")).expanduser()
                if os.getenv("HYPERV_SSH_KEY_PATH", "").strip()
                else None
            ),
            hyperv_ssh_port=int(os.getenv("HYPERV_SSH_PORT", "22")),
            hyperv_boot_timeout_seconds=int(os.getenv("HYPERV_BOOT_TIMEOUT_SECONDS", "180")),
        )
