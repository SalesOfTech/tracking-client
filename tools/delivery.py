"""Export byte-identical runtime files from an accepted release on GitHub only."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


def gh(*args):
    return subprocess.check_output(['gh', *args], text=True)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def export(archive, destination, version):
    target = archive.name.removeprefix('SOFT-Tracking-').removesuffix('-' + version + '.zip')
    if not re.fullmatch(r'(windows|linux|macos)-(x64|x86|arm64)-(modern|legacy)', target):
        raise ValueError('Invalid target')
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate ZIP members')
        setup = json.loads(bundle.read(target + '/setup.json'))
        signed = json.loads(bundle.read(target + '/manifest.json'))
        import base64
        manifest = json.loads(base64.b64decode(signed['payload'], validate=True))
        if setup['target'] != target or setup['version'] != version or manifest['target'] != target or manifest['version'] != version:
            raise ValueError('Mismatched release')
        if not re.fullmatch(r'SOFT-Tracking-Setup-[0-9.]+\.(exe|dmg|run)', setup['file']):
            raise ValueError('Invalid installer')
        output = []
        for name, expected in ((setup['file'], setup['sha256']), ('release-' + version + '.zip', manifest['sha256'])):
            path = destination / (target + '--' + name)
            with bundle.open(target + '/' + name) as source, path.open('xb') as sink:
                shutil.copyfileobj(source, sink, 1024 * 1024)
            if digest(path) != expected:
                raise ValueError('Artifact hash mismatch')
            if name.startswith('release-') and path.stat().st_size != manifest['size']:
                raise ValueError('Update size mismatch')
            output.append(path)
        return output


def main():
    version = os.environ['DELIVERY_VERSION'].removeprefix('v')
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Only accepted stable versions may be exported')
    repo = os.environ['GITHUB_REPOSITORY']
    if repo != 'SalesOfTech/tracking-client':
        raise ValueError('Public client repository required')
    release = json.loads(gh('release', 'view', 'v' + version, '--repo', repo, '--json', 'isDraft,isPrerelease,assets,tagName'))
    if release['isDraft'] or release['isPrerelease']:
        raise ValueError('Source release is not stable')
    archives = [a for a in release['assets'] if a['name'].endswith('.zip')]
    if len(archives) != 8:
        raise ValueError('Expected all eight accepted targets')
    tag = 'delivery-' + version
    try:
        current = json.loads(gh('release', 'view', tag, '--repo', repo, '--json', 'isDraft,assets'))
    except subprocess.CalledProcessError:
        commit = gh('api', 'repos/' + repo + '/commits/v' + version, '--jq', '.sha').strip()
        gh('release', 'create', tag, '--repo', repo, '--target', commit, '--draft', '--prerelease',
           '--title', 'Runtime delivery ' + version, '--notes',
           'Byte-identical installers and signed update payloads from v' + version + '. No compilation or customer data. Storage on GitHub only.')
        current = {'isDraft': True, 'assets': []}
    existing = {a['name']: a for a in current['assets']}
    expected = set()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for asset in archives:
            gh('release', 'download', 'v' + version, '--repo', repo, '--pattern', asset['name'], '--dir', str(root))
            archive = root / asset['name']
            if archive.stat().st_size != asset['size'] or 'sha256:' + digest(archive) != asset['digest']:
                raise ValueError('Source archive mismatch')
            for path in export(archive, root, version):
                expected.add(path.name)
                old = existing.get(path.name)
                if old:
                    if old['size'] != path.stat().st_size or old['digest'] != 'sha256:' + digest(path):
                        raise ValueError('Refusing to replace an existing asset')
                else:
                    if not current['isDraft']:
                        raise ValueError('Incomplete published delivery')
                    gh('release', 'upload', tag, str(path), '--repo', repo)
                path.unlink()
            archive.unlink()
    final = json.loads(gh('release', 'view', tag, '--repo', repo, '--json', 'assets'))
    if {a['name'] for a in final['assets']} != expected:
        raise ValueError('Unexpected delivery assets')
    if current['isDraft']:
        gh('release', 'edit', tag, '--repo', repo, '--draft=false', '--latest=false')
    print('Verified and published', len(expected), 'runtime assets for', version)


if __name__ == '__main__':
    main()
