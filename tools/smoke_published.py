"""Test real previous GitHub packages on their matching native Actions runner."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile

from packaging.version import Version
from ci import ROOT, target, version, upgrade_baselines

sys.path.insert(0, str(ROOT / 'agent'))
from agent_tracker.core.files import atomic_json, read_json
from agent_tracker.core.signed_updates import verify_manifest, MAX_ARCHIVE_BYTES


def main():
    # The workflow's resolved list can include two required transition baselines
    # plus three operator-specified historical releases.
    supplied = [value.strip() for value in os.environ.get('RELEASE_UPGRADE_FROM', '').split(',') if value.strip()]
    required = upgrade_baselines(version())
    previous = upgrade_baselines(version(), ','.join(value for value in supplied if value not in required))
    current = ROOT / 'artifacts' / target() / version()
    build = read_json(current / 'setup-payload/setup-build.json')
    keys = read_json(current / 'setup-payload/trusted-update-keys.json')
    for old in previous:
        if not re.fullmatch(r'\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?', old) or Version(old) >= Version(version()):
            raise ValueError('Upgrade baseline must be older than the release')
        baseline = ROOT / 'artifacts/published-baselines' / target() / old
        baseline.mkdir(parents=True, exist_ok=False)
        name = 'SOFT-Tracking-' + target() + '-' + old + '.zip'
        pinned = read_json(ROOT / 'tools/upgrade-baselines.json').get(old, {}).get(target())
        if pinned:
            # Pin exact archived bundles; baselines must be published for read-only tokens.
            endpoint = 'repos/' + os.environ['GITHUB_REPOSITORY'] + '/releases/assets/' + str(pinned[0])
            with (baseline / name).open('xb') as output:
                subprocess.run(['gh', 'api', endpoint, '-H', 'Accept: application/octet-stream'], stdout=output, check=True)
            checksum = pinned[1] + '  ' + name
        else:
            subprocess.run(['gh', 'release', 'download', 'v' + old, '--repo', os.environ['GITHUB_REPOSITORY'],
                            '--pattern', name, '--pattern', name + '.sha256', '--dir', str(baseline)], check=True)
            checksum = (baseline / (name + '.sha256')).read_text(encoding='ascii').strip()
        digest = hashlib.sha256()
        with (baseline / name).open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        if checksum != digest.hexdigest() + '  ' + name:
            raise ValueError('Published artifact checksum mismatch')
        setup = baseline / 'setup-payload'
        setup.mkdir()
        with zipfile.ZipFile(baseline / name) as archive:
            prefix = target() + '/'
            if archive.getinfo(prefix + 'manifest.json').file_size > 65536:
                raise ValueError('Baseline manifest too large')
            envelope = json.loads(archive.read(prefix + 'manifest.json'))
            manifest = verify_manifest(envelope, keys, '0.0.0', build['os'], build['architecture'])
            if manifest['version'] != old or manifest['target'] != target():
                raise ValueError('Wrong published baseline')
            info = archive.getinfo(prefix + 'release-' + old + '.zip')
            if info.file_size != manifest['size'] or info.file_size > MAX_ARCHIVE_BYTES:
                raise ValueError('Published release archive size mismatch')
            with archive.open(info) as source, (setup / 'release.zip').open('xb') as output:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
        atomic_json(setup / 'manifest.json', envelope)
        atomic_json(setup / 'trusted-update-keys.json', keys)
        atomic_json(setup / 'setup-build.json', manifest)
        subprocess.run([sys.executable, str(ROOT / 'tools/smoke_update.py'), str(baseline), str(current), '--published'], check=True)


if __name__ == '__main__':
    main()
