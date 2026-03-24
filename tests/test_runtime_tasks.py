from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from lima_mcp_server.backend.lima import CommandResult
from lima_mcp_server.config import ServerConfig
from lima_mcp_server.db import LeaseStore
from lima_mcp_server.runtime import DockerRuntimeAdapter
from lima_mcp_server.service import LeaseService
from lima_mcp_server.timeutil import to_iso8601, utc_now


@dataclass
class LocalProc:
    pid: int


class BackendStub:
    available = True
    backend_name = "lima"
    version = "limactl 2.1.0"
    unavailable_reason = ""

    def __init__(self) -> None:
        self.last_shell: str | None = None
        self.shell_result = CommandResult(args=[], exit_code=0, stdout="ok\n", stderr="", duration_ms=10)
        self.copy_from_calls: list[tuple[str, str, str]] = []
        self.image_labels_output: str | None = None
        self.image_created_output: str = "2026-03-24T10:00:00Z"

    def shell_command(self, lima_name: str, command: str, timeout_seconds: int) -> CommandResult:
        self.last_shell = command
        if "df -Pk /" in command:
            return CommandResult(args=[], exit_code=0, stdout="10485760\n", stderr="", duration_ms=4)
        if " image inspect " in command:
            if self.image_labels_output is None:
                return CommandResult(args=[], exit_code=1, stdout="", stderr="Error: No such image: missing", duration_ms=4)
            if ".Config.Labels" in command:
                return CommandResult(args=[], exit_code=0, stdout=f"{self.image_labels_output}\n", stderr="", duration_ms=4)
            if ".Created" in command:
                return CommandResult(args=[], exit_code=0, stdout=f"{self.image_created_output}\n", stderr="", duration_ms=4)
        return self.shell_result

    def copy_from_instance(self, lima_name: str, remote_path: str, local_path: str) -> CommandResult:
        self.copy_from_calls.append((lima_name, remote_path, local_path))
        return CommandResult(args=[], exit_code=0, stdout="", stderr="", duration_ms=1)

    def copy_to_instance(self, lima_name: str, local_path: str, remote_path: str) -> CommandResult:
        return CommandResult(args=[], exit_code=0, stdout="", stderr="", duration_ms=1)

    def stop_instance(self, lima_name: str, force: bool = False) -> CommandResult:
        return CommandResult(args=[], exit_code=0, stdout="", stderr="", duration_ms=1)

    def delete_instance(self, lima_name: str, force: bool = False) -> CommandResult:
        return CommandResult(args=[], exit_code=0, stdout="", stderr="", duration_ms=1)

    def list_instances(self):
        return []


def make_service(tmp_path: Path) -> tuple[LeaseService, BackendStub]:
    cfg = ServerConfig(db_path=tmp_path / "leases.db")
    store = LeaseStore(cfg.db_path)
    backend = BackendStub()
    service = LeaseService(store=store, backend=backend, config=cfg)

    store.create_lease(
        {
            "instance_id": "inst_run",
            "backend_name": "lima",
            "profile_name": "workspace",
            "status": "running",
            "created_at": to_iso8601(utc_now()),
            "expires_at": "2099-01-01T00:00:00Z",
            "last_used_at": to_iso8601(utc_now()),
            "owner_session": "local",
            "ssh_port": 2222,
            "lima_name": "agent-run",
            "workspace_root": str(tmp_path),
            "workspace_id": "ws_test",
            "runtime_name": "docker",
            "runtime_ready": 1,
            "docker_command": "docker",
        }
    )
    return service, backend


def test_runtime_adapter_builds_expected_commands() -> None:
    adapter = DockerRuntimeAdapter()

    build = adapter.docker_build_command(
        "/ws",
        "img:tag",
        docker_command="docker",
        dockerfile="Dockerfile",
        build_args={"A": "1"},
        labels={"git.commit": "abc"},
        target="prod",
    )
    run = adapter.docker_run_command("img:tag", docker_command="docker", command="pytest -q", name="runner", detach=True, privileged=True)
    compose = adapter.docker_compose_command("up", docker_command="docker", file="docker-compose.yml", services=["api"], detach=True)

    assert "docker build" in build
    assert "--build-arg A=1" in build
    assert "--label git.commit=abc" in build
    assert "--target prod" in build
    assert "docker run -d --name runner --privileged img:tag" in run
    assert "docker compose -f docker-compose.yml up -d api" in compose


def test_runtime_adapter_supports_compose_logs_follow_and_exec() -> None:
    adapter = DockerRuntimeAdapter()

    logs = adapter.docker_compose_command(
        "logs",
        docker_command="docker",
        file="docker-compose.yml",
        services=["api"],
        follow=True,
        since="2m",
        tail=50,
    )
    exec_cmd = adapter.docker_compose_command(
        "exec",
        docker_command="docker",
        file="docker-compose.yml",
        services=["api"],
        command="pytest -q",
    )

    assert "docker compose -f docker-compose.yml logs --follow --since 2m --tail 50 api" in logs
    assert "docker compose -f docker-compose.yml exec -T api sh -lc 'pytest -q'" in exec_cmd


def test_docker_build_returns_structured_payload(tmp_path: Path) -> None:
    service, backend = make_service(tmp_path)

    payload = service.docker_build("inst_run", "/workspace", "repo/app:dev")

    assert payload["exit_code"] == 0
    assert "docker build" in payload["command"]
    assert backend.last_shell is not None
    assert "GIT_COMMIT=unknown" in payload["command"]
    assert "--label git.commit=" in payload["command"]


def test_validate_image_returns_mismatch_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, backend = make_service(tmp_path)
    backend.image_labels_output = '{"git.commit":"abc123","build.dependencies.hash":"deadbeef"}'
    backend.image_created_output = "2026-03-24T10:00:00Z"
    monkeypatch.setattr(
        service,
        "_workspace_state",
        lambda workspace_root, settings: {
            "git_commit": "def456",
            "git_short": "def456",
            "git_branch": "main",
            "git_dirty": False,
            "dependencies_hash": "a1b2c3",
            "dockerfile_hash": "x",
            "content_hash": "y",
            "build_timestamp": "2026-03-24T12:00:00Z",
        },
    )

    payload = service.validate_image(
        instance_id="inst_run",
        image_name="repo/app:dev",
        workspace_root=str(tmp_path),
        checks={"git_commit": True, "dependencies": True, "max_age_hours": 24},
    )

    assert payload["valid"] is False
    assert any(reason.startswith("git_commit_mismatch") for reason in payload["reasons"])


def test_docker_build_uses_cached_image_when_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, backend = make_service(tmp_path)
    backend.image_labels_output = '{"git.commit":"def456","build.dependencies.hash":"a1b2c3"}'
    backend.image_created_output = "2026-03-24T10:00:00Z"
    monkeypatch.setattr(
        service,
        "_workspace_state",
        lambda workspace_root, settings: {
            "git_commit": "def456",
            "git_short": "def456",
            "git_branch": "main",
            "git_dirty": False,
            "dependencies_hash": "a1b2c3",
            "dockerfile_hash": "x",
            "content_hash": "y",
            "build_timestamp": "2026-03-24T12:00:00Z",
        },
    )
    (tmp_path / ".lima-mcp.toml").write_text(
        "\n".join(
            [
                "[build.image_caching]",
                "tag_format = \"{image}:dev\"",
            ]
        )
    )

    payload = service.docker_build("inst_run", "/workspace", "repo/app:dev")

    assert payload["cache_hit"] is True
    assert payload["cached_image_tag"] == "repo/app:dev"


def test_docker_exec_maps_missing_container_error(tmp_path: Path) -> None:
    service, backend = make_service(tmp_path)
    backend.shell_result = CommandResult(
        args=[],
        exit_code=1,
        stdout="",
        stderr="Error response from daemon: No such container: nope",
        duration_ms=5,
    )

    payload = service.docker_exec("inst_run", "nope", "echo hi")

    assert payload["error_code"] == "CONTAINER_NOT_FOUND"


def test_docker_compose_exec_requires_command(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)

    payload = service.docker_compose("inst_run", "/workspace", "exec", services=["api"])

    assert payload["error_code"] == "DOCKER_COMMAND_FAILED"


def test_docker_compose_injects_connection_env(tmp_path: Path) -> None:
    service, backend = make_service(tmp_path)

    payload = service.docker_compose("inst_run", "/workspace", "ps")

    assert "DB_HOST" in payload["connection_env"]
    assert "REDIS_URL" in payload["connection_env"]
    assert backend.last_shell is not None
    assert "export DB_HOST=" in backend.last_shell


def test_extend_instance_ttl_updates_expiry(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)

    payload = service.extend_instance_ttl("inst_run", 15)

    assert payload["instance_id"] == "inst_run"
    assert payload["ttl_extended_minutes"] == 15


def test_collect_artifacts_constructs_copy_calls(tmp_path: Path) -> None:
    service, backend = make_service(tmp_path)
    dest = tmp_path / "out"

    payload = service.collect_artifacts("inst_run", ["/tmp/a.txt", "/tmp/b.txt"], str(dest))

    assert len(payload["artifacts"]) == 2
    assert len(backend.copy_from_calls) == 2
    assert backend.copy_from_calls[0][1] == "/tmp/a.txt"


def test_background_task_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _ = make_service(tmp_path)

    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: LocalProc(pid=4242))
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)

    started = service.start_background_task("inst_run", "echo hello", cwd="/tmp", env={"A": "1"})
    assert started["status"] == "running"

    status = service.get_task_status(started["task_id"])
    assert status["status"] == "running"

    exit_path = Path(str(service.store.get_task(started["task_id"])["exit_code_path"]))
    exit_path.write_text("0")

    done = service.get_task_status(started["task_id"])
    assert done["status"] == "succeeded"

    already_done = service.stop_task(started["task_id"], force=False)
    assert already_done["error_code"] == "TASK_ALREADY_FINISHED"


def test_stop_running_task_marks_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _ = make_service(tmp_path)

    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: LocalProc(pid=5151))
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)

    started = service.start_background_task("inst_run", "sleep 120")
    stopped = service.stop_task(started["task_id"], force=True)

    assert stopped["status"] == "stopped"
    assert stopped["exit_code"] == -9
