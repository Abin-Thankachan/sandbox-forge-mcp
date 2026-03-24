from __future__ import annotations

import os
import sys

import pytest


pytestmark = pytest.mark.skipif(
    not sys.platform.lower().startswith("win") or os.getenv("RUN_HYPERV_INTEGRATION") != "1",
    reason="integration tests require Windows host and RUN_HYPERV_INTEGRATION=1",
)


def test_placeholder_hyperv_integration_gate() -> None:
    assert True
