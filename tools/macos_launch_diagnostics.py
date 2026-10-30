"""Inspect LaunchServices startup without disabling Gatekeeper or enrolling a device."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import psutil

from smoke_helpers import run_frozen


def probe_launch(app, directory, inspect_command, launch_argument='--health-check'):
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
    directory.mkdir(parents=True, exist_ok=True)
    home = directory / 'launch-home'
    (home / 'Library/Caches').mkdir(parents=True)
    temporary = directory / 'launch-tmp'
    temporary.mkdir()
    results = []
    for method in ('launchservices', 'direct'):
        marker = directory / (method + '-health.json')
        env = dict(os.environ, HOME=str(home), TMPDIR=str(temporary) + '/',
                   SOFT_TRACKING_HEALTH=str(marker), PYTHONNOUSERSITE='1')
        args = [str(executable), launch_argument]
        if method == 'launchservices':
            args = ['open', '-n', '-W', '--env', 'SOFT_TRACKING_HEALTH=' + str(marker),
                    '--env', 'HOME=' + str(home), '--env', 'TMPDIR=' + str(temporary) + '/',
                    '--stdout', str(directory / 'launch-stdout.log'),
                    '--stderr', str(directory / 'launch-stderr.log'),
                    '-a', str(app), '--args', launch_argument]
        try:
            result = run_frozen(args, env, timeout=120)
            outcome = {'launch_method': method, 'exit_code': result.returncode,
                'health': json.loads(marker.read_text()) if marker.exists() else None,
                'stderr': result.stderr.decode(errors='replace')[-6000:]}
        except subprocess.TimeoutExpired:
            outcome = {'launch_method': method, 'timeout': True,
                       'health_marker': marker.exists()}
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
        results.append(outcome)
        print(json.dumps(outcome), flush=True)
        if method == 'launchservices':
            for name in ('launch-stdout.log', 'launch-stderr.log'):
                path = directory / name
                if path.exists():
                    print(name + ': ' + path.read_text(errors='replace')[-6000:], flush=True)
    return results


def assert_launch_results(results):
    for method in ('launchservices', 'direct'):
        matches = [result for result in results if result.get('launch_method') == method]
        if len(matches) != 1:
            raise AssertionError('Missing or duplicate launch result: ' + method)
        result = matches[0]
        health = result.get('health') or {}
        if result.get('timeout') or result.get('exit_code') != 0 or \
                health.get('ready') is not True or health.get('ui') != 'electron':
            raise AssertionError('Installer startup not confirmed: ' + method)
