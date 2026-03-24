from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .base import BackendCommandError, BackendUnavailableError, CommandResult, VmCreateSpec


_IPV4_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class HyperVBackend:
    backend_name = "hyperv"

    def __init__(
        self,
        switch_name: str,
        base_vhdx: Path | None,
        storage_dir: Path,
        ssh_user: str,
        ssh_key_path: Path | None,
        ssh_port: int = 22,
        boot_timeout_seconds: int = 180,
    ) -> None:
        self.switch_name = switch_name
        self.base_vhdx = base_vhdx
        self.storage_dir = storage_dir
        self.ssh_user = ssh_user
        self.ssh_key_path = ssh_key_path
        self.ssh_port = ssh_port
        self.boot_timeout_seconds = max(boot_timeout_seconds, 10)

        self.available = False
        self.version = ""
        self.unavailable_reason = "backend not initialized"

        self._powershell = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")
        self._ssh_bin = shutil.which("ssh")
        self._scp_bin = shutil.which("scp")

        self._preflight()

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

    def _run_powershell(self, script: str, timeout: int | None = None, check: bool = True) -> CommandResult:
        if not self._powershell:
            raise BackendUnavailableError("powershell executable not found in PATH")
        args = [self._powershell, "-NoProfile", "-NonInteractive", "-Command", script]
        return self._run(args=args, timeout=timeout, check=check)

    def _ensure_available(self) -> None:
        if not self.available:
            raise BackendUnavailableError(self.unavailable_reason)

    def _preflight(self) -> None:
        host_os = sys.platform.lower()
        if not host_os.startswith("win"):
            self.available = False
            self.unavailable_reason = f"unsupported host OS '{host_os}'; only Windows is supported"
            return

        if not self._powershell:
            self.available = False
            self.unavailable_reason = "powershell executable not found in PATH"
            return

        if not self._ssh_bin or not self._scp_bin:
            self.available = False
            self.unavailable_reason = "ssh/scp not found in PATH"
            return

        if self.base_vhdx is None:
            self.available = False
            self.unavailable_reason = "HYPERV_BASE_VHDX is required for hyperv backend"
            return

        if not self.base_vhdx.exists():
            self.available = False
            self.unavailable_reason = f"base VHDX not found: {self.base_vhdx}"
            return

        try:
            self._run_powershell("Get-Command New-VM | Out-Null", timeout=10, check=True)
            self._run_powershell("Get-Command New-VHD | Out-Null", timeout=10, check=True)
        except BackendCommandError as exc:
            self.available = False
            self.unavailable_reason = exc.stderr.strip() or "hyper-v PowerShell commands are unavailable"
            return

        self.available = True
        self.version = "Hyper-V (PowerShell)"
        self.unavailable_reason = ""

    def _ip_lookup_script(self, backend_instance_name: str) -> str:
        return (
            "$ips = (Get-VMNetworkAdapter -VMName "
            + _ps_quote(backend_instance_name)
            + " -ErrorAction SilentlyContinue | Select-Object -ExpandProperty IPAddresses -ErrorAction SilentlyContinue); "
            + "$ipv4 = $ips | Where-Object { $_ -match '^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$' } | Select-Object -First 1; "
            + "if ($ipv4) { Write-Output $ipv4 }"
        )

    def _lookup_ipv4(self, backend_instance_name: str) -> str | None:
        result = self._run_powershell(self._ip_lookup_script(backend_instance_name), timeout=20, check=False)
        if result.exit_code != 0:
            return None
        for line in result.stdout.splitlines():
            value = line.strip()
            if _IPV4_RE.match(value):
                return value
        return None

    def _wait_for_ipv4(self, backend_instance_name: str, timeout_seconds: int | None = None) -> str:
        deadline = time.monotonic() + (timeout_seconds or self.boot_timeout_seconds)
        while time.monotonic() < deadline:
            ip = self._lookup_ipv4(backend_instance_name)
            if ip:
                return ip
            time.sleep(2)
        raise BackendCommandError(
            command=["Get-VMNetworkAdapter", backend_instance_name],
            exit_code=1,
            stdout="",
            stderr=f"Timed out waiting for IPv4 address for VM '{backend_instance_name}'",
            duration_ms=int((timeout_seconds or self.boot_timeout_seconds) * 1000),
        )

    def _ssh_base_args(self, host: str) -> list[str]:
        if not self._ssh_bin:
            raise BackendUnavailableError("ssh binary is unavailable")

        args = [
            self._ssh_bin,
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-p",
            str(self.ssh_port),
        ]
        if self.ssh_key_path:
            args.extend(["-i", str(self.ssh_key_path)])
        args.append(f"{self.ssh_user}@{host}")
        return args

    def _scp_base_args(self) -> list[str]:
        if not self._scp_bin:
            raise BackendUnavailableError("scp binary is unavailable")

        args = [
            self._scp_bin,
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-P",
            str(self.ssh_port),
        ]
        if self.ssh_key_path:
            args.extend(["-i", str(self.ssh_key_path)])
        return args

    def create_instance(self, backend_instance_name: str, vm_spec: VmCreateSpec, timeout_seconds: int = 600) -> CommandResult:
        self._ensure_available()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        disk_path = self.storage_dir / f"{backend_instance_name}.vhdx"
        memory_bytes = int(vm_spec.memory_gib * (1024**3))

        script = "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                f"$name = {_ps_quote(backend_instance_name)}",
                f"$switch = {_ps_quote(self.switch_name)}",
                f"$base = {_ps_quote(str(self.base_vhdx))}",
                f"$disk = {_ps_quote(str(disk_path))}",
                "if (Get-VM -Name $name -ErrorAction SilentlyContinue) { throw \"VM already exists: $name\" }",
                "if (!(Test-Path -LiteralPath $base)) { throw \"Base VHDX not found: $base\" }",
                "New-VHD -Path $disk -ParentPath $base -Differencing | Out-Null",
                f"New-VM -Name $name -Generation 2 -MemoryStartupBytes {memory_bytes} -VHDPath $disk -SwitchName $switch | Out-Null",
                f"Set-VM -Name $name -ProcessorCount {vm_spec.cpus} | Out-Null",
                "Set-VM -Name $name -AutomaticCheckpointsEnabled $false | Out-Null",
                "Write-Output 'created'",
            ]
        )
        return self._run_powershell(script, timeout=timeout_seconds, check=True)

    def start_instance(self, backend_instance_name: str, timeout_seconds: int = 600) -> CommandResult:
        self._ensure_available()
        result = self._run_powershell(
            f"Start-VM -Name {_ps_quote(backend_instance_name)} | Out-Null; Write-Output 'started'",
            timeout=timeout_seconds,
            check=True,
        )
        self._wait_for_ipv4(backend_instance_name, timeout_seconds=self.boot_timeout_seconds)
        return result

    def list_instances(self) -> list[dict[str, Any]]:
        self._ensure_available()
        script = (
            "$rows = @(); "
            f"$sshPort = {self.ssh_port}; "
            "Get-VM | ForEach-Object { "
            "$name = $_.Name; "
            "$state = [string]$_.State; "
            "$ips = (Get-VMNetworkAdapter -VMName $name -ErrorAction SilentlyContinue | Select-Object -ExpandProperty IPAddresses -ErrorAction SilentlyContinue); "
            "$ipv4 = $ips | Where-Object { $_ -match '^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$' } | Select-Object -First 1; "
            "$rows += [pscustomobject]@{ name = $name; status = $state; ipAddress = $ipv4; sshPort = $sshPort } "
            "}; "
            "$rows | ConvertTo-Json -Depth 4"
        )
        result = self._run_powershell(script, timeout=30, check=True)
        if not result.stdout.strip():
            return []

        try:
            parsed = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise BackendCommandError(
                command=result.args,
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=result.duration_ms,
                message=f"Failed to parse Hyper-V list JSON: {exc}",
            ) from exc

        if isinstance(parsed, dict):
            return [parsed]
        if isinstance(parsed, list):
            return parsed
        return []

    def build_shell_command_args(self, backend_instance_name: str, command: str) -> list[str]:
        self._ensure_available()
        host = self._wait_for_ipv4(backend_instance_name)
        return [*self._ssh_base_args(host), "sh", "-lc", command]

    def shell_command(self, backend_instance_name: str, command: str, timeout_seconds: int) -> CommandResult:
        self._ensure_available()
        args = self.build_shell_command_args(backend_instance_name=backend_instance_name, command=command)
        return self._run(args=args, timeout=timeout_seconds, check=False)

    def copy_to_instance(self, backend_instance_name: str, local_path: str, remote_path: str) -> CommandResult:
        self._ensure_available()
        host = self._wait_for_ipv4(backend_instance_name)
        target = f"{self.ssh_user}@{host}:{remote_path}"
        args = [*self._scp_base_args(), local_path, target]
        return self._run(args=args, timeout=300, check=True)

    def copy_from_instance(self, backend_instance_name: str, remote_path: str, local_path: str) -> CommandResult:
        self._ensure_available()
        host = self._wait_for_ipv4(backend_instance_name)
        source = f"{self.ssh_user}@{host}:{remote_path}"
        args = [*self._scp_base_args(), source, local_path]
        return self._run(args=args, timeout=300, check=True)

    def stop_instance(self, backend_instance_name: str, force: bool = False, timeout_seconds: int = 300) -> CommandResult:
        self._ensure_available()
        force_literal = "$true" if force else "$false"
        script = (
            "$ErrorActionPreference = 'Stop'; "
            f"$name = {_ps_quote(backend_instance_name)}; "
            "$vm = Get-VM -Name $name -ErrorAction SilentlyContinue; "
            "if ($vm) { "
            f"Stop-VM -Name $name -Force:{force_literal} -TurnOff:$true -ErrorAction SilentlyContinue | Out-Null "
            "}; "
            "Write-Output 'stopped'"
        )
        return self._run_powershell(script, timeout=timeout_seconds, check=True)

    def delete_instance(self, backend_instance_name: str, force: bool = False, timeout_seconds: int = 300) -> CommandResult:
        self._ensure_available()
        disk_path = self.storage_dir / f"{backend_instance_name}.vhdx"
        force_literal = "$true" if force else "$false"
        script = (
            "$ErrorActionPreference = 'Stop'; "
            f"$name = {_ps_quote(backend_instance_name)}; "
            f"$disk = {_ps_quote(str(disk_path))}; "
            "$vm = Get-VM -Name $name -ErrorAction SilentlyContinue; "
            "if ($vm) { "
            f"Stop-VM -Name $name -Force:{force_literal} -TurnOff:$true -ErrorAction SilentlyContinue | Out-Null; "
            "Remove-VM -Name $name -Force | Out-Null "
            "}; "
            "if (Test-Path -LiteralPath $disk) { Remove-Item -LiteralPath $disk -Force }; "
            "Write-Output 'deleted'"
        )
        return self._run_powershell(script, timeout=timeout_seconds, check=True)

    def extract_ssh_port(self, instance: dict[str, Any]) -> int | None:
        value = instance.get("sshPort") or instance.get("ssh_port")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return self.ssh_port
