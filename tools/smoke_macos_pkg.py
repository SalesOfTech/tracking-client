"""Exercise the real PKG preparation and normal key-entry GUI on an isolated runner."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time
import uuid

import psutil
from macos_pkg import IDENTIFIER


def run(*args, timeout=120, check=True):
    return subprocess.run([str(arg) for arg in args], capture_output=True, text=True,
                          timeout=timeout, check=check)


def installer_processes():
    result = []
    for process in psutil.process_iter():
        try:
            if process.exe() == '/System/Library/CoreServices/Installer.app/Contents/MacOS/Installer':
                result.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return result


def stop(processes):
    for process in processes:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(processes, timeout=10)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(alive, timeout=10)


def check(package, evidence):
    if platform.system() != 'Darwin' or platform.machine() != 'arm64' or os.geteuid() == 0:
        raise RuntimeError('Requires an isolated native ARM runner user')
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise RuntimeError('Do not install test packages on a developer or customer machine')
    console_uid = int(run('stat', '-f', '%u', '/dev/console').stdout.strip())
    if console_uid != os.getuid() or installer_processes():
        raise RuntimeError('Expected the runner console user and no other Installer session')
    if run('pkgutil', '--pkg-info', IDENTIFIER, check=False).returncode == 0:
        raise RuntimeError('Preexisting pilot receipt; refusing to replace it')
    evidence.mkdir(parents=True, exist_ok=True)
    temp = Path(run('getconf', 'DARWIN_USER_TEMP_DIR').stdout.strip()).resolve()
    before = set(temp.glob('SOFTTrackingGUI.*'))
    cache = Path.home() / 'Library/Caches/SOFTTrackingSetup-engine'
    cache_before = set(cache.iterdir()) if cache.exists() else set()
    marker = None
    owned = set()
    try:
        run('xattr', '-w', 'com.apple.quarantine',
            '0083;' + format(int(time.time()), 'x') + ';Chrome;' + str(uuid.uuid4()), package)
        policy = run('spctl', '--assess', '--type', 'install', '--verbose=4', package, check=False)
        print('Unsigned package policy assessment: ' + str(policy.returncode), flush=True)
        print(policy.stderr[-2000:], flush=True)
        # Opening a document in Apple's signed Installer is distinct from executing
        # our unsigned app. Capture any system consent prompt instead of hiding it.
        policy_ui_before = {p.pid for p in psutil.process_iter() if p.name() == 'CoreServicesUIAgent'}
        opened = subprocess.Popen(['open', '-a', '/System/Library/CoreServices/Installer.app', str(package)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            try:
                opened.wait(timeout=5)
                print('Installer document open: ' + str(opened.returncode), flush=True)
            except subprocess.TimeoutExpired:
                print('Package open awaits macOS trust UI; this is NOT automatic approval.', flush=True)
            time.sleep(2)
            run('screencapture', '-x', evidence / '01-package-open.png')
        finally:
            if opened.poll() is None:
                opened.terminate()
            opened.wait(timeout=10)
            # Cancel only the consent UI created by this isolated probe. This does
            # not approve the package or modify Gatekeeper policy.
            stop([p for p in psutil.process_iter() if p.name() == 'CoreServicesUIAgent'
                  and p.pid not in policy_ui_before])
        stop(installer_processes())
        # CLI authorization tests package execution AFTER the user's trust decision;
        # it is deliberately not reported as automated Gatekeeper consent.
        installed = run('sudo', '-n', '/usr/sbin/installer', '-dumplog', '-verboseR', '-pkg', package, '-target', '/',
                        timeout=240, check=False)
        print(installed.stdout[-6000:] + installed.stderr[-6000:], flush=True)
        if installed.returncode:
            raise AssertionError('Apple Installer package preparation failed')
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            owned = set(temp.glob('SOFTTrackingGUI.*')) - before
            markers = [path / 'setup-ready.json' for path in owned if (path / 'setup-ready.json').is_file()]
            if len(markers) == 1:
                marker = markers[0]
                break
            time.sleep(0.5)
        if marker is None:
            run('screencapture', '-x', evidence / '03-launch-failure.png', check=False)
            print('Prepared directories: ' + repr(sorted(map(str, owned))), flush=True)
            jobs = run('launchctl', 'list', check=False)
            print('\n'.join(line for line in jobs.stdout.splitlines() if 'soft.tracking' in line), flush=True)
            install_log = run('sudo', '-n', 'tail', '-n', '400', '/var/log/install.log', check=False)
            print(install_log.stdout[-25000:], flush=True)
            for path in owned:
                for log in path.glob('*.log'):
                    print(log.read_text(errors='replace')[-4000:], flush=True)
            raise AssertionError('Normal key-entry renderer never became ready')
        assert json.loads(marker.read_text()) == {'ready': True, 'ui': 'electron'}
        normal_ui = []
        for process in psutil.process_iter():
            try:
                env = process.environ()
                if env.get('SOFT_TRACKING_HEALTH') == str(marker) and env.get('SOFT_TRACKING_UI_MODE') == 'installer':
                    assert env.get('SOFT_TRACKING_UI_HEALTH') == '0', 'Must test the normal GUI, not health mode'
                    assert process.uids().effective == os.getuid(), 'Setup UI must not run as root'
                    normal_ui.append(process)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        assert normal_ui, 'No normal installer UI under console user'
        run('screencapture', '-x', evidence / '02-key-entry.png')
        run('osascript', '-e', 'tell application id "com.soft.tracking.ui" to quit', timeout=30)
        deadline = time.monotonic() + 30
        while marker.parent.exists() and time.monotonic() < deadline:
            time.sleep(0.5)
        assert not marker.parent.exists(), 'Closing setup left its temporary package copy behind'
        print('PASS: real PKG execution, normal key-entry renderer under console user, close and temporary cleanup', flush=True)
        print('NOT proven: automatic Gatekeeper consent, physical M1, real employee-key enrollment', flush=True)
    finally:
        stop(installer_processes())
        owned |= set(temp.glob('SOFTTrackingGUI.*')) - before
        for path in owned:
            processes = []
            for process in psutil.process_iter():
                try:
                    if str(path) in ' '.join(process.cmdline()) or process.environ().get('SOFT_TRACKING_HEALTH') == str(path / 'setup-ready.json'):
                        processes.append(process)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            stop(processes)
            if path.is_dir() and not path.is_symlink() and path.stat().st_uid == os.getuid():
                shutil.rmtree(path)
        if cache.exists():
            for path in set(cache.iterdir()) - cache_before:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            if not list(cache.iterdir()):
                cache.rmdir()
        run('sudo', '-n', 'pkgutil', '--forget', IDENTIFIER, check=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('evidence', type=Path)
    args = parser.parse_args()
    check(args.package.resolve(), args.evidence.resolve())
