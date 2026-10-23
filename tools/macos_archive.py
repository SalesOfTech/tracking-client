"""Validate embedded Python frameworks without relying on a Gatekeeper exception."""
import plistlib
import re
import struct
import subprocess
from xml.parsers.expat import ExpatError


def verify_python_archive(executable):
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(executable))
    # The pinned PyInstaller reader discards the library name after parsing its
    # cookie; use the same cookie format to check the bootloader's actual target.
    with open(executable, 'rb') as stream:
        stream.seek(archive._end_offset - archive._COOKIE_LENGTH)
        cookie = struct.unpack(archive._COOKIE_FORMAT, stream.read(archive._COOKIE_LENGTH))
    library = cookie[-1].rstrip(b'\0').decode('ascii')
    prefix = 'Python.framework/'
    entries = {name[len(prefix):]: value[-1] for name, value in archive.toc.items()
               if name.startswith(prefix)}
    if not entries and 'Python.framework' not in archive.toc:
        # GitHub's newer Intel Python builds use a standalone shared library,
        # while the framework-based Apple Silicon distribution uses aliases.
        if (not re.fullmatch(r'libpython[0-9]+\.[0-9]+\.dylib', library)
                or archive.toc.get(library, (None,))[-1] != 'b'):
            raise ValueError('Missing embedded macOS Python library')
        binary = archive.extract(library)
        if len(binary) < 32 or binary[:4] not in (b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
            raise ValueError('Invalid embedded macOS Python library')
        return
    if library != 'Python':
        raise ValueError('Python.framework bootloader library mismatch')
    # PyInstaller onefile restores these aliases before loading Python. Flattening
    # them produces an invalid versioned framework, even if each Mach-O is signed.
    aliases = {'Python': 'Versions/Current/Python',
               'Resources': 'Versions/Current/Resources'}
    if entries.get('Versions/Current') != 'n':
        raise ValueError('Python.framework is missing its version symlink')
    version = archive.extract(prefix + 'Versions/Current').decode('utf-8').rstrip('\0')
    if not re.fullmatch(r'[0-9]+\.[0-9]+', version):
        raise ValueError('Invalid Python.framework version symlink')
    aliases['Versions/Current'] = version
    for name, target in aliases.items():
        if entries.get(name) != 'n' or archive.extract(prefix + name) != target.encode() + b'\0':
            raise ValueError('Invalid Python.framework alias: ' + name)
    version_prefix = 'Versions/' + version + '/'
    if entries.get(version_prefix + 'Python') != 'b':
        raise ValueError('Missing embedded Python library')
    if entries.get(version_prefix + 'Resources/Info.plist') != 'x':
        raise ValueError('Missing embedded Python framework metadata')
    # EXE stores the library basename in its cookie, so the bootloader loads
    # _MEIPASS/Python, not the versioned path directly.
    if (archive.toc.get('Python', (None,))[-1] != 'n' or
            archive.extract('Python') != (prefix + version_prefix + 'Python').encode() + b'\0'):
        raise ValueError('Invalid Python.framework bootloader alias: Python')
    try:
        metadata = plistlib.loads(archive.extract(prefix + version_prefix + 'Resources/Info.plist'))
    except (ValueError, plistlib.InvalidFileException, ExpatError) as error:
        raise ValueError('Invalid embedded Python framework metadata') from error
    if not isinstance(metadata, dict) or metadata.get('CFBundleExecutable') != 'Python':
        raise ValueError('Invalid embedded Python framework executable metadata')
    if 'Python.framework' in archive.toc or any(name.startswith('Python/') for name in archive.toc):
        raise ValueError('Python.framework aliases collide with archive entries')
    if any('\\' in name or any(part in ('', '.', '..') for part in name.split('/')) for name in entries):
        raise ValueError('Unsafe Python.framework archive path')
    if any(name not in aliases and not name.startswith(version_prefix) for name in entries):
        raise ValueError('Python.framework contains flattened or unexpected entries')


def verify_frozen(executable, bundle=None):
    verify_python_archive(executable)
    # Ad-hoc signature integrity is necessary, but is NOT Apple notarization.
    subprocess.run(['codesign', '--verify', '--strict', str(executable)], check=True)
    if bundle is not None:
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(bundle)], check=True)
