from __future__ import annotations

import sys

from ..config import ServerConfig
from .base import Backend
from .hyperv import HyperVBackend
from .lima import LimaBackend


def build_backend(config: ServerConfig) -> Backend:
    requested = str(config.backend or "auto").strip().lower()
    if requested not in {"auto", "lima", "hyperv"}:
        raise ValueError(f"Unsupported SANDBOX_BACKEND '{config.backend}'. Allowed: auto, lima, hyperv")

    selected = requested
    if selected == "auto":
        host_os = sys.platform.lower()
        selected = "hyperv" if host_os.startswith("win") else "lima"

    if selected == "hyperv":
        return HyperVBackend(
            switch_name=config.hyperv_switch_name,
            base_vhdx=config.hyperv_base_vhdx,
            storage_dir=config.hyperv_storage_dir,
            ssh_user=config.hyperv_ssh_user,
            ssh_key_path=config.hyperv_ssh_key_path,
            ssh_port=config.hyperv_ssh_port,
            boot_timeout_seconds=config.hyperv_boot_timeout_seconds,
        )

    return LimaBackend()
