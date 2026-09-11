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


def stage_published_archive(archive_path, setup, old, target_name, keys, build):
    """Shared real-package verification; transport does not change acceptance."""
    setup = Path(setup)
    setup.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive_path) as archive:
        prefix = target_name + '/'
        names = [info.filename for info in archive.infolist()]
        if len(names) > 20000 or len(names) != len(set(names)):
            raise ValueError('Duplicate or excessive baseline entries')
        if archive.getinfo(prefix + 'manifest.json').file_size > 65536:
            raise ValueError('Baseline manifest too large')
        envelope = json.loads(archive.read(prefix + 'manifest.json'))
        manifest = verify_manifest(envelope, keys, '0.0.0', build['os'], build['architecture'])
        if manifest['version'] != old or manifest['target'] != target_name:
            raise ValueError('Wrong published baseline')
        info = archive.getinfo(prefix + 'release-' + old + '.zip')
        if info.file_size != manifest['size'] or info.file_size > MAX_ARCHIVE_BYTES:
            raise ValueError('Published release archive size mismatch')
        digest = hashlib.sha256()
        with archive.open(info) as source, (setup / 'release.zip').open('xb') as output:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != manifest['sha256']:
            raise ValueError('Published release archive checksum mismatch')
    atomic_json(setup / 'manifest.json', envelope)
    atomic_json(setup / 'trusted-update-keys.json', keys)
    atomic_json(setup / 'setup-build.json', manifest)
    return manifest


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
        stage_published_archive(baseline / name, baseline / 'setup-payload', old, target(), keys, build)
        subprocess.run([sys.executable, str(ROOT / 'tools/smoke_update.py'), str(baseline), str(current), '--published'], check=True)


if __name__ == '__main__':
    main()
