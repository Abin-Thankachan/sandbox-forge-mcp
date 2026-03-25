from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from typing import Any

from .base import BackendCommandError, BackendUnavailableError, CommandResult, VmCreateSpec


def _parse_limactl_list_json(stdout: str) -> list[dict[str, Any]]:
    """Parse `limactl list --format json` output.

    Lima 2.1+ emits one JSON object per line (NDJSON). Older releases emitted a
    single JSON array or object.
    """
    text = stdout.strip()
    if not text:
        return []

    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError:
        instances: list[dict[str, Any]] = []
        errors: list[str] = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"line {line_no}: {exc}")
                continue
            if not isinstance(obj, dict):
                errors.append(f"line {line_no}: expected object, got {type(obj).__name__}")
                continue
            instances.append(obj)
        if errors:
            raise ValueError("; ".join(errors))
        return instances

    if isinstance(parsed, dict):
        if "instances" in parsed and isinstance(parsed["instances"], list):
            return [x for x in parsed["instances"] if isinstance(x, dict)]
        return [parsed]

    if isinstance(parsed, list):
        return [x for x in parsed if isinstance(x, dict)]

    return []


class LimaBackend:
    backend_name = "lima"

    def __init__(self) -> None:
        self.available = False
        self.version = ""
        self.unavailable_reason = "backend not initialized"
        self._preflight()

    def _preflight(self) -> None:
        host_os = sys.platform.lower()
        if not (host_os.startswith("darwin") or host_os.startswith("linux")):
            self.available = False
            self.unavailable_reason = f"unsupported host OS '{host_os}'; only macOS and Linux are supported"
            return

        binary = shutil.which("limactl")
        if not binary:
            self.available = False
            self.unavailable_reason = "limactl not found in PATH"
            return

        try:
            result = self._run(["limactl", "--version"], timeout=5, check=True)
        except BackendCommandError as exc:
            self.available = False
            self.unavailable_reason = exc.stderr.strip() or "failed to execute limactl --version"
            return

        self.available = True
        self.version = (result.stdout or result.stderr).strip()
        self.unavailable_reason = ""

    def _ensure_available(self) -> None:
        if not self.available:
            raise BackendUnavailableError(self.unavailable_reason)

    def _run(self, args: list[str], timeout: int | None = None, check: bool = True) -> CommandResult:
        start = time.perf_counter()
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            raise BackendCommandError(
                command=args,
                exit_code=-1,
                stdout=exc.stdout or "",
                stderr=f"Command timed out after {timeout}s",
                duration_ms=duration_ms,
            ) from exc

        duration_ms = int((time.perf_counter() - start) * 1000)
        result = CommandResult(
            args=args,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_ms=duration_ms,
        )
        if check and result.exit_code != 0:
            raise BackendCommandError(
                command=args,
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=result.duration_ms,
            )
        return result

    def create_instance(self, backend_instance_name: str, vm_spec: VmCreateSpec, timeout_seconds: int = 600) -> CommandResult:
        self._ensure_available()

        args = [
            "limactl",
            "create",
            "--name",
            backend_instance_name,
            "--tty=false",
            f"--cpus={vm_spec.cpus}",
            f"--memory={vm_spec.memory_gib}",
            f"--disk={vm_spec.disk_gib}",
            "--ssh-port=0",
        ]
        if vm_spec.vm_type:
            args.append(f"--vm-type={vm_spec.vm_type}")
        if vm_spec.arch:
            args.append(f"--arch={vm_spec.arch}")
        args.append(vm_spec.template)
        return self._run(args, timeout=timeout_seconds)

    def start_instance(self, backend_instance_name: str, timeout_seconds: int = 600) -> CommandResult:
        self._ensure_available()
        return self._run(["limactl", "start", backend_instance_name], timeout=timeout_seconds)

    def list_instances(self) -> list[dict[str, Any]]:
        self._ensure_available()
        result = self._run(["limactl", "list", "--format", "json"])
        if not result.stdout.strip():
            return []

        try:
            return _parse_limactl_list_json(result.stdout)
        except ValueError as exc:
            raise BackendCommandError(
                command=result.args,
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=result.duration_ms,
                message=f"Failed to parse limactl list JSON: {exc}",
            ) from exc

    def build_shell_command_args(self, backend_instance_name: str, command: str) -> list[str]:
        return ["limactl", "shell", backend_instance_name, "--", "sh", "-lc", command]

    def shell_command(self, backend_instance_name: str, command: str, timeout_seconds: int) -> CommandResult:
        self._ensure_available()
        args = self.build_shell_command_args(backend_instance_name=backend_instance_name, command=command)
        return self._run(args, timeout=timeout_seconds, check=False)

    def copy_to_instance(self, backend_instance_name: str, local_path: str, remote_path: str) -> CommandResult:
        self._ensure_available()
        return self._run(["limactl", "copy", local_path, f"{backend_instance_name}:{remote_path}"])

    def copy_from_instance(self, backend_instance_name: str, remote_path: str, local_path: str) -> CommandResult:
        self._ensure_available()
        return self._run(["limactl", "copy", f"{backend_instance_name}:{remote_path}", local_path])

    def stop_instance(self, backend_instance_name: str, force: bool = False, timeout_seconds: int = 300) -> CommandResult:
        self._ensure_available()
        args = ["limactl", "stop"]
        if force:
            args.append("--force")
        args.append(backend_instance_name)
        return self._run(args, timeout=timeout_seconds)

    def delete_instance(self, backend_instance_name: str, force: bool = False, timeout_seconds: int = 300) -> CommandResult:
        self._ensure_available()
        args = ["limactl", "delete"]
        if force:
            args.append("--force")
        args.append(backend_instance_name)
        return self._run(args, timeout=timeout_seconds)

    @staticmethod
    def extract_ssh_port(instance: dict[str, Any]) -> int | None:
        direct_keys = ("sshLocalPort", "ssh_local_port", "sshPort", "ssh_port")
        for key in direct_keys:
            value = instance.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)

        ssh_obj = instance.get("ssh")
        if isinstance(ssh_obj, dict):
            for key in ("localPort", "port"):
                value = ssh_obj.get(key)
                if isinstance(value, int):
                    return value
                if isinstance(value, str) and value.isdigit():
                    return int(value)

        return None
