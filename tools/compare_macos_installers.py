"""Compare exact published initial installers on clean ARM CI, without enrollment."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import struct
import subprocess
import tempfile
import time
import uuid
import zipfile

import psutil
from PyInstaller.archive.readers import CArchiveReader

from diagnose_macos import PINS, TARGET, download


def command(*args, timeout=60):
    try:
        result = subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=timeout)
        return {'code': result.returncode, 'stdout': result.stdout[-8000:], 'stderr': result.stderr[-8000:]}
    except subprocess.TimeoutExpired:
        return {'timeout': True}


def extract_installer(bundle, version, destination):
    with zipfile.ZipFile(bundle) as archive:
        metadata = json.loads(archive.read(TARGET + '/setup.json'))
        name = metadata['file']
        if name not in ('SOFT-Tracking-Setup.dmg', 'SOFT-Tracking-Setup-' + version + '.dmg'):
            raise ValueError('Unexpected installer filename')
        digest = hashlib.sha256()
        with archive.open(TARGET + '/' + name) as source, destination.open('xb') as output:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(block)
                output.write(block)
        if digest.hexdigest() != metadata['sha256']:
            raise ValueError('Installer digest mismatch')
        return metadata


def frozen_metadata(executable):
    archive = CArchiveReader(str(executable))
    with executable.open('rb') as stream:
        stream.seek(archive._end_offset - archive._COOKIE_LENGTH)
        cookie = struct.unpack(archive._COOKIE_FORMAT, stream.read(archive._COOKIE_LENGTH))
    entries = {}
    for name, item in archive.toc.items():
        if name.startswith('Python.framework/') or name in ('Python', 'libpython3.11.dylib'):
            content = archive.extract(name)
            entries[name] = {'type': item[-1], 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
            if item[-1] == 'n':
                entries[name]['target'] = content.decode().rstrip('\0')
    return {'python_version': cookie[-2], 'python_library': cookie[-1].rstrip(b'\0').decode(),
            'python_entries': entries}


def launch(app, method, root, evidence):
    metadata = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    executable = app / 'Contents/MacOS' / metadata['CFBundleExecutable']
    marker = root / (method + '-ready.json')
    home = root / (method + '-home')
    (home / 'Library/Caches').mkdir(parents=True)
    temporary = root / (method + '-temp')
    temporary.mkdir()
    env = dict(os.environ, HOME=str(home), TMPDIR=str(temporary) + '/',
               SOFT_TRACKING_HEALTH=str(marker), PYTHONNOUSERSITE='1')
    args = [str(executable)]
    if method == 'launchservices':
        args = ['open', '-n', '-W', '--env', 'SOFT_TRACKING_HEALTH=' + str(marker),
                '--env', 'HOME=' + str(home), '--env', 'TMPDIR=' + str(temporary) + '/',
                '--stdout', str(root / 'app-stdout.log'), '--stderr', str(root / 'app-stderr.log'),
                '-a', str(app)]
    outcome = {'method': method, 'normal_gui_ready': False}
    with (root / (method + '.log')).open('wb') as log:
        child = subprocess.Popen(args, env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if marker.exists():
                    try:
                        ready = json.loads(marker.read_text())
                    except (ValueError, OSError):
                        ready = {}
                    if ready.get('ready') is True and ready.get('ui') == 'electron':
                        normal = []
                        for process in psutil.process_iter():
                            try:
                                process_env = process.environ()
                                if (process_env.get('SOFT_TRACKING_HEALTH') == str(marker)
                                        and process_env.get('SOFT_TRACKING_UI_MODE') == 'installer'
                                        and process_env.get('SOFT_TRACKING_UI_HEALTH') == '0'):
                                    normal.append(process.pid)
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                pass
                        outcome['normal_gui_ready'] = bool(normal)
                        if normal:
                            break
                if child.poll() is not None:
                    break
                time.sleep(0.25)
            outcome['exit_code_before_cleanup'] = child.poll()
            command('screencapture', '-x', evidence / (method + '.png'))
        finally:
            owned = []
            for process in psutil.process_iter():
                try:
                    if process.environ().get('SOFT_TRACKING_HEALTH') == str(marker) or process.pid == child.pid:
                        owned.append(process)
                        process.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            _, alive = psutil.wait_procs(owned, timeout=10)
            for process in alive:
                try:
                    process.kill()
                except psutil.NoSuchProcess:
                    pass
            psutil.wait_procs(alive, timeout=10)
            child.wait(timeout=10)
    outcome['logs'] = {path.name: path.read_text(errors='replace')[-4000:] for path in root.glob('*.log')}
    return outcome


def main(version, quarantine, evidence):
    if (platform.system() != 'Darwin' or platform.machine() != 'arm64'
            or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'):
        raise RuntimeError('Only isolated Apple Silicon GitHub runners')
    evidence.mkdir(parents=True, exist_ok=True)
    report = {'version': version, 'quarantine': quarantine, 'os': command('sw_vers'),
              'scope': 'Exact initial installer; no enrollment or update execution'}
    with tempfile.TemporaryDirectory(prefix='tracking-installer-compare-') as folder:
        root = Path(folder)
        bundle = download(version, root)
        dmg = root / 'setup.dmg'
        report['setup'] = extract_installer(bundle, version, dmg)
        if quarantine:
            result = command('xattr', '-w', 'com.apple.quarantine',
                             '0083;' + format(int(time.time()), 'x') + ';Chrome;' + str(uuid.uuid4()), dmg)
            if result.get('code') != 0:
                raise RuntimeError('Could not set download quarantine')
        report['dmg_assessment'] = command('spctl', '--assess', '--type', 'open', '--context',
                                           'context:primary-signature', '--verbose=4', dmg)
        mount = root / 'mount'
        mount.mkdir()
        subprocess.run(['hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint', str(mount), str(dmg)],
                       check=True, timeout=90, stdout=subprocess.DEVNULL)
        try:
            apps = list(mount.glob('*.app'))
            if len(apps) != 1:
                raise ValueError('Unexpected disk image layout')
            app = apps[0]
            report['dmg_entries'] = sorted(path.name for path in mount.iterdir())
            info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
            report['bundle'] = info
            executable = app / 'Contents/MacOS' / info['CFBundleExecutable']
            report['executable_mode'] = oct(executable.stat().st_mode & 0o777)
            report['architecture'] = command('lipo', '-archs', executable)
            report['minimum_os'] = command('otool', '-l', executable)
            report['signature'] = command('codesign', '-dv', '--verbose=4', app)
            report['signature_integrity'] = command('codesign', '--verify', '--deep', '--strict', app)
            report['app_assessment'] = command('spctl', '--assess', '--type', 'execute', '--verbose=4', app)
            report['quarantine_attributes'] = command('xattr', '-l', app)
            report['frozen'] = frozen_metadata(executable)
            report['launches'] = [launch(app, method, root, evidence)
                                  for method in ('launchservices', 'direct')]
        finally:
            subprocess.run(['hdiutil', 'detach', str(mount)], check=True, timeout=30)
            (evidence / 'comparison.json').write_text(json.dumps(report, indent=2, default=str) + '\n')
            print(json.dumps(report, default=str), flush=True)
    if not all(result['normal_gui_ready'] for result in report['launches']):
        raise AssertionError('Initial normal GUI did not open by every method; see comparison.json')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', choices=PINS, required=True)
    parser.add_argument('--quarantine', action='store_true')
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    main(args.version, args.quarantine, args.evidence)
