from __future__ import annotations

from pathlib import Path

import pytest

from lima_mcp_server.workspace_config import WorkspaceConfigError, resolve_workspace_settings


def test_workspace_config_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    global_cfg = fake_home / ".config" / "lima-mcp" / "config.toml"
    global_cfg.parent.mkdir(parents=True, exist_ok=True)
    global_cfg.write_text(
        "\n".join(
            [
                "[vm]",
                "disk_gib = 20",
                "",
                "[build]",
                "min_free_gib = 2.5",
            ]
        )
    )

    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / ".lima-mcp.toml").write_text(
        "\n".join(
            [
                "[vm]",
                "disk_gib = 25",
                "cpus = 2",
                "",
                "[build]",
                "min_free_gib = 4.0",
            ]
        )
    )

    settings = resolve_workspace_settings(
        workspace_root=str(workspace),
        overrides={"vm": {"disk_gib": 40}},
    )
    assert settings.vm.cpus == 2
    assert settings.vm.disk_gib == 40
    assert settings.build.min_free_gib == 4.0
    assert "request_overrides" in settings.sources


def test_workspace_config_rejects_unknown_key(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / ".lima-mcp.toml").write_text(
        "\n".join(
            [
                "[vm]",
                "cpus = 2",
                "unknown = 1",
            ]
        )
    )

    with pytest.raises(WorkspaceConfigError) as exc:
        resolve_workspace_settings(workspace_root=str(workspace))

    assert any(err.startswith("vm.unknown is not supported") for err in exc.value.errors)


def test_workspace_config_accepts_infra_env_extensions(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / ".lima-mcp.toml").write_text(
        "\n".join(
            [
                "[infra]",
                "bridge_to_compose_network = true",
                "",
                "[infra.mysql]",
                "inject_env_to = \".env\"",
                "extra_env = { TZ = \"UTC\" }",
                "",
                "[infra.redis]",
                "inject_env_to = \".env\"",
                "extra_env = { TZ = \"UTC\" }",
            ]
        )
    )

    settings = resolve_workspace_settings(workspace_root=str(workspace))

    assert settings.infra.bridge_to_compose_network is True
    assert settings.infra.mysql.inject_env_to == ".env"
    assert settings.infra.mysql.extra_env["TZ"] == "UTC"
    assert settings.infra.redis.inject_env_to == ".env"
    assert settings.infra.redis.extra_env["TZ"] == "UTC"


def test_workspace_config_accepts_build_cache_and_staleness_settings(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / ".lima-mcp.toml").write_text(
        "\n".join(
            [
                "[build.prebuilt]",
                "enabled = true",
                "validation = \"strict\"",
                "",
                "[build.prebuilt.staleness_check]",
                "check_git_commit = true",
                "check_dependencies = true",
                "check_age_threshold = \"12h\"",
                "on_stale = \"rebuild\"",
                "",
                "[build.image_caching]",
                "enabled = true",
                "strategy = \"content_hash\"",
                "tag_format = \"{image}:{content_hash}\"",
                "",
                "[build.staleness_detection]",
                "check_git_commit = true",
                "check_git_dirty = true",
                "check_dependencies = [\"pyproject.toml\", \"uv.lock\"]",
                "check_dockerfile = true",
                "max_age_threshold = \"36h\"",
                "on_stale = \"rebuild\"",
                "on_dirty_workspace = \"warn_and_rebuild\"",
                "on_missing = \"build\"",
            ]
        )
    )

    settings = resolve_workspace_settings(workspace_root=str(workspace))
    assert settings.build.image_caching.strategy == "content_hash"
    assert settings.build.prebuilt.staleness_check.check_age_threshold_hours == 12.0
    assert settings.build.staleness_detection.max_age_threshold_hours == 36.0


def test_workspace_config_supports_explicit_network_name(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / ".lima-mcp.toml").write_text(
        "\n".join(
            [
                "[infra]",
                "network_name = \"shopify-network\"",
            ]
        )
    )

    settings = resolve_workspace_settings(workspace_root=str(workspace))
    assert settings.infra.network_name == "shopify-network"


def test_workspace_config_supports_new_sandboxforge_file_and_takes_precedence(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / ".lima-mcp.toml").write_text(
        "\n".join(
            [
                "[vm]",
                "cpus = 2",
            ]
        )
    )
    (workspace / ".sandboxforge.toml").write_text(
        "\n".join(
            [
                "[vm]",
                "cpus = 3",
            ]
        )
    )

    settings = resolve_workspace_settings(workspace_root=str(workspace))
    assert settings.vm.cpus == 3


def test_workspace_config_supports_new_global_path_and_takes_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    legacy_global = fake_home / ".config" / "lima-mcp" / "config.toml"
    new_global = fake_home / ".config" / "sandboxforge-mcp" / "config.toml"
    legacy_global.parent.mkdir(parents=True, exist_ok=True)
    new_global.parent.mkdir(parents=True, exist_ok=True)
    legacy_global.write_text(
        "\n".join(
            [
                "[vm]",
                "cpus = 2",
            ]
        )
    )
    new_global.write_text(
        "\n".join(
            [
                "[vm]",
                "cpus = 3",
            ]
        )
    )

    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    settings = resolve_workspace_settings(workspace_root=str(workspace))
    assert settings.vm.cpus == 3


def test_workspace_config_default_vm_type_is_vz_on_macos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("lima_mcp_server.workspace_config.sys.platform", "darwin")

    settings = resolve_workspace_settings(workspace_root=str(workspace))

    assert settings.vm.vm_type == "vz"


def test_workspace_config_default_vm_type_is_qemu_on_linux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("lima_mcp_server.workspace_config.sys.platform", "linux")

    settings = resolve_workspace_settings(workspace_root=str(workspace))

    assert settings.vm.vm_type == "qemu"


def test_workspace_config_default_vm_type_is_none_on_unsupported_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("lima_mcp_server.workspace_config.sys.platform", "win32")

    settings = resolve_workspace_settings(workspace_root=str(workspace))

    assert settings.vm.vm_type is None


def test_workspace_config_explicit_vm_type_override_is_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("lima_mcp_server.workspace_config.sys.platform", "linux")
    (workspace / ".lima-mcp.toml").write_text(
        "\n".join(
            [
                "[vm]",
                "vm_type = \"vz\"",
            ]
        )
    )

    settings = resolve_workspace_settings(workspace_root=str(workspace))

    assert settings.vm.vm_type == "vz"


def test_workspace_config_rejects_non_windows_path_on_windows_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("lima_mcp_server.workspace_config.sys.platform", "win32")
    monkeypatch.setattr("lima_mcp_server.workspace_config.os.name", "nt")

    with pytest.raises(WorkspaceConfigError) as exc:
        resolve_workspace_settings(workspace_root=str(tmp_path))

    assert any("must be a Windows path" in err for err in exc.value.errors)
