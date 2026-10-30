"""Inspect LaunchServices startup without disabling Gatekeeper or enrolling a device."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import psutil

from smoke_helpers import run_frozen


def probe_launch(app, directory, inspect_command):
    app = Path(app)
    directory = Path(directory)
    metadata = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    print(json.dumps({'bundle_metadata': {key: metadata.get(key) for key in (
        'CFBundleExecutable', 'CFBundleIdentifier', 'CFBundlePackageType',
        'LSMinimumSystemVersion', 'LSArchitecturePriority')}}), flush=True)
    executable = app / 'Contents/MacOS' / metadata['CFBundleExecutable']
    print(json.dumps({'executable_exists': executable.is_file(),
                      'executable_mode': oct(executable.stat().st_mode & 0o777)}), flush=True)
    inspect_command(['file', executable])
    inspect_command(['lipo', '-archs', executable])
    inspect_command(['xattr', '-l', app])
    inspect_command(['open', '-h'])
    home = directory / 'launch-home'
    home.mkdir()
    temporary = directory / 'launch-tmp'
    temporary.mkdir()
    for method in ('launchservices', 'direct'):
        marker = directory / (method + '-health.json')
        env = dict(os.environ, HOME=str(home), TMPDIR=str(temporary) + '/',
                   SOFT_TRACKING_HEALTH=str(marker), PYTHONNOUSERSITE='1')
        args = [str(executable), '--health-check']
        if method == 'launchservices':
            args = ['open', '-n', '-W', '--env', 'SOFT_TRACKING_HEALTH=' + str(marker),
                    '--env', 'HOME=' + str(home), '--env', 'TMPDIR=' + str(temporary) + '/',
                    '-a', str(app), '--args', '--health-check']
        try:
            result = run_frozen(args, env, timeout=120)
            print(json.dumps({'launch_method': method, 'exit_code': result.returncode,
                'health': json.loads(marker.read_text()) if marker.exists() else None,
                'stderr': result.stderr.decode(errors='replace')[-6000:]}), flush=True)
        except subprocess.TimeoutExpired:
            print(json.dumps({'launch_method': method, 'timeout': True,
                              'health_marker': marker.exists()}), flush=True)
        finally:
            # LaunchServices children need not descend from open(1).
            owned = []
            for process in psutil.process_iter():
                try:
                    if process.environ().get('SOFT_TRACKING_HEALTH') == str(marker):
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
