from __future__ import annotations

from pathlib import Path

from lima_mcp_server.config import ServerConfig
from lima_mcp_server.db import LeaseStore
from lima_mcp_server.service import LeaseService
from lima_mcp_server.timeutil import to_iso8601, utc_now


class CleanupBackend:
    available = True
    backend_name = "lima"
    version = "limactl 1.0.0"
    unavailable_reason = ""

    def __init__(self) -> None:
        self.actions: list[str] = []

    def list_instances(self):
        return []

    def stop_instance(self, lima_name: str, force: bool = False):
        self.actions.append(f"stop:{lima_name}")

    def delete_instance(self, lima_name: str, force: bool = False):
        self.actions.append(f"delete:{lima_name}")


def make_store(tmp_path: Path) -> LeaseStore:
    return LeaseStore(tmp_path / "leases.db")


def test_lease_create_and_update(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    now = to_iso8601(utc_now())

    store.create_lease(
        {
            "instance_id": "inst_abc",
            "backend_name": "lima",
            "profile_name": "bare",
            "status": "running",
            "created_at": now,
            "expires_at": "2099-01-01T00:00:00Z",
            "last_used_at": now,
            "owner_session": "local",
            "ssh_port": 2222,
            "lima_name": "agent-abc",
        }
    )

    ok = store.update_lease("inst_abc", status="destroyed")
    row = store.get_lease("inst_abc")

    assert ok is True
    assert row is not None
    assert row["status"] == "destroyed"


def test_sweeper_expires_and_cleans(tmp_path: Path) -> None:
    cfg = ServerConfig(db_path=tmp_path / "leases.db")
    store = LeaseStore(cfg.db_path)
    backend = CleanupBackend()
    service = LeaseService(store=store, backend=backend, config=cfg)

    store.create_lease(
        {
            "instance_id": "inst_old",
            "backend_name": "lima",
            "profile_name": "bare",
            "status": "running",
            "created_at": "2020-01-01T00:00:00Z",
            "expires_at": "2020-01-01T00:10:00Z",
            "last_used_at": "2020-01-01T00:00:00Z",
            "owner_session": "local",
            "ssh_port": None,
            "lima_name": "agent-old",
        }
    )

    result = service.expire_expired_leases()
    row = store.get_lease("inst_old")

    assert result["expired_count"] == 1
    assert row is not None
    assert row["status"] == "expired"
    assert backend.actions == ["stop:agent-old", "delete:agent-old"]


def test_reconciliation_detects_drift(tmp_path: Path) -> None:
    cfg = ServerConfig(db_path=tmp_path / "leases.db")
    store = LeaseStore(cfg.db_path)
    backend = CleanupBackend()
    service = LeaseService(store=store, backend=backend, config=cfg)

    store.create_lease(
        {
            "instance_id": "inst_live",
            "backend_name": "lima",
            "profile_name": "bare",
            "status": "running",
            "created_at": "2020-01-01T00:00:00Z",
            "expires_at": "2099-01-01T00:00:00Z",
            "last_used_at": "2020-01-01T00:00:00Z",
            "owner_session": "local",
            "ssh_port": None,
            "lima_name": "agent-live",
        }
    )

    listed = service.list_instances(include_expired=False)

    assert len(listed["instances"]) == 1
    assert listed["instances"][0]["reconciliation_drift"] is True


def test_task_registry_create_and_update(tmp_path: Path) -> None:
    cfg = ServerConfig(db_path=tmp_path / "leases.db")
    store = LeaseStore(cfg.db_path)
    now = to_iso8601(utc_now())

    store.create_lease(
        {
            "instance_id": "inst_task",
            "backend_name": "lima",
            "profile_name": "bare",
            "status": "running",
            "created_at": now,
            "expires_at": "2099-01-01T00:00:00Z",
            "last_used_at": now,
            "owner_session": "local",
            "ssh_port": None,
            "lima_name": "agent-task",
        }
    )

    store.create_task(
        {
            "task_id": "task_1",
            "instance_id": "inst_task",
            "command": "echo ok",
            "cwd": "/tmp",
            "env_json": "{}",
            "status": "running",
            "pid": 1234,
            "created_at": now,
            "started_at": now,
            "finished_at": None,
            "exit_code": None,
            "log_path": "/tmp/task.log",
            "exit_code_path": "/tmp/task.exit",
            "error_message": None,
        }
    )
    ok = store.update_task("task_1", status="succeeded", exit_code=0)
    task = store.get_task("task_1")

    assert ok is True
    assert task is not None
    assert task["status"] == "succeeded"
    assert task["exit_code"] == 0


def test_lease_defaults_include_workspace_and_docker_command(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    now = to_iso8601(utc_now())
    store.create_lease(
        {
            "instance_id": "inst_defaults",
            "backend_name": "lima",
            "profile_name": "workspace",
            "status": "running",
            "created_at": now,
            "expires_at": "2099-01-01T00:00:00Z",
            "last_used_at": now,
            "owner_session": "local",
            "ssh_port": None,
            "lima_name": "agent-defaults",
        }
    )

    row = store.get_lease("inst_defaults")
    assert row is not None
    assert row["workspace_root"] is None
    assert row["workspace_id"] is None
    assert row["docker_command"] is None
