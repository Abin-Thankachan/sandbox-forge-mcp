from __future__ import annotations

import os
import shutil

import pytest


pytestmark = pytest.mark.skipif(
    shutil.which("limactl") is None or os.getenv("RUN_LIMA_INTEGRATION") != "1",
    reason="integration tests require limactl and RUN_LIMA_INTEGRATION=1",
)


def test_placeholder_integration_gate() -> None:
    assert True
