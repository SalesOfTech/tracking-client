"""Read-only release diagnostics on an isolated Mac; never publishes or enrolls."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TARGET = 'macos-arm64-modern'
PINS = {
    '3.4.2': 'af7fa96621d586ab80f0af68afa6ed8904b1054d5d737d0bc057b23f1062aaa0',
    '3.5.0': '5553576a2ede5d85126aeb3f459f315d48d782b62532d933c3a7c9549cb58526',
}
KEYS = {'pilot-2026': 'CZ2p3MojEDv9sgCmxjJZCxGrR2q2bYO8bBZ04XS0EPw='}
sys.path.insert(0, str(ROOT / 'agent'))
from agent_tracker.core.signed_updates import stage_archive
from smoke_published import stage_published_archive
from macos_archive import verify_frozen


def inspect_command(args):
    print('CHECK ' + ' '.join(map(str, args)), flush=True)
    try:
        result = subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=90)
        print(json.dumps({'exit_code': result.returncode,
                          'stdout': result.stdout[-6000:], 'stderr': result.stderr[-6000:]}), flush=True)
    except subprocess.TimeoutExpired:
        print('ASSESSMENT TIMEOUT; not a successful assessment', flush=True)


def download(version, root):
    name = 'SOFT-Tracking-' + TARGET + '-' + version + '.zip'
    path = root / name
    url = 'https://github.com/SalesOfTech/tracking-client/releases/download/v' + version + '/' + name
    digest = hashlib.sha256()
    total = 0
    with urllib.request.urlopen(url, timeout=60) as response, path.open('xb') as output:
        for block in iter(lambda: response.read(1024 * 1024), b''):
            total += len(block)
            if total > 1024 ** 3:
                raise ValueError('Oversized published bundle')
            digest.update(block)
            output.write(block)
    if digest.hexdigest() != PINS[version]:
        raise ValueError('Published bundle digest mismatch')
    print('VERIFIED published ' + version, flush=True)
    return path


def main():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('Requires native macOS Apple Silicon')
    inspect_command(['sw_vers'])
    inspect_command(['uname', '-m'])
    inspect_command(['spctl', '--status'])
    with tempfile.TemporaryDirectory(prefix='tracking-macos-diagnostic-') as folder:
        root = Path(folder)
        for version in PINS:
            bundle = download(version, root)
            directory = root / version
            setup = directory / 'setup-payload'
            manifest = stage_published_archive(bundle, setup, version, TARGET, KEYS,
                                                {'os': 'macos', 'architecture': 'arm64'})
            shutil.copy2(setup / 'manifest.json', directory / 'manifest.json')
            shutil.copy2(setup / 'release.zip', directory / ('release-' + version + '.zip'))
            payload = stage_archive(setup / 'release.zip', manifest, directory / 'inspected')
            frameworks = list((payload / 'app').rglob('Python.framework'))
            print(json.dumps({'version': version, 'external_python_frameworks': len(frameworks)}), flush=True)
            for framework in frameworks:
                print(json.dumps({'framework': str(framework.relative_to(payload)),
                                  'python_is_symlink': (framework / 'Python').is_symlink(),
                                  'current_is_symlink': (framework / 'Versions/Current').is_symlink()}), flush=True)
                inspect_command(['codesign', '--verify', '--deep', '--strict', framework])
            if version == '3.5.0':
                verify_frozen(payload / 'app/soft-tracking-app')
                inspect_command(['codesign', '-dv', '--verbose=4', payload / 'app/soft-tracking-app'])
                dmg = directory / 'SOFT-Tracking-Setup.dmg'
                with zipfile.ZipFile(bundle) as archive:
                    setup_name = json.loads(archive.read(TARGET + '/setup.json'))['file']
                    if setup_name != 'SOFT-Tracking-Setup-' + version + '.dmg':
                        raise ValueError('Unexpected installer filename')
                    with archive.open(TARGET + '/' + setup_name) as source, dmg.open('xb') as output:
                        shutil.copyfileobj(source, output)
                # Model a downloaded file without removing quarantine or weakening Gatekeeper.
                # This is an explicit policy assessment, not a real Chrome/Finder consent flow.
                subprocess.run(['xattr', '-w', 'com.apple.quarantine',
                                '0083;' + format(int(time.time()), 'x') + ';Chrome;' + str(uuid.uuid4()),
                                str(dmg)], check=True)
                inspect_command(['spctl', '--assess', '--type', 'open', '--context',
                                 'context:primary-signature', '--verbose=4', dmg])
                mount = directory / 'mount'
                mount.mkdir()
                subprocess.run(['hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint', str(mount), str(dmg)],
                               check=True, timeout=90, stdout=subprocess.DEVNULL)
                try:
                    apps = list(mount.glob('*.app'))
                    if len(apps) != 1:
                        raise ValueError('Unexpected installer disk image')
                    inspect_command(['codesign', '--verify', '--deep', '--strict', apps[0]])
                    inspect_command(['spctl', '--assess', '--type', 'execute', '--verbose=4', apps[0]])
                finally:
                    subprocess.run(['hdiutil', 'detach', str(mount)], check=True, timeout=30)
            bundle.unlink()
        home, temporary = root / 'home', root / 'tmp'
        home.mkdir()
        temporary.mkdir()
        env = dict(os.environ, HOME=str(home), TMPDIR=str(temporary), TMP=str(temporary),
                   TEMP=str(temporary), PYTHONNOUSERSITE='1')
        subprocess.run([sys.executable, str(ROOT / 'tools/smoke_update.py'), str(root / '3.4.2'),
                        str(root / '3.5.0'), '--published'], env=env, check=True, timeout=1200)
        print('PASS native installed startup/activation/rollback. NOT proof of unattended network update or Gatekeeper approval.', flush=True)


if __name__ == '__main__':
    main()
