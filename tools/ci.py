"""Small, validated entry points for GitHub Actions; no shell interpolation of inputs."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
sys.path.insert(0, str(ROOT))
from packaging.version import Version
from tools.release import file_digest, require_github_runner, ui_for_profile, write_payload_archive

TARGETS=[
    {'target':'windows-x64-modern','runner':'windows-2022','python':'3.10.11','arch':'x64','profile':'modern'},
    {'target':'windows-x86-legacy','runner':'windows-2022','python':'3.10.11','arch':'x86','profile':'legacy'},
    {'target':'windows-x64-legacy','runner':'windows-2022','python':'3.10.11','arch':'x64','profile':'legacy'},
    {'target':'windows-arm64-modern','runner':'windows-11-arm','python':'3.13.15','arch':'arm64','profile':'modern'},
    {'target':'macos-x64-modern','runner':'macos-15-intel','python':'3.10.19','arch':'x64','profile':'modern'},
    {'target':'macos-arm64-modern','runner':'macos-14','python':'3.10.11','arch':'arm64','profile':'modern'},
    {'target':'linux-x64-modern','runner':'ubuntu-22.04','python':'3.10.19','arch':'x64','profile':'modern'},
    {'target':'linux-arm64-modern','runner':'ubuntu-22.04-arm','python':'3.10.19','arch':'arm64','profile':'modern'},
]
for row in TARGETS:
    row['ui'] = 'qt' if row['profile'] == 'legacy' else 'electron'


def upgrade_baselines(release_version, requested=''):
    extra = [value.strip() for value in requested.split(',') if value.strip()]
    if len(extra) > 3 or len(extra) != len(set(extra)):
        raise ValueError('Expected at most three distinct additional upgrade baselines')
    for old in extra:
        if not re.fullmatch(r'\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?', old) or Version(old) >= Version(release_version):
            raise ValueError('Upgrade baseline must be older than the release')
    required = []
    if Version(release_version).release >= (3, 2, 0):
        required = ['3.1.0', '3.0.4']
        if Version(release_version).release >= (3, 2, 1):
            required.insert(0, '3.2.0')
    elif not extra:
        required = {'3.0.0': ['3.0.0-beta.7', '3.0.0-beta.9'],
                    '3.0.1': ['3.0.0', '3.0.0-beta.7'],
                    '3.0.2': ['3.0.0', '3.0.0-beta.7'],
                    '3.0.3': ['3.0.2', '3.0.0-beta.7']}.get(release_version, [])
    return list(dict.fromkeys(required + extra))


def output(key,value):
    with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8') as stream:
        stream.write(key+'='+value+'\n')


def version():
    value=os.environ.get('RELEASE_VERSION') or os.environ.get('GITHUB_REF_NAME','').removeprefix('v')
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?',value):raise ValueError('Invalid release version')
    return value


def target():
    value=os.environ['RELEASE_TARGET']
    if value not in {row['target'] for row in TARGETS}:raise ValueError('Unsupported release target')
    return value


def key_path():
    return Path(os.environ['RUNNER_TEMP'])/'tracking-release-signing.key'


def plan():
    selected=os.environ.get('RELEASE_TARGET','all')
    rows=TARGETS if selected=='all' else [row for row in TARGETS if row['target']==selected]
    if not rows:raise ValueError('Unknown target')
    output('matrix',json.dumps({'include':rows},separators=(',',':')))
    output('version',version())
    output('upgrade_from', ','.join(upgrade_baselines(version(), os.environ.get('RELEASE_UPGRADE_FROM', ''))))


def signing_key():
    key=base64.b64decode(os.environ['TRACKING_UPDATE_KEY_B64'],validate=True)
    if len(key)!=32:raise ValueError('Expected an Ed25519 seed of 32 bytes')
    with key_path().open('xb') as stream:stream.write(key)
    key_path().chmod(0o600)


def build():
    require_github_runner()
    from agent_tracker.core.target import runtime_target
    current=runtime_target()
    profile=os.environ['RELEASE_PROFILE']
    if '-'.join((*current,profile))!=target():raise ValueError('Runner architecture differs from requested package')
    ui_for_profile(*current, profile)
    subprocess.run([sys.executable,str(ROOT/'tools/release.py'),'build','--key',str(key_path()),'--key-id',os.environ['TRACKING_UPDATE_KEY_ID'],'--version',version(),'--profile',profile],check=True)


def export():
    build=ROOT/'artifacts'/target()/version()
    metadata=json.loads((build/'setup.json').read_text())
    published=ROOT/'artifacts/publish';published.mkdir(exist_ok=True)
    name='SOFT-Tracking-'+target()+'-'+version()+'.zip'
    installer=Path(metadata['file'])
    deployed_installer=installer.stem+'-'+version()+installer.suffix
    metadata['file']=deployed_installer
    provenance={'version':version(),'target':target(),'commit':os.environ['GITHUB_SHA'],
                'run_url':os.environ['GITHUB_SERVER_URL']+'/'+os.environ['GITHUB_REPOSITORY']+'/actions/runs/'+os.environ['GITHUB_RUN_ID']}
    provenance['build'] = json.loads((build/'payload/build.json').read_text(encoding='utf-8'))
    with zipfile.ZipFile(published/name,'x',zipfile.ZIP_STORED) as bundle:
        prefix=target()+'/'
        for filename in ('manifest.json','release-'+version()+'.zip','dependencies.txt','size-report.json'):
            bundle.write(build/filename,prefix+filename)
        if provenance['build'].get('ui') == 'electron':
            bundle.write(build/'desktop-package-lock.json', prefix+'desktop-package-lock.json')
        bundle.write(build/installer,prefix+deployed_installer)
        bundle.writestr(prefix+'setup.json',json.dumps(metadata,sort_keys=True))
        if target().startswith('windows-'):
            migration = json.loads((build / 'migration.json').read_text(encoding='utf-8'))
            if (migration.get('file') != 'SOFT-Tracking-Migrate.exe'
                    or migration.get('target') != target() or migration.get('version') != version()
                    or migration.get('sha256') != file_digest(build / migration['file'])):
                raise ValueError('Invalid migration artifact metadata')
            deployed_migration = 'SOFT-Tracking-Migrate-' + version() + '.exe'
            bundle.write(build / migration['file'], prefix + deployed_migration)
            migration['file'] = deployed_migration
            bundle.writestr(prefix + 'migration.json', json.dumps(migration, sort_keys=True))
        bundle.writestr(prefix+'provenance.json',json.dumps(provenance,sort_keys=True))
    digest=file_digest(published/name)
    (published/(name+'.sha256')).write_text(digest+'  '+name+'\n',encoding='ascii')


def smoke_update():
    from nacl.signing import SigningKey
    from agent_tracker.core.files import atomic_json,read_json
    current=ROOT/'artifacts'/target()/version()
    baseline=ROOT/'artifacts/smoke-baseline'/target()
    payload=baseline/'payload'
    shutil.copytree(current/'payload',payload)
    metadata=dict(read_json(payload/'build.json'),version='0.0.1',extension_version='0.0.1')
    atomic_json(payload/'build.json',metadata)
    atomic_json(payload/'extension/manifest.json',dict(read_json(payload/'extension/manifest.json'),version='0.0.1',version_name='0.0.1'))
    setup=baseline/'setup-payload';setup.mkdir()
    archive=setup/'release.zip'
    write_payload_archive(payload, archive)
    manifest=dict(metadata,protocol=3,expires_at=int(time.time())+3600,size=archive.stat().st_size,
                  sha256=file_digest(archive),
                  url='https://tracking.salesoftech.com/client/v3/releases/'+target()+'/release-0.0.1.zip')
    raw=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
    key=SigningKey(key_path().read_bytes())
    atomic_json(setup/'manifest.json',{'key_id':os.environ['TRACKING_UPDATE_KEY_ID'],'payload':base64.b64encode(raw).decode(),'signature':base64.b64encode(key.sign(raw).signature).decode()})
    atomic_json(setup/'setup-build.json',metadata)
    shutil.copy2(current/'setup-payload/trusted-update-keys.json',setup/'trusted-update-keys.json')
    subprocess.run([sys.executable,str(ROOT/'tools/smoke_update.py'),str(baseline),str(current)],check=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['plan','signing-key','build','export','smoke-update','cleanup-key']);args=parser.parse_args()
    if args.command=='cleanup-key':key_path().unlink(missing_ok=True)
    else:{'plan':plan,'signing-key':signing_key,'build':build,'export':export,'smoke-update':smoke_update}[args.command]()
