"""Pin the native UI runtime without introducing a Chromium engine."""
import os
import platform
import subprocess
import sys


def requirement(target):
    if target in ('windows-x86-legacy', 'windows-x64-legacy', 'macos-x64-modern'):
        return 'PySide2==5.15.2.1'
    if target == 'windows-arm64-modern':
        return 'PySide6-Essentials==6.10.2'
    if target in ('linux-arm64-modern', 'macos-arm64-modern'):
        return 'PySide6-Essentials==6.7.3'
    return 'PySide6-Essentials==6.8.3'


if __name__ == '__main__':
    subprocess.run([sys.executable, '-m', 'pip', 'install', requirement(os.environ.get('RELEASE_TARGET', ''))], check=True)
