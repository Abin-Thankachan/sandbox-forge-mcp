from __future__ import annotations

from datetime import datetime
from pathlib import Path

from lima_mcp_server.backend.lima import CommandResult, VmCreateSpec
from lima_mcp_server.config import ServerConfig
from lima_mcp_server.db import LeaseStore
from lima_mcp_server.service import LeaseService


class ContractBackend:
    available = True
    backend_name = "lima"
    version = "limactl 1.0.0"
    unavailable_reason = ""

    def create_instance(self, backend_instance_name: str, vm_spec: VmCreateSpec):
        return CommandResult(args=[], exit_code=0, stdout="", stderr="", duration_ms=1)

    def start_instance(self, backend_instance_name: str):
        return CommandResult(args=[], exit_code=0, stdout="", stderr="", duration_ms=1)

    def list_instances(self):
        return []

    def extract_ssh_port(self, instance):
        return None

    def shell_command(self, backend_instance_name: str, command: str, timeout_seconds: int):
        if "df -Pk /" in command:
            return CommandResult(args=[], exit_code=0, stdout="10485760\n", stderr="", duration_ms=5)
        if " image inspect " in command:
            return CommandResult(args=[], exit_code=1, stdout="", stderr="Error: No such image: missing", duration_ms=4)
        return CommandResult(args=[], exit_code=0, stdout="docker\n", stderr="", duration_ms=9)

    def copy_to_instance(self, backend_instance_name: str, local_path: str, remote_path: str):
        return CommandResult(args=[], exit_code=0, stdout="", stderr="", duration_ms=1)

    def copy_from_instance(self, backend_instance_name: str, remote_path: str, local_path: str):
        return CommandResult(args=[], exit_code=0, stdout="", stderr="", duration_ms=1)

    def stop_instance(self, backend_instance_name: str, force: bool = False):
        return CommandResult(args=[], exit_code=0, stdout="", stderr="", duration_ms=1)

    def delete_instance(self, backend_instance_name: str, force: bool = False):
        return CommandResult(args=[], exit_code=0, stdout="", stderr="", duration_ms=1)

    def build_shell_command_args(self, backend_instance_name: str, command: str) -> list[str]:
        return ["backend-shell", backend_instance_name, command]


def parse_iso(ts: str) -> None:
    datetime.fromisoformat(ts.replace("Z", "+00:00"))


def make_service(tmp_path: Path) -> LeaseService:
    cfg = ServerConfig(db_path=tmp_path / "leases.db")
    service = LeaseService(LeaseStore(cfg.db_path), ContractBackend(), cfg)
    service._host_cpu_count = lambda: 8  # type: ignore[method-assign]
    service._host_available_memory_gib = lambda: 32.0  # type: ignore[method-assign]
    service._host_free_disk_gib = lambda _workspace_root: 100.0  # type: ignore[method-assign]
    return service


def test_create_contract_keys_and_timestamps(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    payload = service.create_instance(workspace_root=str(tmp_path), ttl_minutes=30, auto_bootstrap=False)

    for key in ["instance_id", "backend", "status", "created_at", "expires_at", "ssh_port", "workspace_root", "workspace_id"]:
        assert key in payload
    parse_iso(payload["created_at"])
    parse_iso(payload["expires_at"])


def test_run_command_contract_keys(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    created = service.create_instance(workspace_root=str(tmp_path), ttl_minutes=30, auto_bootstrap=False)

    payload = service.run_command(created["instance_id"], "echo ok", timeout_seconds=10)
    for key in ["instance_id", "command", "exit_code", "stdout", "stderr", "duration_ms"]:
        assert key in payload


def test_error_shape_for_missing_instance(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    payload = service.run_command("inst_missing", "echo hi")

    assert payload["error_code"] == "INSTANCE_NOT_FOUND"
    assert "message" in payload
    assert "details" in payload


def test_prepare_workspace_contract_keys(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    created = service.create_instance(workspace_root=str(tmp_path), ttl_minutes=30, auto_bootstrap=False)

    payload = service.prepare_workspace(created["instance_id"], include_services=True)

    for key in ["instance_id", "runtime", "runtime_ready", "docker_command", "network", "services"]:
        assert key in payload


def test_docker_build_contract_keys(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    created = service.create_instance(workspace_root=str(tmp_path), ttl_minutes=30, auto_bootstrap=False)
    service.prepare_workspace(created["instance_id"], include_services=False)

    payload = service.docker_build(created["instance_id"], "/workspace", "repo/app:dev")
    for key in ["instance_id", "command", "image_tag", "exit_code", "stdout", "stderr", "duration_ms"]:
        assert key in payload


def test_validate_image_contract_keys(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    created = service.create_instance(workspace_root=str(tmp_path), ttl_minutes=30, auto_bootstrap=False)

    payload = service.validate_image(created["instance_id"], "repo/app:dev", workspace_root=str(tmp_path))
    for key in ["instance_id", "valid", "reasons", "recommendation", "image_metadata", "expected_image_tag"]:
        assert key in payload
