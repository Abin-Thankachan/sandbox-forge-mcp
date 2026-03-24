from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class CommandResult:
    args: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


class BackendUnavailableError(RuntimeError):
    pass


class BackendCommandError(RuntimeError):
    def __init__(
        self,
        command: list[str],
        exit_code: int,
        stdout: str,
        stderr: str,
        duration_ms: int,
        message: str = "Backend command failed",
    ) -> None:
        super().__init__(message)
        self.command = command
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.duration_ms = duration_ms

    def details(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class VmCreateSpec:
    cpus: int
    memory_gib: float
    disk_gib: float
    template: str
    arch: str | None = None
    vm_type: str | None = None


class Backend(Protocol):
    backend_name: str
    available: bool
    version: str
    unavailable_reason: str

    def create_instance(self, backend_instance_name: str, vm_spec: VmCreateSpec, timeout_seconds: int = 600) -> CommandResult: ...

    def start_instance(self, backend_instance_name: str, timeout_seconds: int = 600) -> CommandResult: ...

    def list_instances(self) -> list[dict[str, Any]]: ...

    def shell_command(self, backend_instance_name: str, command: str, timeout_seconds: int) -> CommandResult: ...

    def copy_to_instance(self, backend_instance_name: str, local_path: str, remote_path: str) -> CommandResult: ...

    def copy_from_instance(self, backend_instance_name: str, remote_path: str, local_path: str) -> CommandResult: ...

    def stop_instance(self, backend_instance_name: str, force: bool = False, timeout_seconds: int = 300) -> CommandResult: ...

    def delete_instance(self, backend_instance_name: str, force: bool = False, timeout_seconds: int = 300) -> CommandResult: ...

    def extract_ssh_port(self, instance: dict[str, Any]) -> int | None: ...

    def build_shell_command_args(self, backend_instance_name: str, command: str) -> list[str]: ...
