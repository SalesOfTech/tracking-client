import platform
import sys
import sysconfig


def runtime_target():
    os_name={"Windows":"windows","Darwin":"macos","Linux":"linux"}.get(platform.system())
    build_platform=sysconfig.get_platform().lower()
    if os_name=="windows":
        architecture={"win32":"x86","win-amd64":"x64","win-arm64":"arm64"}.get(build_platform)
    else:
        architecture="arm64" if platform.machine().lower() in ("arm64","aarch64") else "x64" if sys.maxsize>2**32 else "x86"
    if os_name is None or architecture is None:
        raise ValueError("Unsupported runtime architecture")
    return os_name,architecture
