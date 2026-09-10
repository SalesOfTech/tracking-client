from __future__ import annotations

import platform

from .base import PlatformAdapter


def load_platform_adapter() -> PlatformAdapter:
    system = platform.system().lower()
    if system == "windows":
        from .windows import WindowsPlatform

        return WindowsPlatform()
    if system == "darwin":
        from .macos import MacOSPlatform

        return MacOSPlatform()
    if system == "linux":
        from .linux import LinuxPlatform

        return LinuxPlatform()
    raise RuntimeError(f"Unsupported platform: {system}")
