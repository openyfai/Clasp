"""Platform detection helpers for Kronos install/runtime guards."""
from __future__ import annotations

import sys


def is_wsl() -> bool:
    """Return True when running inside Windows Subsystem for Linux."""
    if sys.platform != "linux":
        return False
    try:
        with open("/proc/version", encoding="utf-8") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def is_native_windows() -> bool:
    """Return True for native Windows shells (cmd/PowerShell), not WSL."""
    return sys.platform == "win32"
