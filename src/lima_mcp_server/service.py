from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .backend import (
    BackendCommandError,
    BackendUnavailableError,
    CommandResult,
    Backend,
    VmCreateSpec,
)
from .config import ServerConfig
from .db import LeaseStore
from .errors import error_response
from .runtime import DockerRuntimeAdapter
from .timeutil import parse_iso8601, to_iso8601, utc_now
from .workspace_config import WorkspaceConfigError, WorkspaceSettings, resolve_workspace_settings

ACTIVE_STATUSES = ("creating", "running")
TERMINAL_STATUSES = {"destroyed", "expired"}
DOCKER_ALLOWED_COMPOSE_ACTIONS = {"up", "down", "ps", "logs", "pull", "build", "restart", "stop", "exec"}
TASK_TERMINAL_STATUSES = {"succeeded", "failed", "stopped"}
DEFAULT_SERVICE_READY_TIMEOUT_SECONDS = 90
DEFAULT_SERVICE_READY_POLL_SECONDS = 2
DEFAULT_HOST_MEMORY_HEADROOM_GIB = 0.5
DEFAULT_HOST_DISK_HEADROOM_GIB = 2.0


class LeaseService:
    def __init__(self, store: LeaseStore, backend: Backend, config: ServerConfig) -> None:
        self.store = store
        self.backend = backend
        self.config = config
        self.docker = DockerRuntimeAdapter()

    def _resolve_workspace_settings(
        self,
        workspace_root: str,
        overrides: dict[str, Any] | None = None,
    ) -> tuple[WorkspaceSettings | None, dict[str, Any] | None]:
        try:
            settings = resolve_workspace_settings(workspace_root=workspace_root, overrides=overrides)
            return settings, None
        except WorkspaceConfigError as exc:
            return None, error_response(
                "WORKSPACE_CONFIG_INVALID",
                "Workspace configuration is invalid",
                {
                    "workspace_root": workspace_root,
                    "errors": exc.errors,
                },
            )

    def validate_workspace_config(
        self,
        workspace_root: str,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        settings, error = self._resolve_workspace_settings(workspace_root=workspace_root, overrides=overrides)
        if error is not None or settings is None:
            return error or error_response(
                "WORKSPACE_CONFIG_INVALID",
                "Workspace configuration is invalid",
                {"workspace_root": workspace_root},
            )

        return {
            "workspace_root": settings.workspace_root,
            "workspace_id": settings.workspace_id,
            "sources": settings.sources,
            "config": settings.to_dict(),
            "valid": True,
            "diagnostics": [],
        }

    def validate_image(
        self,
        instance_id: str,
        image_name: str,
        workspace_root: str | None = None,
        checks: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        lease, error = self._get_lease_for_action(instance_id, require_workspace=True)
        if error is not None or lease is None:
            return error or error_response("INSTANCE_NOT_FOUND", "Instance not found", {"instance_id": instance_id})

        target_workspace = workspace_root or str(lease.get("workspace_root") or "")
        settings, config_error = self._resolve_workspace_settings(workspace_root=target_workspace)
        if config_error is not None or settings is None:
            return config_error or error_response(
                "WORKSPACE_CONFIG_INVALID",
                "Workspace configuration is invalid",
                {"workspace_root": target_workspace},
            )

        validation, validation_error = self._validate_image_internal(
            lease=lease,
            workspace_root=settings.workspace_root,
            image_name=image_name,
            settings=settings,
            checks=checks,
        )
        if validation_error is not None:
            return validation_error

        expected_tag = self._expected_cached_image_tag(
            image_name=image_name,
            settings=settings,
            workspace_state=validation.get("workspace_state", {}),
        )
        validation["instance_id"] = instance_id
        validation["expected_image_tag"] = expected_tag
        return validation

    def _backend_unavailable(self) -> dict[str, Any]:
        backend_name = str(self.backend.backend_name or "unknown")
        reason = str(self.backend.unavailable_reason or "").strip()
        reason_lower = reason.lower()
        probable_cause = "backend_unavailable"
        next_steps = [
            "Verify virtualization backend prerequisites are installed and available.",
            "Review the backend reason and retry after fixing host prerequisites.",
        ]
        if "unsupported host os" in reason_lower:
            probable_cause = "unsupported_host_os"
            next_steps = [
                "Run this server on a host OS supported by your configured backend.",
                "Use SANDBOX_BACKEND=auto to select a supported backend for this host.",
            ]
        elif backend_name == "lima" and "limactl not found" in reason_lower:
            probable_cause = "backend_binary_missing"
            next_steps = [
                "Install Lima and ensure `limactl` is in PATH.",
                "Verify with: `limactl --version`.",
                "Restart the server after PATH updates, then retry.",
            ]
        elif backend_name == "lima" and "failed to execute limactl --version" in reason_lower:
            probable_cause = "backend_binary_not_executable"
            next_steps = [
                "Verify `limactl` runs successfully from this shell: `limactl --version`.",
                "Check host permissions and backend installation health, then retry.",
            ]
        elif backend_name == "hyperv":
            probable_cause = "hyperv_unavailable"
            next_steps = [
                "Ensure Hyper-V PowerShell commands are available (`Get-Command New-VM`).",
                "Ensure HYPERV_BASE_VHDX points to an existing base image.",
                "Ensure OpenSSH client tools (`ssh`, `scp`) are available in PATH.",
            ]

        return error_response(
            "BACKEND_UNAVAILABLE",
            "Requested virtualization backend is unavailable",
            {
                "backend": {
                    "name": backend_name,
                    "reason": reason,
                },
                "reason": reason,
                "probable_cause": probable_cause,
                "next_steps": next_steps,
            },
        )

    def _instance_creation_failure_guidance(self, exc: BackendCommandError) -> dict[str, Any]:
        combined = f"{exc.stderr}\n{exc.stdout}".lower()
        backend_name = str(self.backend.backend_name or "").lower()

        if backend_name == "lima" and "qemu" in combined and "not found" in combined:
            return {
                "probable_cause": "host_vm_dependency_missing",
                "next_steps": [
                    "Install Linux host virtualization dependencies required by Lima (including QEMU tooling).",
                    "Confirm host tooling is available, then retry `create_instance`.",
                ],
            }
        if backend_name == "lima" and (
            "/dev/kvm" in combined or ("kvm" in combined and ("not available" in combined or "permission denied" in combined))
        ):
            return {
                "probable_cause": "kvm_unavailable_or_permission_denied",
                "next_steps": [
                    "Ensure KVM is available and your user has permission to access `/dev/kvm`.",
                    "Retry after fixing virtualization permissions/capabilities.",
                ],
            }
        if backend_name == "lima" and "vm-type" in combined and "vz" in combined and "linux" in combined:
            return {
                "probable_cause": "unsupported_vm_type_for_host",
                "next_steps": [
                    "Set `vm.vm_type = \"qemu\"` for Linux hosts (or remove override to use host default).",
                    "Retry `create_instance` after updating workspace config.",
                ],
            }
        if backend_name == "hyperv" and ("new-vm" in combined or "hyper-v" in combined):
            return {
                "probable_cause": "hyperv_prerequisites_missing",
                "next_steps": [
                    "Ensure Hyper-V is enabled and PowerShell cmdlets are available.",
                    "Ensure `HYPERV_BASE_VHDX` exists and is accessible.",
                    "Retry `create_instance` after fixing host prerequisites.",
                ],
            }
        return {
            "probable_cause": "instance_create_failed",
            "next_steps": [
                "Inspect `details.stderr` for backend-specific dependency errors.",
                "Verify backend prerequisites and virtualization dependencies, then retry.",
            ],
        }

    def _invalid_ttl(self, ttl_minutes: int | None) -> dict[str, Any]:
        return error_response(
            "TTL_INVALID",
            "Invalid ttl_minutes",
            {
                "ttl_minutes": ttl_minutes,
                "default_ttl_minutes": self.config.default_ttl_minutes,
                "max_ttl_minutes": self.config.max_ttl_minutes,
            },
        )

    def _new_identifiers(self) -> tuple[str, str]:
        shortid = uuid.uuid4().hex[:8]
        return f"inst_{shortid}", f"agent-{shortid}"

    def _new_task_id(self) -> str:
        return f"task_{uuid.uuid4().hex[:12]}"

    def _is_expired(self, lease: dict[str, Any]) -> bool:
        return parse_iso8601(str(lease["expires_at"])) <= utc_now()

    def _get_live_map(self) -> dict[str, dict[str, Any]]:
        live_entries = self.backend.list_instances()
        live_map: dict[str, dict[str, Any]] = {}
        for row in live_entries:
            name = str(row.get("name") or row.get("instance") or row.get("backend_instance_name") or "")
            if name:
                live_map[name] = row
        return live_map

    def _normalize_lease_status(self, lease: dict[str, Any]) -> dict[str, Any]:
        if lease.get("status") not in TERMINAL_STATUSES and self._is_expired(lease):
            self.store.update_lease(lease["instance_id"], status="expired", last_used_at=to_iso8601(utc_now()))
            lease["status"] = "expired"
        return lease

    def _build_vm_command(self, command: str, cwd: str | None = None, env: dict[str, str] | None = None) -> str:
        prefix: list[str] = []
        if cwd:
            prefix.append(f"cd {shlex.quote(cwd)}")
        if env:
            for key, value in env.items():
                prefix.append(f"export {key}={shlex.quote(str(value))}")
        prefix.append(command)
        return " && ".join(prefix)

    def _exec_in_instance(
        self,
        lease: dict[str, Any],
        command: str,
        timeout_seconds: int = 600,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        vm_command = self._build_vm_command(command=command, cwd=cwd, env=env)
        result = self.backend.shell_command(
            backend_instance_name=str(lease["backend_instance_name"]),
            command=vm_command,
            timeout_seconds=timeout_seconds,
        )
        self.store.update_lease(lease["instance_id"], last_used_at=to_iso8601(utc_now()))
        return result

    def _command_details(self, command: str, result: CommandResult) -> dict[str, Any]:
        return {
            "command": command,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
        }

    def _docker_command_failed(self, command: str, result: CommandResult) -> dict[str, Any]:
        stderr_lower = result.stderr.lower()
        if "no such container" in stderr_lower or "is not running" in stderr_lower:
            return error_response(
                "CONTAINER_NOT_FOUND",
                "Container was not found",
                self._command_details(command, result),
            )
        return error_response(
            "DOCKER_COMMAND_FAILED",
            "Docker command failed",
            self._command_details(command, result),
        )

    def _detect_docker_command(self, lease: dict[str, Any]) -> tuple[str | None, CommandResult]:
        detect_cmd = (
            "if docker info >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then "
            "echo docker; "
            "elif command -v sudo >/dev/null 2>&1 "
            "&& sudo -n docker info >/dev/null 2>&1 "
            "&& sudo -n docker compose version >/dev/null 2>&1; then "
            'echo "sudo -n docker"; '
            "else exit 1; fi"
        )
        result = self._exec_in_instance(lease=lease, command=detect_cmd, timeout_seconds=120)
        if result.exit_code != 0:
            return None, result

        for line in result.stdout.splitlines():
            normalized = line.strip()
            if normalized in {"docker", "sudo -n docker"}:
                return normalized, result
        return None, result

    def _load_workspace_settings_for_lease(
        self,
        lease: dict[str, Any],
    ) -> tuple[WorkspaceSettings | None, dict[str, Any] | None]:
        workspace_root = str(lease.get("workspace_root") or "")
        if not workspace_root:
            return None, error_response(
                "WORKSPACE_BINDING_REQUIRED",
                "Instance is not bound to a workspace",
                {"instance_id": lease.get("instance_id")},
            )
        return self._resolve_workspace_settings(workspace_root=workspace_root)

    def _docker_command_for_lease(self, lease: dict[str, Any]) -> str:
        value = str(lease.get("docker_command") or "").strip()
        return value or "docker"

    def _ensure_runtime_ready(self, lease: dict[str, Any], runtime: str = "docker") -> dict[str, Any] | None:
        runtime_name = str(lease.get("runtime_name") or "")
        runtime_ready = bool(lease.get("runtime_ready"))
        docker_command = str(lease.get("docker_command") or "").strip()
        if runtime == "docker" and runtime_ready and runtime_name == "docker" and docker_command:
            return None

        resolved, result = self._detect_docker_command(lease)
        if resolved:
            self.store.update_lease(
                lease["instance_id"],
                runtime_name="docker",
                runtime_ready=1,
                docker_command=resolved,
                last_used_at=to_iso8601(utc_now()),
            )
            lease["runtime_name"] = "docker"
            lease["runtime_ready"] = 1
            lease["docker_command"] = resolved
            return None

        return error_response(
            "RUNTIME_NOT_READY",
            "Requested runtime is not prepared for this instance",
            self._command_details("docker readiness probe", result),
        )

    def _task_dir(self) -> Path:
        path = self.config.db_path.parent / "tasks"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _run_local_command(
        self,
        args: list[str],
        cwd: str | None = None,
        timeout_seconds: int = 10,
    ) -> tuple[int, str, str]:
        try:
            completed = subprocess.run(
                args,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            return completed.returncode, completed.stdout, completed.stderr
        except Exception as exc:
            return 1, "", str(exc)

    def _host_platform(self) -> str:
        return sys.platform.lower()

    def _host_cpu_count(self) -> int | None:
        count = os.cpu_count()
        if isinstance(count, int) and count > 0:
            return count
        return None

    def _read_linux_meminfo(self) -> str | None:
        try:
            return Path("/proc/meminfo").read_text(encoding="utf-8")
        except OSError:
            return None

    def _parse_linux_available_memory_gib(self, content: str) -> float | None:
        for key in ("MemAvailable", "MemFree"):
            match = re.search(rf"^{key}:\s+(\d+)\s+kB$", content, flags=re.MULTILINE)
            if match:
                kib = int(match.group(1))
                return kib / (1024 * 1024)
        return None

    def _parse_darwin_vm_stat_available_memory_gib(self, content: str) -> float | None:
        page_size_match = re.search(r"page size of (\d+) bytes", content)
        if not page_size_match:
            return None

        page_size = int(page_size_match.group(1))
        total_pages = 0
        for label in ("Pages free", "Pages inactive", "Pages speculative"):
            stat_match = re.search(rf"^{label}:\s+(\d+)\.", content, flags=re.MULTILINE)
            if not stat_match:
                continue
            total_pages += int(stat_match.group(1))

        if total_pages <= 0:
            return None
        return (total_pages * page_size) / (1024**3)

    def _powershell_binary(self) -> str | None:
        for candidate in ("powershell.exe", "powershell", "pwsh"):
            binary = shutil.which(candidate)
            if binary:
                return binary
        return None

    def _host_available_memory_gib(self) -> float | None:
        platform_name = self._host_platform()
        if platform_name.startswith("linux"):
            meminfo = self._read_linux_meminfo()
            if not meminfo:
                return None
            return self._parse_linux_available_memory_gib(meminfo)

        if platform_name.startswith("darwin"):
            rc, out, _ = self._run_local_command(["vm_stat"], timeout_seconds=5)
            if rc == 0:
                parsed = self._parse_darwin_vm_stat_available_memory_gib(out)
                if parsed is not None:
                    return parsed
            return None

        if platform_name.startswith("win"):
            powershell = self._powershell_binary()
            if not powershell:
                return None
            cmd = "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"
            rc, out, _ = self._run_local_command([powershell, "-NoProfile", "-NonInteractive", "-Command", cmd], timeout_seconds=8)
            if rc != 0:
                return None
            match = re.search(r"(\d+)", out)
            if not match:
                return None
            kib = int(match.group(1))
            return kib / (1024 * 1024)

        return None

    def _host_free_disk_gib(self, workspace_root: str) -> float | None:
        try:
            usage = shutil.disk_usage(workspace_root)
            return usage.free / (1024**3)
        except OSError:
            return None

    def _ensure_host_capacity_for_vm(self, settings: WorkspaceSettings) -> dict[str, Any] | None:
        cpu_count = self._host_cpu_count()
        memory_available_gib = self._host_available_memory_gib()
        disk_free_gib = self._host_free_disk_gib(settings.workspace_root)

        failed_checks: list[str] = []
        required_memory_gib = settings.vm.memory_gib + DEFAULT_HOST_MEMORY_HEADROOM_GIB
        required_disk_gib = settings.vm.disk_gib + DEFAULT_HOST_DISK_HEADROOM_GIB

        if cpu_count is not None and settings.vm.cpus > cpu_count:
            failed_checks.append("cpu")

        if memory_available_gib is not None and memory_available_gib < required_memory_gib:
            failed_checks.append("memory")

        if disk_free_gib is not None and disk_free_gib < required_disk_gib:
            failed_checks.append("disk")

        if not failed_checks:
            return None

        suggested_memory_gib = settings.vm.memory_gib
        if memory_available_gib is not None:
            suggested_memory_gib = max(0.5, round(max(memory_available_gib - DEFAULT_HOST_MEMORY_HEADROOM_GIB, 0.5), 2))

        suggested_disk_gib = settings.vm.disk_gib
        if disk_free_gib is not None:
            suggested_disk_gib = max(5.0, round(max(disk_free_gib - DEFAULT_HOST_DISK_HEADROOM_GIB, 5.0), 1))

        suggested_cpus = settings.vm.cpus
        if cpu_count is not None:
            suggested_cpus = max(1, min(settings.vm.cpus, cpu_count))

        config_path = Path(settings.workspace_root) / ".sandboxforge.toml"
        return error_response(
            "INSUFFICIENT_HOST_RESOURCES",
            "Host capacity is too low for the requested VM shape",
            {
                "host_os": self._host_platform(),
                "workspace_root": settings.workspace_root,
                "workspace_config": str(config_path),
                "requested_vm": {
                    "cpus": settings.vm.cpus,
                    "memory_gib": settings.vm.memory_gib,
                    "disk_gib": settings.vm.disk_gib,
                },
                "available_host": {
                    "cpu_count": cpu_count,
                    "memory_available_gib": round(memory_available_gib, 3) if memory_available_gib is not None else None,
                    "disk_free_gib": round(disk_free_gib, 3) if disk_free_gib is not None else None,
                },
                "required_headroom": {
                    "minimum_available_memory_gib": round(required_memory_gib, 3),
                    "minimum_free_disk_gib": round(required_disk_gib, 3),
                },
                "failed_checks": failed_checks,
                "suggested_vm": {
                    "cpus": suggested_cpus,
                    "memory_gib": suggested_memory_gib,
                    "disk_gib": suggested_disk_gib,
                },
                "next_steps": [
                    f"Reduce vm.cpus, vm.memory_gib, or vm.disk_gib in {config_path}",
                    "Close memory-heavy host applications and retry create_instance",
                    "Retry with auto_bootstrap=false and include_services=false to reduce startup overhead",
                ],
            },
        )

    def _hash_workspace_patterns(self, workspace_root: str, patterns: list[str]) -> str | None:
        root = Path(workspace_root)
        entries: list[tuple[str, str]] = []
        for pattern in patterns:
            for path in sorted(root.glob(pattern)):
                if not path.is_file():
                    continue
                rel = path.relative_to(root).as_posix()
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                entries.append((rel, digest))
        if not entries:
            return None

        payload = "\n".join(f"{name}:{digest}" for name, digest in entries).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _workspace_state(self, workspace_root: str, settings: WorkspaceSettings) -> dict[str, Any]:
        git_commit: str | None = None
        git_short: str | None = None
        git_branch: str | None = None
        git_dirty = False

        rc, out, _ = self._run_local_command(["git", "-C", workspace_root, "rev-parse", "HEAD"], timeout_seconds=10)
        if rc == 0:
            git_commit = out.strip() or None
            if git_commit:
                git_short = git_commit[:8]

        rc, out, _ = self._run_local_command(
            ["git", "-C", workspace_root, "rev-parse", "--abbrev-ref", "HEAD"],
            timeout_seconds=10,
        )
        if rc == 0:
            git_branch = out.strip() or None

        rc, out, _ = self._run_local_command(
            ["git", "-C", workspace_root, "status", "--porcelain"],
            timeout_seconds=10,
        )
        if rc == 0:
            git_dirty = bool(out.strip())

        dependency_patterns = list(settings.build.staleness_detection.check_dependencies)
        deps_hash = self._hash_workspace_patterns(workspace_root, dependency_patterns)

        dockerfile_hash: str | None = None
        if settings.build.staleness_detection.check_dockerfile:
            dockerfile_hash = self._hash_workspace_patterns(workspace_root, ["Dockerfile"])

        if settings.build.image_caching.strategy == "content_hash":
            tracked_hash: str | None = None
            rc, out, _ = self._run_local_command(
                ["git", "-C", workspace_root, "ls-files", "-z"],
                timeout_seconds=20,
            )
            if rc == 0:
                files = [item for item in out.split("\0") if item]
                entries: list[tuple[str, str]] = []
                for item in files:
                    path = Path(workspace_root) / item
                    if path.is_file():
                        entries.append((item, hashlib.sha256(path.read_bytes()).hexdigest()))
                if entries:
                    payload = "\n".join(f"{name}:{digest}" for name, digest in sorted(entries)).encode("utf-8")
                    tracked_hash = hashlib.sha256(payload).hexdigest()

            parts = [
                git_commit or "nogit",
                "dirty" if git_dirty else "clean",
                deps_hash or "nodeps",
                dockerfile_hash or "nodockerfile",
                tracked_hash or "notracked",
            ]
            content_hash = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
        elif settings.build.image_caching.strategy == "git_commit":
            content_hash = git_commit or "nogit"
        else:
            parts = [
                git_short or "nogit",
                deps_hash or "nodeps",
                dockerfile_hash or "nodockerfile",
                "dirty" if git_dirty else "clean",
            ]
            content_hash = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

        return {
            "git_commit": git_commit,
            "git_short": git_short,
            "git_branch": git_branch or "detached",
            "git_dirty": git_dirty,
            "dependencies_hash": deps_hash,
            "dockerfile_hash": dockerfile_hash,
            "content_hash": content_hash,
            "build_timestamp": to_iso8601(utc_now()),
        }

    def _parse_image_name(self, image_name: str) -> tuple[str, str | None]:
        value = image_name.strip()
        if ":" in value and "/" in value and value.rfind(":") < value.rfind("/"):
            return value, None
        if ":" in value:
            repo, tag = value.rsplit(":", 1)
            return repo, tag
        return value, None

    def _expected_cached_image_tag(self, image_name: str, settings: WorkspaceSettings, workspace_state: dict[str, Any]) -> str:
        image_repo, _ = self._parse_image_name(image_name)
        git_short = str(workspace_state.get("git_short") or "nogit")[:12]
        deps_hash = str(workspace_state.get("dependencies_hash") or "nodeps")[:12]
        content_hash = str(workspace_state.get("content_hash") or "nocontent")[:12]

        template = settings.build.image_caching.tag_format
        replacements = {
            "{image}": image_repo,
            "{git_short}": git_short,
            "{deps_hash}": deps_hash,
            "{content_hash}": content_hash,
        }
        rendered = template
        for key, value in replacements.items():
            rendered = rendered.replace(key, value)
        if ":" not in rendered:
            rendered = f"{image_repo}:{rendered}"
        return rendered

    def _docker_image_metadata(self, lease: dict[str, Any], docker_command: str, image_name: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        labels_cmd = (
            f"{docker_command} image inspect {shlex.quote(image_name)} --format "
            + shlex.quote("{{json .Config.Labels}}")
        )
        labels_result = self._exec_in_instance(lease=lease, command=labels_cmd, timeout_seconds=120)
        if labels_result.exit_code != 0:
            if "No such image" in labels_result.stderr:
                return None, None
            return None, self._docker_command_failed(labels_cmd, labels_result)

        created_cmd = (
            f"{docker_command} image inspect {shlex.quote(image_name)} --format "
            + shlex.quote("{{.Created}}")
        )
        created_result = self._exec_in_instance(lease=lease, command=created_cmd, timeout_seconds=120)
        if created_result.exit_code != 0:
            return None, self._docker_command_failed(created_cmd, created_result)

        labels_raw = labels_result.stdout.strip() or "{}"
        try:
            labels = json.loads(labels_raw)
            if not isinstance(labels, dict):
                labels = {}
        except json.JSONDecodeError:
            labels = {}

        created = created_result.stdout.strip()
        metadata: dict[str, Any] = {
            "labels": {str(k): str(v) for k, v in labels.items()},
            "created": created,
        }
        parsed = self._parse_docker_timestamp(created)
        if parsed is not None:
            age_hours = max((utc_now() - parsed).total_seconds() / 3600.0, 0.0)
            metadata["age_hours"] = round(age_hours, 3)
        return metadata, None

    def _parse_docker_timestamp(self, value: str) -> datetime | None:
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        if "." in raw and "+" in raw:
            head, tz = raw.rsplit("+", 1)
            prefix, frac = head.split(".", 1)
            frac = (frac + "000000")[:6]
            raw = f"{prefix}.{frac}+{tz}"
        try:
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None

    def _validation_policies(self, settings: WorkspaceSettings, checks: dict[str, Any] | None = None) -> dict[str, Any]:
        staleness = settings.build.staleness_detection
        prebuilt = settings.build.prebuilt.staleness_check
        base: dict[str, Any] = {
            "git_commit": prebuilt.check_git_commit,
            "git_dirty": "warn_and_rebuild" if staleness.check_git_dirty else "disabled",
            "dependencies": prebuilt.check_dependencies,
            "max_age_hours": prebuilt.check_age_threshold_hours,
        }
        if checks:
            base.update(checks)
        return base

    def _validate_image_internal(
        self,
        lease: dict[str, Any],
        workspace_root: str,
        image_name: str,
        settings: WorkspaceSettings,
        checks: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        runtime_error = self._ensure_runtime_ready(lease, runtime="docker")
        if runtime_error is not None:
            return {}, runtime_error
        docker_command = self._docker_command_for_lease(lease)

        workspace_state = self._workspace_state(workspace_root=workspace_root, settings=settings)
        image_meta, image_error = self._docker_image_metadata(lease=lease, docker_command=docker_command, image_name=image_name)
        if image_error is not None:
            return {}, image_error

        policies = self._validation_policies(settings=settings, checks=checks)
        reasons: list[str] = []
        warnings: list[str] = []
        recommendation = "reuse"

        if image_meta is None:
            reasons.append(f"image_missing: {image_name}")
            on_missing = settings.build.staleness_detection.on_missing
            recommendation = "build" if on_missing == "build" else on_missing
            return {
                "valid": False,
                "reasons": reasons,
                "warnings": warnings,
                "recommendation": recommendation,
                "workspace_state": workspace_state,
                "image_metadata": {"image_name": image_name},
            }, None

        labels = image_meta.get("labels", {})
        git_label = str(labels.get("git.commit") or labels.get("org.opencontainers.image.revision") or "")
        deps_label = str(labels.get("build.dependencies.hash") or "")
        image_age = image_meta.get("age_hours")

        if policies.get("git_commit", True):
            expected = str(workspace_state.get("git_commit") or "")
            if expected and git_label and expected != git_label:
                reasons.append(f"git_commit_mismatch: image={git_label}, workspace={expected}")
            elif expected and not git_label:
                reasons.append("git_commit_missing_in_image_labels")

        if bool(policies.get("dependencies", True)):
            expected = str(workspace_state.get("dependencies_hash") or "")
            if expected and deps_label and expected != deps_label:
                reasons.append(f"dependencies_changed: image={deps_label[:12]}, workspace={expected[:12]}")
            elif expected and not deps_label:
                reasons.append("dependencies_hash_missing_in_image_labels")

        max_age_hours = policies.get("max_age_hours")
        if isinstance(max_age_hours, (int, float)) and max_age_hours >= 0 and isinstance(image_age, (int, float)):
            if float(image_age) > float(max_age_hours):
                reasons.append(f"image_too_old: age_hours={image_age}, threshold_hours={float(max_age_hours)}")

        git_dirty_policy = str(policies.get("git_dirty", "disabled")).lower()
        if workspace_state.get("git_dirty"):
            msg = "workspace_has_uncommitted_changes"
            if git_dirty_policy in {"warn", "warning"}:
                warnings.append(msg)
            elif git_dirty_policy == "fail":
                reasons.append(msg)
                recommendation = "fail"
            elif git_dirty_policy in {"rebuild", "warn_and_rebuild"}:
                if git_dirty_policy == "warn_and_rebuild":
                    warnings.append(msg)
                reasons.append(msg)
                recommendation = "rebuild"

        valid = not reasons
        if not valid and recommendation == "reuse":
            recommendation = settings.build.prebuilt.staleness_check.on_stale

        if valid and warnings and recommendation == "reuse":
            recommendation = "warn"

        response = {
            "valid": valid,
            "reasons": reasons,
            "warnings": warnings,
            "recommendation": recommendation,
            "workspace_state": workspace_state,
            "image_metadata": {
                "image_name": image_name,
                "git_commit": git_label or None,
                "git_branch": labels.get("git.branch"),
                "build_timestamp": labels.get("build.timestamp") or labels.get("org.opencontainers.image.created"),
                "dependencies_hash": deps_label or None,
                "age_hours": image_age,
                "labels": labels,
            },
        }
        return response, None

    def _pid_alive(self, pid: int | None) -> bool:
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _refresh_task_state(self, task: dict[str, Any]) -> dict[str, Any]:
        if task.get("status") in TASK_TERMINAL_STATUSES:
            return task

        exit_file = Path(str(task["exit_code_path"]))
        if exit_file.exists():
            try:
                code = int(exit_file.read_text().strip() or "0")
            except ValueError:
                code = -1
            status = "succeeded" if code == 0 else "failed"
            self.store.update_task(
                str(task["task_id"]),
                status=status,
                exit_code=code,
                finished_at=to_iso8601(utc_now()),
            )
            refreshed = self.store.get_task(str(task["task_id"]))
            return refreshed or task

        pid = task.get("pid")
        if isinstance(pid, int) and not self._pid_alive(pid):
            self.store.update_task(
                str(task["task_id"]),
                status="failed",
                error_message="Task process exited without an exit marker",
                finished_at=to_iso8601(utc_now()),
            )
            refreshed = self.store.get_task(str(task["task_id"]))
            return refreshed or task

        return task

    def create_instance(
        self,
        workspace_root: str,
        ttl_minutes: int | None = None,
        auto_bootstrap: bool = True,
        wait_for_ready: bool = False,
    ) -> dict[str, Any]:
        if not self.backend.available:
            return self._backend_unavailable()

        workspace_settings, config_error = self._resolve_workspace_settings(workspace_root=workspace_root)
        if config_error is not None or workspace_settings is None:
            return config_error or error_response(
                "WORKSPACE_CONFIG_INVALID",
                "Workspace configuration is invalid",
                {"workspace_root": workspace_root},
            )

        ttl = ttl_minutes if ttl_minutes is not None else self.config.default_ttl_minutes
        if ttl <= 0 or ttl > self.config.max_ttl_minutes:
            return self._invalid_ttl(ttl_minutes)

        now = utc_now()
        now_iso = to_iso8601(now)
        active_count = self.store.count_active(ACTIVE_STATUSES, now_iso)
        if active_count >= self.config.max_instances:
            return error_response(
                "INSTANCE_LIMIT_EXCEEDED",
                "Maximum active instance limit reached",
                {
                    "max_instances": self.config.max_instances,
                    "active_instances": active_count,
                },
            )

        host_capacity_error = self._ensure_host_capacity_for_vm(workspace_settings)
        if host_capacity_error is not None:
            return host_capacity_error

        instance_id, backend_instance_name = self._new_identifiers()
        expires_at = now + timedelta(minutes=ttl)
        owner_session = os.getenv("MCP_SESSION_ID", "local")
        lease = {
            "instance_id": instance_id,
            "backend_name": self.backend.backend_name,
            "profile_name": "workspace",
            "status": "creating",
            "created_at": now_iso,
            "expires_at": to_iso8601(expires_at),
            "last_used_at": now_iso,
            "owner_session": owner_session,
            "ssh_port": None,
            "backend_instance_name": backend_instance_name,
            "workspace_root": workspace_settings.workspace_root,
            "workspace_id": workspace_settings.workspace_id,
            "runtime_name": None,
            "runtime_ready": 0,
            "docker_command": None,
        }

        self.store.create_lease(lease)

        try:
            vm_spec = VmCreateSpec(
                cpus=workspace_settings.vm.cpus,
                memory_gib=workspace_settings.vm.memory_gib,
                disk_gib=workspace_settings.vm.disk_gib,
                template=workspace_settings.vm.template,
                arch=workspace_settings.vm.arch,
                vm_type=workspace_settings.vm.vm_type,
            )
            self.backend.create_instance(backend_instance_name=backend_instance_name, vm_spec=vm_spec)
            self.backend.start_instance(backend_instance_name=backend_instance_name)
            ssh_port = None
            live_map = self._get_live_map()
            if backend_instance_name in live_map:
                ssh_port = self.backend.extract_ssh_port(live_map[backend_instance_name])

            self.store.update_lease(
                instance_id,
                status="running",
                ssh_port=ssh_port,
                last_used_at=to_iso8601(utc_now()),
            )
            payload = {
                "instance_id": instance_id,
                "backend": self.backend.backend_name,
                "status": "running",
                "created_at": now_iso,
                "expires_at": to_iso8601(expires_at),
                "ssh_port": ssh_port,
                "workspace_root": workspace_settings.workspace_root,
                "workspace_id": workspace_settings.workspace_id,
                "auto_bootstrap": auto_bootstrap,
                "wait_for_ready": wait_for_ready,
            }
            if auto_bootstrap:
                bootstrap = self.prepare_workspace(
                    instance_id=instance_id,
                    include_services=workspace_settings.infra.include_services_by_default,
                    wait_for_ready=wait_for_ready,
                )
                if "error_code" in bootstrap:
                    return error_response(
                        "WORKSPACE_BOOTSTRAP_FAILED",
                        "Instance was created but workspace bootstrap failed",
                        {
                            "instance_id": instance_id,
                            "workspace_root": workspace_settings.workspace_root,
                            "bootstrap_error": bootstrap,
                        },
                    )
                payload["bootstrap"] = bootstrap
            return payload
        except BackendUnavailableError:
            self.store.update_lease(instance_id, status="error", last_used_at=to_iso8601(utc_now()))
            return self._backend_unavailable()
        except BackendCommandError as exc:
            self.store.update_lease(instance_id, status="error", last_used_at=to_iso8601(utc_now()))
            details = exc.details()
            details["guidance"] = self._instance_creation_failure_guidance(exc)
            return error_response(
                "BACKEND_COMMAND_FAILED",
                "Backend command failed during instance creation",
                details,
            )

    def list_instances(self, include_expired: bool = False) -> dict[str, Any]:
        leases = [self._normalize_lease_status(row) for row in self.store.list_leases()]
        now = utc_now()

        filtered = leases
        if not include_expired:
            filtered = [
                row
                for row in leases
                if row.get("status") not in {"expired", "destroyed"}
                and parse_iso8601(str(row["expires_at"])) > now
            ]

        live_map: dict[str, dict[str, Any]] = {}
        backend_available = self.backend.available
        backend_error: dict[str, Any] | None = None

        if backend_available:
            try:
                live_map = self._get_live_map()
            except BackendCommandError as exc:
                backend_error = error_response(
                    "BACKEND_COMMAND_FAILED",
                    "Unable to reconcile with live backend state",
                    exc.details(),
                )

        instances: list[dict[str, Any]] = []
        for row in filtered:
            live = live_map.get(str(row["backend_instance_name"]))
            live_status = None
            if live is not None:
                live_status = str(live.get("status") or live.get("Status") or "") or None

            status = str(row["status"])
            drift = False
            if status == "running" and live is None:
                drift = True
            if status in TERMINAL_STATUSES and live is not None:
                drift = True

            instances.append(
                {
                    "instance_id": row["instance_id"],
                    "backend_name": row["backend_name"],
                    "profile_name": row["profile_name"],
                    "workspace_root": row.get("workspace_root"),
                    "workspace_id": row.get("workspace_id"),
                    "status": status,
                    "created_at": row["created_at"],
                    "expires_at": row["expires_at"],
                    "last_used_at": row["last_used_at"],
                    "owner_session": row["owner_session"],
                    "ssh_port": row["ssh_port"],
                    "backend_instance_name": row["backend_instance_name"],
                    "runtime_name": row.get("runtime_name"),
                    "runtime_ready": bool(row.get("runtime_ready")),
                    "docker_command": row.get("docker_command"),
                    "live_exists": live is not None,
                    "live_status": live_status,
                    "reconciliation_drift": drift,
                }
            )

        result: dict[str, Any] = {
            "instances": instances,
            "backend": self.backend.backend_name,
            "backend_available": backend_available,
        }
        if backend_available:
            result["backend_version"] = self.backend.version
        else:
            result["backend_error"] = self._backend_unavailable()
        if backend_error is not None:
            result["reconciliation_error"] = backend_error
        return result

    def _get_lease_for_action(
        self,
        instance_id: str,
        require_workspace: bool = False,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        lease = self.store.get_lease(instance_id)
        if lease is None:
            return None, error_response(
                "INSTANCE_NOT_FOUND",
                f"Instance '{instance_id}' was not found",
                {"instance_id": instance_id},
            )

        lease = self._normalize_lease_status(lease)
        if lease.get("status") in TERMINAL_STATUSES:
            return None, error_response(
                "INSTANCE_TERMINAL",
                f"Instance '{instance_id}' is in terminal state '{lease['status']}'",
                {"instance_id": instance_id, "status": lease["status"]},
            )

        if require_workspace and not lease.get("workspace_root"):
            return None, error_response(
                "WORKSPACE_BINDING_REQUIRED",
                f"Instance '{instance_id}' is not bound to a workspace",
                {"instance_id": instance_id},
            )

        return lease, None

    def run_command(self, instance_id: str, command: str, timeout_seconds: int = 600) -> dict[str, Any]:
        if not self.backend.available:
            return self._backend_unavailable()

        lease, error = self._get_lease_for_action(instance_id)
        if error is not None or lease is None:
            return error or error_response("INSTANCE_NOT_FOUND", "Instance not found", {"instance_id": instance_id})

        try:
            result = self._exec_in_instance(lease=lease, command=command, timeout_seconds=timeout_seconds)
            return {
                "instance_id": instance_id,
                "command": command,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration_ms": result.duration_ms,
            }
        except BackendUnavailableError:
            return self._backend_unavailable()
        except BackendCommandError as exc:
            return error_response(
                "BACKEND_COMMAND_FAILED",
                "Failed to execute command in backend instance",
                exc.details(),
            )

    def copy_to_instance(self, instance_id: str, local_path: str, remote_path: str) -> dict[str, Any]:
        if not self.backend.available:
            return self._backend_unavailable()

        lease, error = self._get_lease_for_action(instance_id)
        if error is not None or lease is None:
            return error or error_response("INSTANCE_NOT_FOUND", "Instance not found", {"instance_id": instance_id})

        try:
            self.backend.copy_to_instance(
                backend_instance_name=str(lease["backend_instance_name"]),
                local_path=local_path,
                remote_path=remote_path,
            )
            self.store.update_lease(instance_id, last_used_at=to_iso8601(utc_now()))
            return {
                "instance_id": instance_id,
                "local_path": local_path,
                "remote_path": remote_path,
                "status": "ok",
            }
        except BackendUnavailableError:
            return self._backend_unavailable()
        except BackendCommandError as exc:
            return error_response(
                "BACKEND_COMMAND_FAILED",
                "Copy to instance failed",
                exc.details(),
            )

    def copy_from_instance(self, instance_id: str, remote_path: str, local_path: str) -> dict[str, Any]:
        if not self.backend.available:
            return self._backend_unavailable()

        lease, error = self._get_lease_for_action(instance_id)
        if error is not None or lease is None:
            return error or error_response("INSTANCE_NOT_FOUND", "Instance not found", {"instance_id": instance_id})

        try:
            self.backend.copy_from_instance(
                backend_instance_name=str(lease["backend_instance_name"]),
                remote_path=remote_path,
                local_path=local_path,
            )
            self.store.update_lease(instance_id, last_used_at=to_iso8601(utc_now()))
            return {
                "instance_id": instance_id,
                "remote_path": remote_path,
                "local_path": local_path,
                "status": "ok",
            }
        except BackendUnavailableError:
            return self._backend_unavailable()
        except BackendCommandError as exc:
            return error_response(
                "BACKEND_COMMAND_FAILED",
                "Copy from instance failed",
                exc.details(),
            )

    def destroy_instance(self, instance_id: str, force: bool = False) -> dict[str, Any]:
        if not self.backend.available:
            return self._backend_unavailable()

        lease = self.store.get_lease(instance_id)
        if lease is None:
            return error_response(
                "INSTANCE_NOT_FOUND",
                f"Instance '{instance_id}' was not found",
                {"instance_id": instance_id},
            )

        backend_instance_name = str(lease["backend_instance_name"])
        now_iso = to_iso8601(utc_now())

        try:
            self.backend.stop_instance(backend_instance_name=backend_instance_name, force=force)
            self.backend.delete_instance(backend_instance_name=backend_instance_name, force=force)
        except BackendUnavailableError:
            return self._backend_unavailable()
        except BackendCommandError as exc:
            return error_response(
                "BACKEND_COMMAND_FAILED",
                "Destroy instance failed",
                exc.details(),
            )

        self.store.update_lease(instance_id, status="destroyed", last_used_at=now_iso)
        return {
            "instance_id": instance_id,
            "backend": self.backend.backend_name,
            "status": "destroyed",
        }

    def _sanitize_name(self, value: str) -> str:
        cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value)
        return cleaned.strip("-_") or "default"

    def _ensure_docker_runtime(self, lease: dict[str, Any], settings: WorkspaceSettings) -> tuple[str | None, dict[str, Any] | None]:
        command = self.docker.prepare_runtime_script(install_if_missing=settings.docker.install_if_missing)
        result = self._exec_in_instance(lease=lease, command=command, timeout_seconds=900)
        if result.exit_code != 0:
            return None, error_response(
                "RUNTIME_NOT_READY",
                "Failed to prepare docker runtime",
                self._command_details(command, result),
            )

        docker_command, detect_result = self._detect_docker_command(lease)
        if not docker_command:
            return None, error_response(
                "RUNTIME_NOT_READY",
                "Docker runtime became available but command permissions are unresolved",
                self._command_details("docker readiness probe", detect_result),
            )

        self.store.update_lease(
            lease["instance_id"],
            runtime_name="docker",
            runtime_ready=1,
            docker_command=docker_command,
            last_used_at=to_iso8601(utc_now()),
        )
        lease["runtime_name"] = "docker"
        lease["runtime_ready"] = 1
        lease["docker_command"] = docker_command
        return docker_command, None

    def _ensure_network(
        self,
        lease: dict[str, Any],
        docker_command: str,
        network_name: str,
    ) -> tuple[str, dict[str, Any] | None]:
        inspect_cmd = f"{docker_command} network inspect {shlex.quote(network_name)} >/dev/null 2>&1"
        inspect = self._exec_in_instance(lease=lease, command=inspect_cmd, timeout_seconds=120)
        if inspect.exit_code == 0:
            return "existing", None

        create_cmd = f"{docker_command} network create {shlex.quote(network_name)}"
        create = self._exec_in_instance(lease=lease, command=create_cmd, timeout_seconds=120)
        if create.exit_code != 0:
            return "failed", error_response(
                "DOCKER_COMMAND_FAILED",
                "Failed to create docker network",
                self._command_details(create_cmd, create),
            )
        return "created", None

    def _ensure_service_container(
        self,
        lease: dict[str, Any],
        docker_command: str,
        name: str,
        image: str,
        run_flags: list[str] | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        inspect_cmd = f"{docker_command} inspect -f " + shlex.quote("{{.State.Running}}") + f" {shlex.quote(name)}"
        inspect = self._exec_in_instance(lease=lease, command=inspect_cmd, timeout_seconds=120)
        if inspect.exit_code == 0:
            state = inspect.stdout.strip().lower()
            if state == "true":
                return "running", None
            start_cmd = f"{docker_command} start {shlex.quote(name)}"
            start = self._exec_in_instance(lease=lease, command=start_cmd, timeout_seconds=120)
            if start.exit_code != 0:
                return "failed", error_response(
                    "DOCKER_COMMAND_FAILED",
                    f"Failed to start container '{name}'",
                    self._command_details(start_cmd, start),
                )
            return "started", None

        flags = " ".join(run_flags or [])
        run_cmd = f"{docker_command} run -d --name {shlex.quote(name)} {flags} {shlex.quote(image)}".strip()
        run = self._exec_in_instance(lease=lease, command=run_cmd, timeout_seconds=300)
        if run.exit_code != 0:
            return "failed", error_response(
                "DOCKER_COMMAND_FAILED",
                f"Failed to create container '{name}'",
                self._command_details(run_cmd, run),
            )
        return "created", None

    def _infra_network_name(self, settings: WorkspaceSettings, instance_id: str) -> str:
        explicit = str(settings.infra.network_name or "").strip()
        if explicit:
            return explicit
        return self._sanitize_name(f"{settings.infra.network_name_prefix}-{instance_id[:8]}")

    def _build_connection_env(
        self,
        settings: WorkspaceSettings,
        services_payload: dict[str, Any],
    ) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
        connection_env: dict[str, str] = {}
        inject_targets: dict[str, dict[str, str]] = {}

        mysql = services_payload.get("mysql")
        if isinstance(mysql, dict):
            host = str(mysql.get("host") or "")
            if host:
                mysql_env = {
                    "DB_HOST": host,
                    "DB_PORT": "3306",
                    "DB_NAME": settings.infra.mysql.database,
                    "DB_USER": "root",
                    "DB_PASSWORD": settings.infra.mysql.root_password,
                    "MYSQL_HOST": host,
                    "MYSQL_PORT": "3306",
                    "MYSQL_DATABASE": settings.infra.mysql.database,
                    "MYSQL_USER": "root",
                    "MYSQL_PASSWORD": settings.infra.mysql.root_password,
                }
                connection_env.update(mysql_env)
                if settings.infra.mysql.inject_env_to:
                    inject_targets.setdefault(settings.infra.mysql.inject_env_to, {}).update(mysql_env)

        redis = services_payload.get("redis")
        if isinstance(redis, dict):
            host = str(redis.get("host") or "")
            if host:
                redis_env = {
                    "REDIS_HOST": host,
                    "REDIS_PORT": "6379",
                    "REDIS_URL": f"redis://{host}:6379/0",
                }
                connection_env.update(redis_env)
                if settings.infra.redis.inject_env_to:
                    inject_targets.setdefault(settings.infra.redis.inject_env_to, {}).update(redis_env)

        return connection_env, inject_targets

    def _write_env_targets(
        self,
        settings: WorkspaceSettings,
        inject_targets: dict[str, dict[str, str]],
    ) -> list[dict[str, Any]]:
        writes: list[dict[str, Any]] = []
        for target, env_map in inject_targets.items():
            destination = Path(target)
            if not destination.is_absolute():
                destination = Path(settings.workspace_root) / target
            destination = destination.resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)

            existing_lines: list[str] = []
            if destination.exists():
                existing_lines = destination.read_text(encoding="utf-8").splitlines()

            positions: dict[str, int] = {}
            for idx, line in enumerate(existing_lines):
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key = stripped.split("=", 1)[0].strip()
                positions[key] = idx

            updated = list(existing_lines)
            for key, value in sorted(env_map.items()):
                rendered = f"{key}={value}"
                if key in positions:
                    updated[positions[key]] = rendered
                else:
                    updated.append(rendered)

            content = "\n".join(updated).rstrip() + "\n"
            destination.write_text(content, encoding="utf-8")
            writes.append({"path": str(destination), "keys": sorted(env_map.keys())})
        return writes

    def _discover_running_infra_services(
        self,
        lease: dict[str, Any],
        settings: WorkspaceSettings,
        docker_command: str,
    ) -> dict[str, Any]:
        instance_id = str(lease["instance_id"])
        services_payload: dict[str, Any] = {}
        candidates: list[tuple[str, str, int]] = []
        if settings.infra.mysql.enabled:
            candidates.append(("mysql", self._sanitize_name(f"{settings.infra.mysql.container_name_prefix}-{instance_id[:8]}"), 3306))
        if settings.infra.redis.enabled:
            candidates.append(("redis", self._sanitize_name(f"{settings.infra.redis.container_name_prefix}-{instance_id[:8]}"), 6379))

        template = "{{.State.Running}}"
        for service_name, container_name, port in candidates:
            inspect_cmd = f"{docker_command} inspect -f {shlex.quote(template)} {shlex.quote(container_name)}"
            inspect = self._exec_in_instance(lease=lease, command=inspect_cmd, timeout_seconds=60)
            if inspect.exit_code != 0:
                continue
            services_payload[service_name] = {
                "status": "running" if inspect.stdout.strip().lower() == "true" else "stopped",
                "container": container_name,
                "host": container_name,
                "port": port,
            }
        return services_payload

    def _wait_for_service_readiness(
        self,
        lease: dict[str, Any],
        docker_command: str,
        settings: WorkspaceSettings,
        services_payload: dict[str, Any],
        timeout_seconds: int = DEFAULT_SERVICE_READY_TIMEOUT_SECONDS,
        poll_seconds: int = DEFAULT_SERVICE_READY_POLL_SECONDS,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        probes: dict[str, str] = {}
        attempts: dict[str, int] = {}
        last_results: dict[str, dict[str, Any]] = {}

        mysql = services_payload.get("mysql")
        if isinstance(mysql, dict) and mysql.get("container"):
            inner = (
                f"MYSQL_PWD={shlex.quote(settings.infra.mysql.root_password)} "
                "mysqladmin ping -h 127.0.0.1 -uroot --silent"
            )
            probes["mysql"] = f"{docker_command} exec {shlex.quote(str(mysql['container']))} sh -lc {shlex.quote(inner)}"

        redis = services_payload.get("redis")
        if isinstance(redis, dict) and redis.get("container"):
            inner = "redis-cli ping | grep -q PONG"
            probes["redis"] = f"{docker_command} exec {shlex.quote(str(redis['container']))} sh -lc {shlex.quote(inner)}"

        if not probes:
            return {}, None

        pending = set(probes.keys())
        deadline = time.monotonic() + max(timeout_seconds, 1)
        step_timeout = max(5, poll_seconds * 2)

        while pending and time.monotonic() < deadline:
            for service_name in list(pending):
                command = probes[service_name]
                attempts[service_name] = attempts.get(service_name, 0) + 1
                result = self._exec_in_instance(
                    lease=lease,
                    command=command,
                    timeout_seconds=step_timeout,
                )
                last_results[service_name] = self._command_details(command, result)
                if result.exit_code == 0:
                    pending.remove(service_name)
            if pending:
                time.sleep(max(poll_seconds, 1))

        readiness = {
            name: {
                "ready": name not in pending,
                "attempts": attempts.get(name, 0),
            }
            for name in probes
        }
        if not pending:
            return readiness, None

        return readiness, error_response(
            "SERVICE_NOT_READY",
            "Timed out waiting for infrastructure services to become ready",
            {
                "instance_id": lease["instance_id"],
                "timeout_seconds": timeout_seconds,
                "pending_services": sorted(pending),
                "readiness": readiness,
                "last_probe_results": {name: last_results.get(name, {}) for name in sorted(pending)},
            },
        )

    def _detect_writable_workspace_paths(self, lease: dict[str, Any]) -> list[str]:
        probe_cmd = (
            'for p in /workspace /tmp/workspace "$HOME/workspace"; do '
            'if mkdir -p "$p" >/dev/null 2>&1 && touch "$p/.sandboxforge-write-test" >/dev/null 2>&1; then '
            'rm -f "$p/.sandboxforge-write-test" >/dev/null 2>&1; '
            'echo "$p"; '
            "fi; "
            "done"
        )
        result = self._exec_in_instance(lease=lease, command=probe_cmd, timeout_seconds=60)
        if result.exit_code != 0:
            return ["/tmp/workspace"]
        discovered = [line.strip() for line in result.stdout.splitlines() if line.strip().startswith("/")]
        if not discovered:
            return ["/tmp/workspace"]
        return discovered

    def _bridge_compose_to_infra_network(
        self,
        lease: dict[str, Any],
        settings: WorkspaceSettings,
        docker_command: str,
        project_dir: str,
        file: str | None,
        services: list[str] | None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        network_name = self._infra_network_name(settings, str(lease["instance_id"]))
        network_status, network_error = self._ensure_network(lease=lease, docker_command=docker_command, network_name=network_name)
        if network_error is not None:
            return {}, network_error

        compose_ps_cmd = self.docker.docker_compose_command(
            action="ps",
            docker_command=docker_command,
            file=file,
            services=services,
            quiet=True,
        )
        ps_result = self._exec_in_instance(
            lease=lease,
            command=compose_ps_cmd,
            timeout_seconds=180,
            cwd=project_dir,
        )
        if ps_result.exit_code != 0:
            return {}, self._docker_command_failed(compose_ps_cmd, ps_result)

        container_ids = [line.strip() for line in ps_result.stdout.splitlines() if line.strip()]
        if not container_ids:
            return {
                "status": "no_compose_containers",
                "network": network_name,
                "network_status": network_status,
                "containers_seen": 0,
                "containers_connected": 0,
                "containers_already_connected": 0,
            }, None

        connected = 0
        already_connected = 0
        template = "{{range $k, $_ := .NetworkSettings.Networks}}{{$k}} {{end}}"

        for container_id in container_ids:
            inspect_cmd = f"{docker_command} inspect -f {shlex.quote(template)} {shlex.quote(container_id)}"
            inspect = self._exec_in_instance(lease=lease, command=inspect_cmd, timeout_seconds=120)
            if inspect.exit_code != 0:
                return {}, self._docker_command_failed(inspect_cmd, inspect)
            existing_networks = set(inspect.stdout.strip().split())
            if network_name in existing_networks:
                already_connected += 1
                continue

            connect_cmd = f"{docker_command} network connect {shlex.quote(network_name)} {shlex.quote(container_id)}"
            connect = self._exec_in_instance(lease=lease, command=connect_cmd, timeout_seconds=120)
            if connect.exit_code != 0:
                return {}, self._docker_command_failed(connect_cmd, connect)
            connected += 1

        return {
            "status": "ok",
            "network": network_name,
            "network_status": network_status,
            "containers_seen": len(container_ids),
            "containers_connected": connected,
            "containers_already_connected": already_connected,
        }, None

    def prepare_workspace(self, instance_id: str, include_services: bool = True, wait_for_ready: bool = False) -> dict[str, Any]:
        lease, error = self._get_lease_for_action(instance_id, require_workspace=True)
        if error is not None or lease is None:
            return error or error_response("INSTANCE_NOT_FOUND", "Instance not found", {"instance_id": instance_id})

        settings, config_error = self._load_workspace_settings_for_lease(lease)
        if config_error is not None or settings is None:
            return config_error or error_response(
                "WORKSPACE_CONFIG_INVALID",
                "Workspace configuration is invalid",
                {"instance_id": instance_id},
            )

        docker_command, runtime_error = self._ensure_docker_runtime(lease, settings)
        if runtime_error is not None or docker_command is None:
            return runtime_error or error_response(
                "RUNTIME_NOT_READY",
                "Failed to prepare docker runtime",
                {"instance_id": instance_id},
            )

        network_name = self._infra_network_name(settings, instance_id)
        network_status = "skipped"
        if settings.infra.ensure_network:
            network_status, network_error = self._ensure_network(lease, docker_command, network_name)
            if network_error is not None:
                return network_error

        effective_include_services = include_services and settings.infra.include_services_by_default
        services_payload: dict[str, Any] = {}
        if effective_include_services:
            if settings.infra.mysql.enabled:
                mysql_name = self._sanitize_name(f"{settings.infra.mysql.container_name_prefix}-{instance_id[:8]}")
                mysql_flags = [
                    f"--network {shlex.quote(network_name)}",
                    f"-e MYSQL_ROOT_PASSWORD={shlex.quote(settings.infra.mysql.root_password)}",
                    f"-e MYSQL_DATABASE={shlex.quote(settings.infra.mysql.database)}",
                ]
                for key, value in sorted(settings.infra.mysql.extra_env.items()):
                    mysql_flags.append(f"-e {shlex.quote(f'{key}={value}')}")
                mysql_status, mysql_error = self._ensure_service_container(
                    lease=lease,
                    docker_command=docker_command,
                    name=mysql_name,
                    image=settings.infra.mysql.image,
                    run_flags=mysql_flags,
                )
                if mysql_error is not None:
                    return mysql_error
                services_payload["mysql"] = {
                    "status": mysql_status,
                    "container": mysql_name,
                    "image": settings.infra.mysql.image,
                    "host": mysql_name,
                    "port": 3306,
                    "database": settings.infra.mysql.database,
                }

            if settings.infra.redis.enabled:
                redis_name = self._sanitize_name(f"{settings.infra.redis.container_name_prefix}-{instance_id[:8]}")
                redis_flags = [f"--network {shlex.quote(network_name)}"]
                for key, value in sorted(settings.infra.redis.extra_env.items()):
                    redis_flags.append(f"-e {shlex.quote(f'{key}={value}')}")
                redis_status, redis_error = self._ensure_service_container(
                    lease=lease,
                    docker_command=docker_command,
                    name=redis_name,
                    image=settings.infra.redis.image,
                    run_flags=redis_flags,
                )
                if redis_error is not None:
                    return redis_error
                services_payload["redis"] = {
                    "status": redis_status,
                    "container": redis_name,
                    "image": settings.infra.redis.image,
                    "host": redis_name,
                    "port": 6379,
                }

        readiness: dict[str, Any] = {}
        if wait_for_ready and effective_include_services:
            readiness, ready_error = self._wait_for_service_readiness(
                lease=lease,
                docker_command=docker_command,
                settings=settings,
                services_payload=services_payload,
            )
            if ready_error is not None:
                return ready_error

        connection_env, inject_targets = self._build_connection_env(settings=settings, services_payload=services_payload)
        try:
            env_injections = self._write_env_targets(settings=settings, inject_targets=inject_targets) if inject_targets else []
        except OSError as exc:
            return error_response(
                "WORKSPACE_ENV_INJECTION_FAILED",
                "Failed to write injected environment file",
                {
                    "instance_id": instance_id,
                    "workspace_root": settings.workspace_root,
                    "error": str(exc),
                },
            )
        writable_paths = self._detect_writable_workspace_paths(lease)

        return {
            "instance_id": instance_id,
            "workspace_root": settings.workspace_root,
            "workspace_id": settings.workspace_id,
            "runtime": "docker",
            "runtime_ready": True,
            "docker_command": docker_command,
            "network": {
                "name": network_name,
                "status": network_status,
            },
            "include_services": effective_include_services,
            "services": services_payload,
            "wait_for_ready": wait_for_ready,
            "service_readiness": readiness,
            "connection_env": connection_env,
            "env_injections": env_injections,
            "workspace_paths": {
                "preferred": writable_paths[0] if writable_paths else "/tmp/workspace",
                "writable": writable_paths,
            },
        }

    def sync_workspace_to_instance(
        self,
        instance_id: str,
        local_path: str,
        remote_path: str,
        exclude: list[str] | None = None,
    ) -> dict[str, Any]:
        lease, error = self._get_lease_for_action(instance_id, require_workspace=True)
        if error is not None or lease is None:
            return error or error_response("INSTANCE_NOT_FOUND", "Instance not found", {"instance_id": instance_id})

        settings, config_error = self._load_workspace_settings_for_lease(lease)
        if config_error is not None or settings is None:
            return config_error or error_response(
                "WORKSPACE_CONFIG_INVALID",
                "Workspace configuration is invalid",
                {"instance_id": instance_id},
            )

        source = Path(local_path).expanduser().resolve()
        if not source.exists():
            return error_response(
                "BACKEND_COMMAND_FAILED",
                "Local path does not exist",
                {"local_path": str(source)},
            )

        patterns = list(exclude if exclude is not None else settings.sync.exclude_patterns)
        if not settings.sync.include_git:
            patterns.extend([".git", ".git/**"])
        tmp_fd, tmp_name = tempfile.mkstemp(prefix="workspace-sync-", suffix=".tar.gz")
        os.close(tmp_fd)

        try:
            def filter_member(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
                rel = tarinfo.name.lstrip("./")
                if rel:
                    for pattern in patterns:
                        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(Path(rel).name, pattern):
                            return None
                return tarinfo

            with tarfile.open(tmp_name, "w:gz") as tar:
                tar.add(str(source), arcname=".", filter=filter_member)

            remote_archive = f"/tmp/{Path(tmp_name).name}"
            self.backend.copy_to_instance(str(lease["backend_instance_name"]), tmp_name, remote_archive)
            extract_cmd = (
                f"mkdir -p {shlex.quote(remote_path)} && "
                f"tar -xzf {shlex.quote(remote_archive)} -C {shlex.quote(remote_path)} && "
                f"rm -f {shlex.quote(remote_archive)}"
            )
            result = self._exec_in_instance(lease=lease, command=extract_cmd, timeout_seconds=1200)
            if result.exit_code != 0:
                return error_response(
                    "BACKEND_COMMAND_FAILED",
                    "Failed to extract workspace archive in instance",
                    self._command_details(extract_cmd, result),
                )

            archive_size = Path(tmp_name).stat().st_size
            return {
                "instance_id": instance_id,
                "local_path": str(source),
                "remote_path": remote_path,
                "archive_bytes": archive_size,
                "exclude": patterns,
                "status": "ok",
            }
        except BackendCommandError as exc:
            return error_response(
                "BACKEND_COMMAND_FAILED",
                "Workspace sync failed",
                exc.details(),
            )
        finally:
            Path(tmp_name).unlink(missing_ok=True)

    def sync_instance_to_workspace(self, instance_id: str, remote_path: str, local_path: str) -> dict[str, Any]:
        lease, error = self._get_lease_for_action(instance_id, require_workspace=True)
        if error is not None or lease is None:
            return error or error_response("INSTANCE_NOT_FOUND", "Instance not found", {"instance_id": instance_id})

        target = Path(local_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.backend.copy_from_instance(
                backend_instance_name=str(lease["backend_instance_name"]),
                remote_path=remote_path,
                local_path=str(target),
            )
            self.store.update_lease(instance_id, last_used_at=to_iso8601(utc_now()))
            return {
                "instance_id": instance_id,
                "remote_path": remote_path,
                "local_path": str(target),
                "status": "ok",
            }
        except BackendCommandError as exc:
            return error_response(
                "BACKEND_COMMAND_FAILED",
                "Reverse sync from instance failed",
                exc.details(),
            )

    def docker_build(
        self,
        instance_id: str,
        context_path: str,
        image_tag: str,
        dockerfile: str | None = None,
        build_args: dict[str, str] | None = None,
        target: str | None = None,
        no_cache: bool = False,
    ) -> dict[str, Any]:
        lease, error = self._get_lease_for_action(instance_id, require_workspace=True)
        if error is not None or lease is None:
            return error or error_response("INSTANCE_NOT_FOUND", "Instance not found", {"instance_id": instance_id})

        settings, config_error = self._load_workspace_settings_for_lease(lease)
        if config_error is not None or settings is None:
            return config_error or error_response(
                "WORKSPACE_CONFIG_INVALID",
                "Workspace configuration is invalid",
                {"instance_id": instance_id},
            )

        runtime_error = self._ensure_runtime_ready(lease, runtime="docker")
        if runtime_error is not None:
            return runtime_error

        docker_command = self._docker_command_for_lease(lease)
        min_free_gib = settings.build.min_free_gib
        preflight_error = self._ensure_build_disk_headroom(
            lease=lease,
            docker_command=docker_command,
            settings=settings,
            required_gib=min_free_gib,
        )
        if preflight_error is not None:
            return preflight_error

        workspace_state = self._workspace_state(workspace_root=settings.workspace_root, settings=settings)
        cache_strategy = settings.build.image_caching.strategy
        expected_cached_tag = self._expected_cached_image_tag(
            image_name=image_tag,
            settings=settings,
            workspace_state=workspace_state,
        )
        validation_result: dict[str, Any] | None = None

        if settings.build.image_caching.enabled and settings.build.prebuilt.enabled and not no_cache:
            validation_mode = settings.build.prebuilt.validation
            if validation_mode == "disabled":
                image_meta, image_meta_error = self._docker_image_metadata(
                    lease=lease,
                    docker_command=docker_command,
                    image_name=expected_cached_tag,
                )
                if image_meta_error is not None:
                    return image_meta_error
                if image_meta is not None:
                    if expected_cached_tag != image_tag:
                        tag_cmd = f"{docker_command} tag {shlex.quote(expected_cached_tag)} {shlex.quote(image_tag)}"
                        tag_result = self._exec_in_instance(lease=lease, command=tag_cmd, timeout_seconds=120)
                        if tag_result.exit_code != 0:
                            return self._docker_command_failed(tag_cmd, tag_result)
                    return {
                        "instance_id": instance_id,
                        "command": "cached-image-reuse",
                        "image_tag": image_tag,
                        "cached_image_tag": expected_cached_tag,
                        "cache_strategy": cache_strategy,
                        "cache_hit": True,
                        "validation": {"valid": True, "mode": "disabled"},
                        "exit_code": 0,
                        "stdout": "",
                        "stderr": "",
                        "duration_ms": 0,
                    }
            else:
                validation_result, validation_error = self._validate_image_internal(
                    lease=lease,
                    workspace_root=settings.workspace_root,
                    image_name=expected_cached_tag,
                    settings=settings,
                    checks=None,
                )
                if validation_error is not None:
                    return validation_error

                if validation_result.get("valid"):
                    if expected_cached_tag != image_tag:
                        tag_cmd = f"{docker_command} tag {shlex.quote(expected_cached_tag)} {shlex.quote(image_tag)}"
                        tag_result = self._exec_in_instance(lease=lease, command=tag_cmd, timeout_seconds=120)
                        if tag_result.exit_code != 0:
                            return self._docker_command_failed(tag_cmd, tag_result)
                    return {
                        "instance_id": instance_id,
                        "command": "cached-image-reuse",
                        "image_tag": image_tag,
                        "cached_image_tag": expected_cached_tag,
                        "cache_strategy": cache_strategy,
                        "cache_hit": True,
                        "validation": validation_result,
                        "exit_code": 0,
                        "stdout": "",
                        "stderr": "",
                        "duration_ms": 0,
                    }

                recommendation = str(validation_result.get("recommendation") or settings.build.prebuilt.staleness_check.on_stale)
                if validation_mode == "strict" and recommendation == "fail":
                    return error_response(
                        "PREBUILT_IMAGE_INVALID",
                        "Prebuilt image failed strict validation",
                        {
                            "instance_id": instance_id,
                            "image_tag": expected_cached_tag,
                            "validation": validation_result,
                        },
                    )

        auto_build_args = {
            "GIT_COMMIT": str(workspace_state.get("git_commit") or "unknown"),
            "GIT_BRANCH": str(workspace_state.get("git_branch") or "unknown"),
            "BUILD_TIMESTAMP": str(workspace_state.get("build_timestamp") or to_iso8601(utc_now())),
            "DEPENDENCIES_HASH": str(workspace_state.get("dependencies_hash") or "unknown"),
            "WORKSPACE_CONTENT_HASH": str(workspace_state.get("content_hash") or "unknown"),
        }
        merged_build_args = dict(auto_build_args)
        if build_args:
            merged_build_args.update(build_args)

        auto_labels = {
            "git.commit": str(workspace_state.get("git_commit") or ""),
            "git.branch": str(workspace_state.get("git_branch") or ""),
            "build.timestamp": str(workspace_state.get("build_timestamp") or ""),
            "build.dependencies.hash": str(workspace_state.get("dependencies_hash") or ""),
            "build.content.hash": str(workspace_state.get("content_hash") or ""),
            "org.opencontainers.image.revision": str(workspace_state.get("git_commit") or ""),
            "org.opencontainers.image.created": str(workspace_state.get("build_timestamp") or ""),
        }

        command = self.docker.docker_build_command(
            context_path=context_path,
            image_tag=image_tag,
            docker_command=docker_command,
            dockerfile=dockerfile,
            build_args=merged_build_args,
            labels=auto_labels,
            target=target,
            no_cache=no_cache,
        )
        env = {"DOCKER_BUILDKIT": "1"} if settings.build.enable_buildkit else None
        result = self._exec_in_instance(lease=lease, command=command, timeout_seconds=1800, env=env)
        if result.exit_code != 0:
            return self._docker_command_failed(command, result)

        if settings.build.image_caching.enabled and expected_cached_tag != image_tag:
            tag_cmd = f"{docker_command} tag {shlex.quote(image_tag)} {shlex.quote(expected_cached_tag)}"
            tag_result = self._exec_in_instance(lease=lease, command=tag_cmd, timeout_seconds=120)
            if tag_result.exit_code != 0:
                return self._docker_command_failed(tag_cmd, tag_result)

        return {
            "instance_id": instance_id,
            "command": command,
            "image_tag": image_tag,
            "cached_image_tag": expected_cached_tag,
            "cache_strategy": cache_strategy,
            "cache_hit": False,
            "validation": validation_result,
            "workspace_state": workspace_state,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
        }

    def _disk_free_gib(self, lease: dict[str, Any]) -> tuple[float | None, CommandResult]:
        probe_cmd = "df -Pk / | awk 'NR==2 {print $4}'"
        result = self._exec_in_instance(lease=lease, command=probe_cmd, timeout_seconds=60)
        if result.exit_code != 0:
            return None, result
        raw = result.stdout.strip().splitlines()
        if not raw:
            return None, result
        try:
            kib = int(raw[-1].strip())
        except ValueError:
            return None, result
        return kib / (1024 * 1024), result

    def _ensure_build_disk_headroom(
        self,
        lease: dict[str, Any],
        docker_command: str,
        settings: WorkspaceSettings,
        required_gib: float,
    ) -> dict[str, Any] | None:
        free_before, probe_before = self._disk_free_gib(lease)
        if free_before is None:
            return error_response(
                "BACKEND_COMMAND_FAILED",
                "Unable to determine free disk space before docker build",
                self._command_details("disk probe", probe_before),
            )
        if free_before >= required_gib:
            return None

        cleanup_cmd = self.docker.docker_cleanup_command(mode="safe", docker_command=docker_command)
        cleanup_result = self._exec_in_instance(lease=lease, command=cleanup_cmd, timeout_seconds=600)
        if cleanup_result.exit_code != 0:
            return self._docker_command_failed(cleanup_cmd, cleanup_result)

        free_after, probe_after = self._disk_free_gib(lease)
        if free_after is None:
            return error_response(
                "BACKEND_COMMAND_FAILED",
                "Unable to determine free disk space after cleanup",
                self._command_details("disk probe", probe_after),
            )
        if free_after >= required_gib:
            return None

        recommended_disk = max(int(settings.vm.disk_gib * 2), 30)
        workspace_config_path = Path(settings.workspace_root) / ".sandboxforge.toml"
        return error_response(
            "INSUFFICIENT_DISK",
            "Insufficient free disk for docker build after safe cleanup",
            {
                "instance_id": lease["instance_id"],
                "workspace_root": settings.workspace_root,
                "free_disk_gib_before_cleanup": round(free_before, 3),
                "free_disk_gib_after_cleanup": round(free_after, 3),
                "required_free_disk_gib": required_gib,
                "cleanup_command": cleanup_cmd,
                "guided_recreate_action": [
                    f"Set vm.disk_gib >= {recommended_disk} in {workspace_config_path}",
                    f"destroy_instance(instance_id='{lease['instance_id']}')",
                    f"create_instance(workspace_root='{settings.workspace_root}', auto_bootstrap=True)",
                ],
            },
        )

    def docker_run(
        self,
        instance_id: str,
        image: str,
        command: str | None = None,
        name: str | None = None,
        env: dict[str, str] | None = None,
        volumes: list[str] | None = None,
        ports: list[str] | None = None,
        workdir: str | None = None,
        detach: bool = True,
        privileged: bool = False,
    ) -> dict[str, Any]:
        lease, error = self._get_lease_for_action(instance_id, require_workspace=True)
        if error is not None or lease is None:
            return error or error_response("INSTANCE_NOT_FOUND", "Instance not found", {"instance_id": instance_id})

        runtime_error = self._ensure_runtime_ready(lease, runtime="docker")
        if runtime_error is not None:
            return runtime_error

        docker_command = self._docker_command_for_lease(lease)
        docker_cmd = self.docker.docker_run_command(
            image=image,
            docker_command=docker_command,
            command=command,
            name=name,
            env=env,
            volumes=volumes,
            ports=ports,
            workdir=workdir,
            detach=detach,
            privileged=privileged,
        )
        result = self._exec_in_instance(lease=lease, command=docker_cmd, timeout_seconds=1200)
        if result.exit_code != 0:
            return self._docker_command_failed(docker_cmd, result)

        container_id = result.stdout.strip().splitlines()[0] if result.stdout.strip() else None
        return {
            "instance_id": instance_id,
            "command": docker_cmd,
            "container": container_id,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
        }

    def docker_exec(self, instance_id: str, container: str, command: str, timeout_seconds: int = 600) -> dict[str, Any]:
        lease, error = self._get_lease_for_action(instance_id, require_workspace=True)
        if error is not None or lease is None:
            return error or error_response("INSTANCE_NOT_FOUND", "Instance not found", {"instance_id": instance_id})

        runtime_error = self._ensure_runtime_ready(lease, runtime="docker")
        if runtime_error is not None:
            return runtime_error

        docker_cmd = self.docker.docker_exec_command(
            container=container,
            command=command,
            docker_command=self._docker_command_for_lease(lease),
        )
        result = self._exec_in_instance(lease=lease, command=docker_cmd, timeout_seconds=timeout_seconds)
        if result.exit_code != 0:
            return self._docker_command_failed(docker_cmd, result)
        return {
            "instance_id": instance_id,
            "container": container,
            "command": docker_cmd,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
        }

    def docker_logs(
        self,
        instance_id: str,
        container: str,
        tail: int = 500,
        follow: bool = False,
        since: str | None = None,
    ) -> dict[str, Any]:
        lease, error = self._get_lease_for_action(instance_id, require_workspace=True)
        if error is not None or lease is None:
            return error or error_response("INSTANCE_NOT_FOUND", "Instance not found", {"instance_id": instance_id})

        runtime_error = self._ensure_runtime_ready(lease, runtime="docker")
        if runtime_error is not None:
            return runtime_error

        docker_cmd = self.docker.docker_logs_command(
            container=container,
            docker_command=self._docker_command_for_lease(lease),
            tail=tail,
            follow=follow,
            since=since,
        )
        timeout_seconds = 30 if follow else 600
        result = self._exec_in_instance(lease=lease, command=docker_cmd, timeout_seconds=timeout_seconds)
        if result.exit_code != 0:
            return self._docker_command_failed(docker_cmd, result)
        return {
            "instance_id": instance_id,
            "container": container,
            "command": docker_cmd,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
        }

    def docker_compose(
        self,
        instance_id: str,
        project_dir: str,
        action: str,
        file: str | None = None,
        services: list[str] | None = None,
        detach: bool = True,
        command: str | None = None,
        follow: bool = False,
        since: str | None = None,
        tail: int | None = None,
    ) -> dict[str, Any]:
        lease, error = self._get_lease_for_action(instance_id, require_workspace=True)
        if error is not None or lease is None:
            return error or error_response("INSTANCE_NOT_FOUND", "Instance not found", {"instance_id": instance_id})

        settings, config_error = self._load_workspace_settings_for_lease(lease)
        if config_error is not None or settings is None:
            return config_error or error_response(
                "WORKSPACE_CONFIG_INVALID",
                "Workspace configuration is invalid",
                {"instance_id": instance_id},
            )

        runtime_error = self._ensure_runtime_ready(lease, runtime="docker")
        if runtime_error is not None:
            return runtime_error

        normalized = action.strip().lower()
        if normalized not in DOCKER_ALLOWED_COMPOSE_ACTIONS:
            return error_response(
                "DOCKER_COMMAND_FAILED",
                f"Unsupported compose action '{action}'",
                {"allowed_actions": sorted(DOCKER_ALLOWED_COMPOSE_ACTIONS)},
            )
        if normalized == "exec" and (not services or not command):
            return error_response(
                "DOCKER_COMMAND_FAILED",
                "compose exec requires both services[0] and command",
                {"action": normalized},
            )

        docker_command = self._docker_command_for_lease(lease)
        docker_cmd = self.docker.docker_compose_command(
            action=normalized,
            docker_command=docker_command,
            file=file,
            services=services,
            detach=detach,
            command=command,
            follow=follow,
            since=since,
            tail=tail,
        )
        running_infra_services = self._discover_running_infra_services(
            lease=lease,
            settings=settings,
            docker_command=docker_command,
        )
        connection_env, _ = self._build_connection_env(
            settings=settings,
            services_payload=running_infra_services,
        )
        timeout_seconds = 30 if normalized == "logs" and follow else 1200
        result = self._exec_in_instance(
            lease=lease,
            command=docker_cmd,
            timeout_seconds=timeout_seconds,
            cwd=project_dir,
            env=connection_env or None,
        )
        if result.exit_code != 0:
            return self._docker_command_failed(docker_cmd, result)

        bridge_payload: dict[str, Any] = {"status": "skipped"}
        if normalized in {"up", "restart"} and settings.infra.bridge_to_compose_network:
            bridge_payload, bridge_error = self._bridge_compose_to_infra_network(
                lease=lease,
                settings=settings,
                docker_command=docker_command,
                project_dir=project_dir,
                file=file,
                services=services,
            )
            if bridge_error is not None:
                return bridge_error

        return {
            "instance_id": instance_id,
            "project_dir": project_dir,
            "action": normalized,
            "command": docker_cmd,
            "compose_follow": follow,
            "compose_since": since,
            "compose_tail": tail,
            "bridge": bridge_payload,
            "connection_env": connection_env,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
        }

    def docker_ps(self, instance_id: str, all: bool = False) -> dict[str, Any]:
        lease, error = self._get_lease_for_action(instance_id, require_workspace=True)
        if error is not None or lease is None:
            return error or error_response("INSTANCE_NOT_FOUND", "Instance not found", {"instance_id": instance_id})

        runtime_error = self._ensure_runtime_ready(lease, runtime="docker")
        if runtime_error is not None:
            return runtime_error

        docker_cmd = self.docker.docker_ps_command(
            all_containers=all,
            docker_command=self._docker_command_for_lease(lease),
        )
        result = self._exec_in_instance(lease=lease, command=docker_cmd, timeout_seconds=120)
        if result.exit_code != 0:
            return self._docker_command_failed(docker_cmd, result)
        return {
            "instance_id": instance_id,
            "all": all,
            "command": docker_cmd,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
        }

    def docker_images(self, instance_id: str) -> dict[str, Any]:
        lease, error = self._get_lease_for_action(instance_id, require_workspace=True)
        if error is not None or lease is None:
            return error or error_response("INSTANCE_NOT_FOUND", "Instance not found", {"instance_id": instance_id})

        runtime_error = self._ensure_runtime_ready(lease, runtime="docker")
        if runtime_error is not None:
            return runtime_error

        docker_cmd = self.docker.docker_images_command(docker_command=self._docker_command_for_lease(lease))
        result = self._exec_in_instance(lease=lease, command=docker_cmd, timeout_seconds=120)
        if result.exit_code != 0:
            return self._docker_command_failed(docker_cmd, result)
        return {
            "instance_id": instance_id,
            "command": docker_cmd,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
        }

    def docker_cleanup(self, instance_id: str, mode: str = "safe") -> dict[str, Any]:
        lease, error = self._get_lease_for_action(instance_id, require_workspace=True)
        if error is not None or lease is None:
            return error or error_response("INSTANCE_NOT_FOUND", "Instance not found", {"instance_id": instance_id})

        runtime_error = self._ensure_runtime_ready(lease, runtime="docker")
        if runtime_error is not None:
            return runtime_error

        docker_cmd = self.docker.docker_cleanup_command(
            mode=mode,
            docker_command=self._docker_command_for_lease(lease),
        )
        result = self._exec_in_instance(lease=lease, command=docker_cmd, timeout_seconds=600)
        if result.exit_code != 0:
            return self._docker_command_failed(docker_cmd, result)
        return {
            "instance_id": instance_id,
            "mode": mode,
            "command": docker_cmd,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
        }

    def start_background_task(
        self,
        instance_id: str,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        lease, error = self._get_lease_for_action(instance_id)
        if error is not None or lease is None:
            return error or error_response("INSTANCE_NOT_FOUND", "Instance not found", {"instance_id": instance_id})

        if not self.backend.available:
            return self._backend_unavailable()

        task_id = self._new_task_id()
        created_at = to_iso8601(utc_now())
        task_dir = self._task_dir()
        log_path = task_dir / f"{task_id}.log"
        exit_path = task_dir / f"{task_id}.exit"

        vm_command = self._build_vm_command(command=command, cwd=cwd, env=env)
        try:
            backend_args = self.backend.build_shell_command_args(
                backend_instance_name=str(lease["backend_instance_name"]),
                command=vm_command,
            )
        except BackendUnavailableError:
            return self._backend_unavailable()
        except BackendCommandError as exc:
            return error_response(
                "BACKEND_COMMAND_FAILED",
                "Failed to prepare background task command",
                exc.details(),
            )
        runner = [
            sys.executable,
            "-c",
            (
                "import pathlib, subprocess, sys; "
                "args = sys.argv[1:-1]; "
                "exit_path = pathlib.Path(sys.argv[-1]); "
                "result = subprocess.run(args, check=False); "
                "exit_path.write_text(str(result.returncode), encoding='utf-8')"
            ),
            *backend_args,
            str(exit_path),
        ]

        try:
            with log_path.open("w", encoding="utf-8") as log_file:
                proc = subprocess.Popen(
                    runner,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        except Exception as exc:
            return error_response(
                "BACKEND_COMMAND_FAILED",
                "Failed to start background task",
                {"error": str(exc), "instance_id": instance_id},
            )

        task = {
            "task_id": task_id,
            "instance_id": instance_id,
            "command": command,
            "cwd": cwd,
            "env_json": json.dumps(env or {}),
            "status": "running",
            "pid": proc.pid,
            "created_at": created_at,
            "started_at": created_at,
            "finished_at": None,
            "exit_code": None,
            "log_path": str(log_path),
            "exit_code_path": str(exit_path),
            "error_message": None,
        }
        self.store.create_task(task)
        self.store.update_lease(instance_id, last_used_at=to_iso8601(utc_now()))

        return {
            "task_id": task_id,
            "instance_id": instance_id,
            "status": "running",
            "pid": proc.pid,
            "log_path": str(log_path),
        }

    def get_task_status(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task is None:
            return error_response("TASK_NOT_FOUND", f"Task '{task_id}' was not found", {"task_id": task_id})

        task = self._refresh_task_state(task)
        return {
            "task_id": task["task_id"],
            "instance_id": task["instance_id"],
            "status": task["status"],
            "pid": task["pid"],
            "created_at": task["created_at"],
            "started_at": task["started_at"],
            "finished_at": task["finished_at"],
            "exit_code": task["exit_code"],
            "error_message": task["error_message"],
            "log_path": task["log_path"],
        }

    def get_task_logs(self, task_id: str, tail: int = 500) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task is None:
            return error_response("TASK_NOT_FOUND", f"Task '{task_id}' was not found", {"task_id": task_id})

        task = self._refresh_task_state(task)
        log_path = Path(str(task["log_path"]))
        if not log_path.exists():
            logs = ""
        else:
            lines = log_path.read_text(errors="replace").splitlines()
            logs = "\n".join(lines[-tail:] if tail > 0 else lines)
            if logs:
                logs += "\n"

        return {
            "task_id": task_id,
            "instance_id": task["instance_id"],
            "status": task["status"],
            "logs": logs,
            "tail": tail,
        }

    def stop_task(self, task_id: str, force: bool = False) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task is None:
            return error_response("TASK_NOT_FOUND", f"Task '{task_id}' was not found", {"task_id": task_id})

        task = self._refresh_task_state(task)
        if task.get("status") in TASK_TERMINAL_STATUSES:
            return error_response(
                "TASK_ALREADY_FINISHED",
                f"Task '{task_id}' is already finished",
                {"task_id": task_id, "status": task.get("status")},
            )

        pid = task.get("pid")
        sig = signal.SIGKILL if force else signal.SIGTERM
        if isinstance(pid, int):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass

        exit_code = -9 if force else -15
        self.store.update_task(
            task_id,
            status="stopped",
            finished_at=to_iso8601(utc_now()),
            exit_code=exit_code,
            error_message="Task stopped by user request",
        )
        updated = self.store.get_task(task_id) or task
        return {
            "task_id": task_id,
            "instance_id": updated["instance_id"],
            "status": updated["status"],
            "exit_code": updated["exit_code"],
        }

    def collect_artifacts(self, instance_id: str, remote_paths: list[str], local_dest: str) -> dict[str, Any]:
        lease, error = self._get_lease_for_action(instance_id)
        if error is not None or lease is None:
            return error or error_response("INSTANCE_NOT_FOUND", "Instance not found", {"instance_id": instance_id})

        if not remote_paths:
            return error_response(
                "BACKEND_COMMAND_FAILED",
                "remote_paths cannot be empty",
                {"remote_paths": remote_paths},
            )

        local_dir = Path(local_dest).expanduser().resolve()
        local_dir.mkdir(parents=True, exist_ok=True)

        copied: list[dict[str, str]] = []
        for idx, remote in enumerate(remote_paths):
            name = Path(remote.rstrip("/")).name or f"artifact-{idx}"
            target = local_dir / name
            if target.exists():
                target = local_dir / f"{idx}-{name}"

            try:
                self.backend.copy_from_instance(str(lease["backend_instance_name"]), remote, str(target))
            except BackendCommandError as exc:
                return error_response(
                    "BACKEND_COMMAND_FAILED",
                    "Failed to collect artifact",
                    {
                        "remote_path": remote,
                        "local_path": str(target),
                        **exc.details(),
                    },
                )
            copied.append({"remote_path": remote, "local_path": str(target)})

        self.store.update_lease(instance_id, last_used_at=to_iso8601(utc_now()))
        return {
            "instance_id": instance_id,
            "local_dest": str(local_dir),
            "artifacts": copied,
        }

    def extend_instance_ttl(self, instance_id: str, ttl_minutes: int) -> dict[str, Any]:
        lease, error = self._get_lease_for_action(instance_id)
        if error is not None or lease is None:
            return error or error_response("INSTANCE_NOT_FOUND", "Instance not found", {"instance_id": instance_id})

        if ttl_minutes <= 0 or ttl_minutes > self.config.max_ttl_minutes:
            return self._invalid_ttl(ttl_minutes)

        current_expiry = parse_iso8601(str(lease["expires_at"]))
        base = max(current_expiry, utc_now())
        new_expiry = base + timedelta(minutes=ttl_minutes)

        self.store.update_lease(
            instance_id,
            expires_at=to_iso8601(new_expiry),
            last_used_at=to_iso8601(utc_now()),
        )
        return {
            "instance_id": instance_id,
            "expires_at": to_iso8601(new_expiry),
            "ttl_extended_minutes": ttl_minutes,
        }

    def expire_expired_leases(self) -> dict[str, Any]:
        now_iso = to_iso8601(utc_now())
        expired = self.store.list_expired_active(ACTIVE_STATUSES, now_iso)

        expired_ids: list[str] = []
        errors: list[dict[str, Any]] = []

        for lease in expired:
            instance_id = str(lease["instance_id"])
            backend_instance_name = str(lease["backend_instance_name"])

            if self.backend.available:
                try:
                    self.backend.stop_instance(backend_instance_name=backend_instance_name, force=True)
                    self.backend.delete_instance(backend_instance_name=backend_instance_name, force=True)
                except BackendCommandError as exc:
                    errors.append(
                        error_response(
                            "BACKEND_COMMAND_FAILED",
                            f"Failed to clean up expired instance '{instance_id}'",
                            exc.details(),
                        )
                    )

            self.store.update_lease(instance_id, status="expired", last_used_at=now_iso)
            expired_ids.append(instance_id)

        return {
            "expired_count": len(expired_ids),
            "expired_instance_ids": expired_ids,
            "errors": errors,
        }
