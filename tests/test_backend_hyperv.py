from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from lima_mcp_server.backend.base import VmCreateSpec
from lima_mcp_server.backend.hyperv import HyperVBackend


class Recorder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, args, capture_output, text, timeout, check):  # noqa: ANN001
        self.calls.append(list(args))
        command = " ".join(args)
        if "Get-Command New-VM" in command or "Get-Command New-VHD" in command:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        if "ConvertTo-Json" in command:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout='[{"name":"vm1","status":"Running","ipAddress":"10.0.0.2","sshPort":22}]',
                stderr="",
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")


def test_hyperv_backend_unavailable_on_non_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lima_mcp_server.backend.hyperv.sys.platform", "linux")
    backend = HyperVBackend(
        switch_name="Default Switch",
        base_vhdx=tmp_path / "base.vhdx",
        storage_dir=tmp_path / "hyperv",
        ssh_user="ubuntu",
        ssh_key_path=None,
    )
    assert backend.available is False
    assert "only Windows is supported" in backend.unavailable_reason


def test_hyperv_backend_requires_base_vhdx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lima_mcp_server.backend.hyperv.sys.platform", "win32")
    monkeypatch.setattr(shutil, "which", lambda _: "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    backend = HyperVBackend(
        switch_name="Default Switch",
        base_vhdx=None,
        storage_dir=tmp_path / "hyperv",
        ssh_user="ubuntu",
        ssh_key_path=None,
    )
    assert backend.available is False
    assert "HYPERV_BASE_VHDX is required" in backend.unavailable_reason


def test_hyperv_create_instance_uses_powershell_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    base_vhdx = tmp_path / "base.vhdx"
    base_vhdx.write_text("base", encoding="utf-8")

    monkeypatch.setattr("lima_mcp_server.backend.hyperv.sys.platform", "win32")
    monkeypatch.setattr(shutil, "which", lambda _: "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    monkeypatch.setattr(subprocess, "run", recorder.run)

    backend = HyperVBackend(
        switch_name="Default Switch",
        base_vhdx=base_vhdx,
        storage_dir=tmp_path / "hyperv",
        ssh_user="ubuntu",
        ssh_key_path=None,
    )
    backend.create_instance(
        "agent-abc",
        VmCreateSpec(cpus=2, memory_gib=4.0, disk_gib=30.0, template="template:docker"),
    )

    commands = [" ".join(call) for call in recorder.calls]
    assert any("New-VHD" in cmd for cmd in commands)
    assert any("New-VM" in cmd for cmd in commands)
    assert any("Set-VM" in cmd for cmd in commands)


def test_hyperv_shell_and_copy_use_ssh_and_scp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    base_vhdx = tmp_path / "base.vhdx"
    base_vhdx.write_text("base", encoding="utf-8")

    def fake_which(binary: str) -> str | None:
        mapping = {
            "powershell.exe": "powershell.exe",
            "powershell": "powershell.exe",
            "pwsh": "powershell.exe",
            "ssh": "ssh",
            "scp": "scp",
        }
        return mapping.get(binary)

    monkeypatch.setattr("lima_mcp_server.backend.hyperv.sys.platform", "win32")
    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(subprocess, "run", recorder.run)

    backend = HyperVBackend(
        switch_name="Default Switch",
        base_vhdx=base_vhdx,
        storage_dir=tmp_path / "hyperv",
        ssh_user="ubuntu",
        ssh_key_path=None,
    )
    monkeypatch.setattr(backend, "_wait_for_ipv4", lambda backend_instance_name, timeout_seconds=None: "10.0.0.4")

    backend.shell_command("agent-abc", "echo hi", timeout_seconds=10)
    backend.copy_to_instance("agent-abc", "local.txt", "/tmp/remote.txt")
    backend.copy_from_instance("agent-abc", "/tmp/remote.txt", "out.txt")

    assert any(call and call[0] == "ssh" for call in recorder.calls)
    assert sum(1 for call in recorder.calls if call and call[0] == "scp") >= 2


def test_hyperv_list_instances_parses_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    base_vhdx = tmp_path / "base.vhdx"
    base_vhdx.write_text("base", encoding="utf-8")

    def fake_which(binary: str) -> str | None:
        mapping = {
            "powershell.exe": "powershell.exe",
            "powershell": "powershell.exe",
            "pwsh": "powershell.exe",
            "ssh": "ssh",
            "scp": "scp",
        }
        return mapping.get(binary)

    monkeypatch.setattr("lima_mcp_server.backend.hyperv.sys.platform", "win32")
    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(subprocess, "run", recorder.run)

    backend = HyperVBackend(
        switch_name="Default Switch",
        base_vhdx=base_vhdx,
        storage_dir=tmp_path / "hyperv",
        ssh_user="ubuntu",
        ssh_key_path=None,
    )

    rows = backend.list_instances()
    assert rows
    assert rows[0]["name"] == "vm1"
