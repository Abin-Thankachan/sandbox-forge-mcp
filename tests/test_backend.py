from __future__ import annotations

import shutil
import subprocess

import pytest

from lima_mcp_server.backend.lima import LimaBackend, VmCreateSpec


class Recorder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, args, capture_output, text, timeout, check):  # noqa: ANN001
        self.calls.append(list(args))
        if args == ["limactl", "--version"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="limactl 1.0.0\n", stderr="")
        if args[:3] == ["limactl", "list", "--format"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")
        if args[:2] == ["limactl", "shell"]:
            return subprocess.CompletedProcess(args=args, returncode=7, stdout="hello\n", stderr="boom\n")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")


def test_copy_command_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/limactl")
    monkeypatch.setattr(subprocess, "run", recorder.run)

    backend = LimaBackend()
    backend.copy_to_instance("agent-abc", "/tmp/local.txt", "/remote/file.txt")
    backend.copy_from_instance("agent-abc", "/remote/file.txt", "/tmp/out.txt")

    assert ["limactl", "copy", "/tmp/local.txt", "agent-abc:/remote/file.txt"] in recorder.calls
    assert ["limactl", "copy", "agent-abc:/remote/file.txt", "/tmp/out.txt"] in recorder.calls


def test_shell_exit_code_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/limactl")
    monkeypatch.setattr(subprocess, "run", recorder.run)

    backend = LimaBackend()
    result = backend.shell_command("agent-abc", "echo hello", timeout_seconds=30)

    assert result.exit_code == 7
    assert result.stdout == "hello\n"
    assert result.stderr == "boom\n"


def test_create_instance_uses_vm_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/limactl")
    monkeypatch.setattr(subprocess, "run", recorder.run)

    backend = LimaBackend()
    backend.create_instance(
        "agent-abc",
        VmCreateSpec(
            cpus=2,
            memory_gib=4.0,
            disk_gib=30.0,
            template="template:docker",
            arch="aarch64",
            vm_type="vz",
        ),
    )

    create_calls = [call for call in recorder.calls if call[:2] == ["limactl", "create"]]
    assert create_calls
    create_cmd = create_calls[0]
    assert "--cpus=2" in create_cmd
    assert "--memory=4.0" in create_cmd
    assert "--disk=30.0" in create_cmd
    assert "--arch=aarch64" in create_cmd
    assert "--vm-type=vz" in create_cmd
    assert create_cmd[-1] == "template:docker"


def test_backend_unavailable_when_limactl_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: None)
    backend = LimaBackend()

    assert backend.available is False
    assert "limactl not found" in backend.unavailable_reason
