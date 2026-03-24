from __future__ import annotations

import pytest

from lima_mcp_server.config import ServerConfig


@pytest.mark.parametrize("allow_non_loopback, expected_host", [("0", "127.0.0.1"), ("1", "0.0.0.0")])
def test_http_host_non_loopback_requires_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    allow_non_loopback: str,
    expected_host: str,
) -> None:
    monkeypatch.setenv("MCP_HTTP_HOST", "0.0.0.0")
    monkeypatch.setenv("MCP_HTTP_ALLOW_NON_LOOPBACK", allow_non_loopback)

    config = ServerConfig.from_env()
    assert config.http_host == expected_host


def test_http_host_defaults_to_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_HTTP_HOST", raising=False)
    monkeypatch.delenv("MCP_HTTP_ALLOW_NON_LOOPBACK", raising=False)

    config = ServerConfig.from_env()
    assert config.http_host == "127.0.0.1"
