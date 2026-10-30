"""Check every embedded ARM Mach-O deployment target, not just package metadata."""
import argparse
from pathlib import Path
import tempfile

from macholib.MachO import MachO
from macholib.mach_o import LC_BUILD_VERSION, LC_VERSION_MIN_MACOSX
from PyInstaller.archive.readers import CArchiveReader

ARM64 = 0x0100000c
MACHO_MAGIC = {b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xce\xfa\xed\xfe',
               b'\xfe\xed\xfa\xce', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
               b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}


def minimum(path):
    versions = []
    for header in MachO(str(path)).headers:
        if header.header.cputype != ARM64:
            continue
        for load, command, _ in header.commands:
            if load.cmd == LC_BUILD_VERSION:
                versions.append(command.minos)
            elif load.cmd == LC_VERSION_MIN_MACOSX:
                versions.append(command.version)
    if not versions:
        raise ValueError('No ARM64 macOS deployment target: ' + str(path))
    return max(versions)


def verify(executable, major=11):
    ceiling = major << 16
    if minimum(executable) > ceiling:
        raise ValueError('Bootloader requires newer macOS: ' + str(executable))
    archive = CArchiveReader(str(executable))
    checked = 0
    with tempfile.TemporaryDirectory(prefix='tracking-macho-floor-') as directory:
        binary = Path(directory) / 'binary'
        for name, entry in archive.toc.items():
            if entry[-1] != 'b':
                continue
            data = archive.extract(name)
            # Qt hooks also classify resource text as BINARY. Audit native code,
            # while refusing corrupted files named as loadable libraries.
            if data[:4] not in MACHO_MAGIC:
                if name.endswith(('.so', '.dylib')):
                    raise ValueError('Invalid embedded native library: ' + name)
                continue
            binary.write_bytes(data)
            if minimum(binary) > ceiling:
                raise ValueError('Embedded binary requires newer macOS: ' + name)
            checked += 1
    if checked == 0:
        raise ValueError('No embedded native binaries checked')
    print('PASS ARM64 macOS {}.0 floor: {} ({} native binaries)'.format(major, executable.name, checked), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--major', type=int, default=11)
    parser.add_argument('executables', nargs='+', type=Path)
    args = parser.parse_args()
    for executable in args.executables:
        verify(executable, args.major)
