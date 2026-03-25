from __future__ import annotations

import shutil
import subprocess

import pytest

from lima_mcp_server.backend.base import BackendCommandError
from lima_mcp_server.backend.lima import LimaBackend, VmCreateSpec, _parse_limactl_list_json


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


def test_parse_limactl_list_json_single_array() -> None:
    out = _parse_limactl_list_json('[{"name": "a"}, {"name": "b"}]')
    assert [x.get("name") for x in out] == ["a", "b"]


def test_parse_limactl_list_json_ndjson_lines() -> None:
    ndjson = '{"name":"vm1","status":"Running"}\n{"name":"vm2","status":"Stopped"}\n'
    out = _parse_limactl_list_json(ndjson)
    assert [x.get("name") for x in out] == ["vm1", "vm2"]


def test_parse_limactl_list_json_wrapped_instances_key() -> None:
    out = _parse_limactl_list_json('{"instances": [{"name": "x"}]}')
    assert out == [{"name": "x"}]


def test_parse_limactl_list_json_raises_on_partial_malformed_ndjson() -> None:
    ndjson = '{"name":"vm1"}\n{"name":"vm2"\n'
    with pytest.raises(ValueError):
        _parse_limactl_list_json(ndjson)


def test_list_instances_parses_ndjson_output(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()

    def fake_run(args, capture_output, text, timeout, check):  # noqa: ANN001
        recorder.calls.append(list(args))
        if args == ["limactl", "--version"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="limactl 2.1.0\n", stderr="")
        if args[:3] == ["limactl", "list", "--format"]:
            stdout = '{"name":"vm1","status":"Running"}\n{"name":"vm2","status":"Stopped"}\n'
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/limactl")
    monkeypatch.setattr(subprocess, "run", fake_run)

    backend = LimaBackend()
    out = backend.list_instances()
    assert [item.get("name") for item in out] == ["vm1", "vm2"]


def test_list_instances_raises_on_malformed_ndjson(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args, capture_output, text, timeout, check):  # noqa: ANN001
        if args == ["limactl", "--version"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="limactl 2.1.0\n", stderr="")
        if args[:3] == ["limactl", "list", "--format"]:
            stdout = '{"name":"vm1"}\n{"name":"vm2"\n'
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/limactl")
    monkeypatch.setattr(subprocess, "run", fake_run)

    backend = LimaBackend()
    with pytest.raises(BackendCommandError, match="Failed to parse limactl list JSON"):
        backend.list_instances()


def test_backend_unavailable_when_limactl_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: None)
    backend = LimaBackend()

    assert backend.available is False
    assert "limactl not found" in backend.unavailable_reason


def test_backend_unavailable_on_unsupported_host_without_limactl_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, capture_output, text, timeout, check):  # noqa: ANN001
        calls.append(list(args))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("lima_mcp_server.backend.lima.sys.platform", "win32")
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/limactl")
    monkeypatch.setattr(subprocess, "run", fake_run)

    backend = LimaBackend()

    assert backend.available is False
    assert "unsupported host os" in backend.unavailable_reason.lower()
    assert calls == []
