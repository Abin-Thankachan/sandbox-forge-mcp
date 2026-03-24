from __future__ import annotations

from pathlib import Path

import pytest

from lima_mcp_server.backend.factory import build_backend
from lima_mcp_server.config import ServerConfig


class DummyBackend:
    def __init__(self, name: str) -> None:
        self.backend_name = name
        self.available = True
        self.version = "dummy"
        self.unavailable_reason = ""


def test_backend_factory_auto_selects_lima_on_non_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("lima_mcp_server.backend.factory.sys.platform", "darwin")
    monkeypatch.setattr("lima_mcp_server.backend.factory.LimaBackend", lambda: DummyBackend("lima"))

    cfg = ServerConfig(db_path=tmp_path / "leases.db", backend="auto")
    backend = build_backend(cfg)
    assert backend.backend_name == "lima"


def test_backend_factory_auto_selects_hyperv_on_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("lima_mcp_server.backend.factory.sys.platform", "win32")
    monkeypatch.setattr(
        "lima_mcp_server.backend.factory.HyperVBackend",
        lambda **kwargs: DummyBackend("hyperv"),  # noqa: ARG005
    )

    cfg = ServerConfig(
        db_path=tmp_path / "leases.db",
        backend="auto",
        hyperv_base_vhdx=tmp_path / "base.vhdx",
    )
    backend = build_backend(cfg)
    assert backend.backend_name == "hyperv"


def test_backend_factory_rejects_unknown_backend(tmp_path: Path) -> None:
    cfg = ServerConfig(db_path=tmp_path / "leases.db", backend="unknown")
    with pytest.raises(ValueError):
        build_backend(cfg)
