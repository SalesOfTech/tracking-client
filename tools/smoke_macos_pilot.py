"""Native ARM acceptance of the exact distributed DMG and explicit trust fallback."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile

from smoke_helpers import run_frozen, assert_ui_health
from smoke_package import main as smoke_package
from release import copy_electron


def check(image):
    attached = subprocess.check_output(['hdiutil', 'attach', '-readonly', '-nobrowse', '-plist', str(image)])
    mount = next(Path(row['mount-point']) for row in plistlib.loads(attached)['system-entities']
                 if 'mount-point' in row)
    try:
        with tempfile.TemporaryDirectory(prefix='soft-arm-pilot-') as temporary:
            root = Path(temporary)
            app = root/'SOFT-Tracking-Setup.app'
            subprocess.run(['ditto', str(mount/app.name), str(app)], check=True)
            subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
            executable = app/'Contents/MacOS/SOFT-Tracking-Setup'
            prepared = json.loads(subprocess.check_output([str(executable), '--prepare-engine']))
            engine = Path(prepared['bundle_path'])
            seals = list((engine/'Contents/Frameworks/Python.framework').rglob('CodeResources'))
            assert seals, 'Framework is missing its resource seal'
            subprocess.run(['codesign', '--verify', '--deep', '--strict', str(engine)], check=True)
            # Exercise the distributed, read-only mounted installer before copying payloads.
            health = root/'mounted-health.json'
            env = dict(os.environ, SOFT_TRACKING_HEALTH=str(health))
            result = run_frozen([str(mount/app.name/'Contents/MacOS/SOFT-Tracking-Setup'), '--health-check'],
                                env=env, timeout=120)
            assert result.returncode == 0, result.stderr.decode(errors='replace')[-4000:]
            bundle = root/'setup-payload'
            resources = app/'Contents/Resources/setup-payload'
            shutil.copytree(resources, bundle, symlinks=True, ignore=shutil.ignore_patterns('electron'))
            # The generic smoke compares transport layouts; the DMG itself keeps
            # complete Electron links so its read-only setup can launch directly.
            copy_electron(resources/'electron', bundle/'electron')
            metadata = json.loads((bundle/'setup-build.json').read_text())
            assert_ui_health(health, metadata)
            smoke_package(bundle)
            # The fallback is a deliberate administrator trust decision, not notarization.
            script = root/'Install.command'
            shutil.copy2(mount/'Install.command', script)
            subprocess.run(['xattr', '-r', '-w', 'com.apple.quarantine',
                            '0083;69000000;Chrome;00000000-0000-0000-0000-000000000001', str(app)], check=True)
            subprocess.run(['xattr', '-r', '-w', 'com.apple.quarantine',
                            '0083;69000000;Chrome;00000000-0000-0000-0000-000000000001', str(engine)], check=True)
            declined = run_frozen(['/bin/bash', str(script), '--health-check'], input=b'n\n',
                                  env=dict(os.environ), timeout=30)
            assert declined.returncode != 0
            subprocess.run(['xattr', '-p', 'com.apple.quarantine', str(engine)], check=True, stdout=subprocess.DEVNULL)
            health = root/'consented-health.json'
            result = run_frozen(['/bin/bash', str(script), '--health-check'], input=b'y\n',
                                env=dict(os.environ, SOFT_TRACKING_HEALTH=str(health)), timeout=180)
            assert result.returncode == 0, result.stderr.decode(errors='replace')[-4000:]
            assert_ui_health(health, metadata)
            # Consent must not remove quarantine from the original download.
            assert subprocess.run(['xattr', '-p', 'com.apple.quarantine', str(engine)],
                                  capture_output=True).returncode != 0
            subprocess.run(['xattr', '-p', 'com.apple.quarantine', str(app)], check=True, stdout=subprocess.DEVNULL)
            # A cold-cache run must not reuse a previously healthy extraction.
            shutil.rmtree(engine.parent)
            health = root/'cold-consented-health.json'
            result = run_frozen(['/bin/bash', str(script), '--health-check'], input=b'y\n',
                                env=dict(os.environ, SOFT_TRACKING_HEALTH=str(health)), timeout=180)
            assert result.returncode == 0, result.stderr.decode(errors='replace')[-4000:]
            assert_ui_health(health, metadata)
            print('PASS: mounted DMG, sealed framework, frozen install/native messaging/start/stop, explicit trust fallback')
            print('NOT tested: physical M1, zero-consent Gatekeeper trust, macOS 11/12, next-release live update')
    finally:
        subprocess.run(['hdiutil', 'detach', str(mount)], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('image', type=Path)
    check(parser.parse_args().image.resolve())
