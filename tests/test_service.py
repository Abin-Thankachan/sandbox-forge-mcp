from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from lima_mcp_server.backend.lima import BackendCommandError, CommandResult, VmCreateSpec
from lima_mcp_server.config import ServerConfig
from lima_mcp_server.db import LeaseStore
from lima_mcp_server.service import LeaseService
from lima_mcp_server.timeutil import to_iso8601, utc_now


@dataclass
class FakeBackend:
    available: bool = True
    backend_name: str = "lima"
    version: str = "limactl 1.0.0"
    unavailable_reason: str = ""

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.live_rows = []
        self.shell_result = CommandResult(args=[], exit_code=0, stdout="", stderr="", duration_ms=1)

    def create_instance(self, backend_instance_name: str, vm_spec: VmCreateSpec) -> CommandResult:
        self.calls.append(("create", backend_instance_name))
        return CommandResult(args=[], exit_code=0, stdout="", stderr="", duration_ms=1)

    def start_instance(self, backend_instance_name: str) -> CommandResult:
        self.calls.append(("start", backend_instance_name))
        return CommandResult(args=[], exit_code=0, stdout="", stderr="", duration_ms=1)

    def list_instances(self):
        return self.live_rows

    def extract_ssh_port(self, instance):
        return instance.get("sshLocalPort")

    def shell_command(self, backend_instance_name: str, command: str, timeout_seconds: int) -> CommandResult:
        self.calls.append(("shell", backend_instance_name))
        return self.shell_result

    def copy_to_instance(self, backend_instance_name: str, local_path: str, remote_path: str) -> CommandResult:
        self.calls.append(("copy_to", backend_instance_name))
        return CommandResult(args=[], exit_code=0, stdout="", stderr="", duration_ms=1)

    def copy_from_instance(self, backend_instance_name: str, remote_path: str, local_path: str) -> CommandResult:
        self.calls.append(("copy_from", backend_instance_name))
        return CommandResult(args=[], exit_code=0, stdout="", stderr="", duration_ms=1)

    def stop_instance(self, backend_instance_name: str, force: bool = False) -> CommandResult:
        self.calls.append(("stop", backend_instance_name))
        return CommandResult(args=[], exit_code=0, stdout="", stderr="", duration_ms=1)

    def delete_instance(self, backend_instance_name: str, force: bool = False) -> CommandResult:
        self.calls.append(("delete", backend_instance_name))
        return CommandResult(args=[], exit_code=0, stdout="", stderr="", duration_ms=1)

    def build_shell_command_args(self, backend_instance_name: str, command: str) -> list[str]:
        return ["backend-shell", backend_instance_name, command]


def make_service(tmp_path: Path, backend: FakeBackend | None = None) -> LeaseService:
    cfg = ServerConfig(db_path=tmp_path / "leases.db")
    store = LeaseStore(cfg.db_path)
    service = LeaseService(store=store, backend=backend or FakeBackend(), config=cfg)
    service._host_cpu_count = lambda: 8  # type: ignore[method-assign]
    service._host_available_memory_gib = lambda: 32.0  # type: ignore[method-assign]
    service._host_free_disk_gib = lambda _workspace_root: 100.0  # type: ignore[method-assign]
    return service


def make_unstubbed_service(tmp_path: Path, backend: FakeBackend | None = None) -> LeaseService:
    cfg = ServerConfig(db_path=tmp_path / "leases.db")
    return LeaseService(store=LeaseStore(cfg.db_path), backend=backend or FakeBackend(), config=cfg)


def test_create_instance_success_path(tmp_path: Path) -> None:
    backend = FakeBackend()
    service = make_service(tmp_path, backend)

    result = service.create_instance(workspace_root=str(tmp_path), ttl_minutes=30, auto_bootstrap=False)

    assert result["instance_id"].startswith("inst_")
    assert result["status"] == "running"
    assert result["ssh_port"] is None
    assert backend.calls[0][0] == "create"
    assert backend.calls[1][0] == "start"


def test_cap_exceeded(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    now = to_iso8601(utc_now())
    future = to_iso8601(utc_now())

    for i in range(3):
        service.store.create_lease(
            {
                "instance_id": f"inst_{i}",
                "backend_name": "lima",
                "profile_name": "bare",
                "status": "running",
                "created_at": now,
                "expires_at": "2099-01-01T00:00:00Z",
                "last_used_at": future,
                "owner_session": "local",
                "ssh_port": None,
                "backend_instance_name": f"agent-{i}",
            }
        )

    result = service.create_instance(workspace_root=str(tmp_path), ttl_minutes=30, auto_bootstrap=False)
    assert result["error_code"] == "INSTANCE_LIMIT_EXCEEDED"


def test_ttl_rejection(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    too_high = service.create_instance(workspace_root=str(tmp_path), ttl_minutes=999, auto_bootstrap=False)
    too_low = service.create_instance(workspace_root=str(tmp_path), ttl_minutes=0, auto_bootstrap=False)

    assert too_high["error_code"] == "TTL_INVALID"
    assert too_low["error_code"] == "TTL_INVALID"


def test_run_command_exit_propagation(tmp_path: Path) -> None:
    backend = FakeBackend()
    backend.shell_result = CommandResult(args=[], exit_code=42, stdout="out", stderr="err", duration_ms=12)
    service = make_service(tmp_path, backend)

    create = service.create_instance(workspace_root=str(tmp_path), ttl_minutes=30, auto_bootstrap=False)
    result = service.run_command(create["instance_id"], "exit 42")

    assert result["exit_code"] == 42
    assert result["stdout"] == "out"
    assert result["stderr"] == "err"


def test_destroy_orders_stop_then_delete(tmp_path: Path) -> None:
    backend = FakeBackend()
    service = make_service(tmp_path, backend)

    create = service.create_instance(workspace_root=str(tmp_path), ttl_minutes=30, auto_bootstrap=False)
    service.destroy_instance(create["instance_id"])

    ordered = [name for name, _ in backend.calls if name in {"stop", "delete"}]
    assert ordered == ["stop", "delete"]


def test_backend_unavailable_returns_structured_error(tmp_path: Path) -> None:
    backend = FakeBackend(available=False, unavailable_reason="limactl not found in PATH")
    service = make_service(tmp_path, backend)

    result = service.create_instance(workspace_root=str(tmp_path), ttl_minutes=30, auto_bootstrap=False)

    assert result["error_code"] == "BACKEND_UNAVAILABLE"
    assert "details" in result
    assert result["details"]["probable_cause"] == "backend_binary_missing"
    assert result["details"]["next_steps"]


def test_backend_unavailable_on_unsupported_host_returns_guidance(tmp_path: Path) -> None:
    backend = FakeBackend(
        available=False,
        unavailable_reason="unsupported host OS 'win32'; only macOS and Linux are supported",
    )
    service = make_service(tmp_path, backend)

    result = service.create_instance(workspace_root=str(tmp_path), ttl_minutes=30, auto_bootstrap=False)

    assert result["error_code"] == "BACKEND_UNAVAILABLE"
    assert result["details"]["probable_cause"] == "unsupported_host_os"
    assert any("supported" in step for step in result["details"]["next_steps"])


def test_create_instance_dependency_failure_returns_guided_message(tmp_path: Path) -> None:
    backend = FakeBackend()

    def fail_create(backend_instance_name: str, vm_spec: VmCreateSpec) -> CommandResult:  # noqa: ARG001
        raise BackendCommandError(
            command=["limactl", "create", "--name", backend_instance_name],
            exit_code=1,
            stdout="",
            stderr="qemu-system-x86_64: command not found",
            duration_ms=10,
        )

    backend.create_instance = fail_create  # type: ignore[method-assign]
    service = make_service(tmp_path, backend)

    result = service.create_instance(workspace_root=str(tmp_path), ttl_minutes=30, auto_bootstrap=False)

    assert result["error_code"] == "BACKEND_COMMAND_FAILED"
    guidance = result["details"]["guidance"]
    assert guidance["probable_cause"] == "host_vm_dependency_missing"
    assert any("QEMU" in step for step in guidance["next_steps"])


def test_create_instance_blocks_when_host_cpu_is_insufficient(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = FakeBackend()
    service = make_service(tmp_path, backend)
    (tmp_path / ".sandboxforge.toml").write_text("[vm]\ncpus = 2\n")
    monkeypatch.setattr(service, "_host_cpu_count", lambda: 1)

    result = service.create_instance(workspace_root=str(tmp_path), ttl_minutes=30, auto_bootstrap=False)

    assert result["error_code"] == "INSUFFICIENT_HOST_RESOURCES"
    assert "cpu" in result["details"]["failed_checks"]
    assert backend.calls == []


def test_create_instance_blocks_when_host_memory_is_insufficient(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = FakeBackend()
    service = make_service(tmp_path, backend)
    monkeypatch.setattr(service, "_host_available_memory_gib", lambda: 2.2)

    result = service.create_instance(workspace_root=str(tmp_path), ttl_minutes=30, auto_bootstrap=False)

    assert result["error_code"] == "INSUFFICIENT_HOST_RESOURCES"
    assert "memory" in result["details"]["failed_checks"]
    assert result["details"]["required_headroom"]["minimum_available_memory_gib"] == 2.5
    assert backend.calls == []


def test_create_instance_blocks_when_host_disk_is_insufficient(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = FakeBackend()
    service = make_service(tmp_path, backend)
    monkeypatch.setattr(service, "_host_free_disk_gib", lambda _workspace_root: 16.0)

    result = service.create_instance(workspace_root=str(tmp_path), ttl_minutes=30, auto_bootstrap=False)

    assert result["error_code"] == "INSUFFICIENT_HOST_RESOURCES"
    assert "disk" in result["details"]["failed_checks"]
    assert result["details"]["required_headroom"]["minimum_free_disk_gib"] == 17.0
    assert backend.calls == []


def test_memory_probe_linux_uses_memavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = make_unstubbed_service(tmp_path)
    monkeypatch.setattr(service, "_host_platform", lambda: "linux")
    monkeypatch.setattr(
        service,
        "_read_linux_meminfo",
        lambda: "MemTotal: 8000000 kB\nMemAvailable: 3145728 kB\nMemFree: 1024 kB\n",
    )

    value = service._host_available_memory_gib()
    assert value == pytest.approx(3.0, rel=1e-6)


def test_memory_probe_darwin_uses_vm_stat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = make_unstubbed_service(tmp_path)
    monkeypatch.setattr(service, "_host_platform", lambda: "darwin")

    def fake_run(args: list[str], cwd: str | None = None, timeout_seconds: int = 10) -> tuple[int, str, str]:  # noqa: ARG001
        output = (
            "Mach Virtual Memory Statistics: (page size of 4096 bytes)\n"
            "Pages free: 131072.\n"
            "Pages inactive: 131072.\n"
            "Pages speculative: 0.\n"
        )
        return 0, output, ""

    monkeypatch.setattr(service, "_run_local_command", fake_run)

    value = service._host_available_memory_gib()
    assert value == pytest.approx(1.0, rel=1e-6)


def test_memory_probe_windows_uses_freephysicalmemory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = make_unstubbed_service(tmp_path)
    monkeypatch.setattr(service, "_host_platform", lambda: "win32")
    monkeypatch.setattr(service, "_powershell_binary", lambda: "powershell.exe")

    def fake_run(args: list[str], cwd: str | None = None, timeout_seconds: int = 10) -> tuple[int, str, str]:  # noqa: ARG001
        return 0, "2097152\n", ""

    monkeypatch.setattr(service, "_run_local_command", fake_run)

    value = service._host_available_memory_gib()
    assert value == pytest.approx(2.0, rel=1e-6)
