"""One-off: create Lima instance, compose up API, curl /health, destroy (uses repo code)."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

# Repo root (parent of examples/)
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from lima_mcp_server.backend.factory import build_backend  # noqa: E402
from lima_mcp_server.config import ServerConfig  # noqa: E402
from lima_mcp_server.db import LeaseStore  # noqa: E402
from lima_mcp_server.service import LeaseService  # noqa: E402

WORKSPACE = Path(__file__).resolve().parent


def main() -> int:
    db = Path(os.environ.get("LEASE_DB_PATH", "/tmp/sf-sample-workflow.db"))
    config = replace(ServerConfig.from_env(), db_path=db)
    store = LeaseStore(config.db_path)
    backend = build_backend(config)
    service = LeaseService(store, backend, config)

    print("create_instance (bootstrap + wait_for_ready)...", flush=True)
    created = service.create_instance(
        workspace_root=str(WORKSPACE),
        ttl_minutes=60,
        auto_bootstrap=True,
        wait_for_ready=True,
    )
    if created.get("error_code"):
        print(json.dumps(created, indent=2))
        return 1

    instance_id = created["instance_id"]
    bootstrap = created.get("bootstrap") or {}
    project_dir = (bootstrap.get("workspace_paths") or {}).get("preferred") or "/tmp/workspace"
    print(f"instance_id={instance_id} project_dir={project_dir}", flush=True)

    print("docker compose build api...", flush=True)
    b = service.docker_compose(instance_id, project_dir, "build", services=["api"])
    if b.get("error_code"):
        print(json.dumps(b, indent=2))
        service.destroy_instance(instance_id, force=True)
        return 1

    print("docker compose up api...", flush=True)
    u = service.docker_compose(instance_id, project_dir, "up", services=["api"])
    if u.get("error_code"):
        print(json.dumps(u, indent=2))
        service.destroy_instance(instance_id, force=True)
        return 1

    print("curl /health...", flush=True)
    curl = service.run_command(
        instance_id,
        "curl -sS -f http://127.0.0.1:8000/health",
        timeout_seconds=60,
    )
    print(json.dumps(curl, indent=2))
    if curl.get("exit_code") != 0:
        service.destroy_instance(instance_id, force=True)
        return 1

    print("destroy_instance...", flush=True)
    destroyed = service.destroy_instance(instance_id, force=True)
    print(json.dumps(destroyed, indent=2))
    return 0 if destroyed.get("status") == "destroyed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
