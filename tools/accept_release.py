"""Opt-in genuine-baseline acceptance and exact-byte stable promotion on Actions.

CLI failures deliberately expose only a fixed stage code. Never print an HTTP
exception, secret, subprocess output or private package contents here.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit
import zipfile

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from ci import TARGETS, upgrade_baselines
from smoke_published import stage_published_archive
from agent_tracker.core.files import atomic_json
from agent_tracker.core.target import runtime_target

VERSION = '3.2.0'
ENVIRONMENT = 'tracking-release-acceptance'
SMOKE_STEP = 'Both genuine published upgrades with all existing smoke assertions'
MAX_BUNDLE = 2 * 1024 ** 3 - 1
ASSET_HOST = 'release-assets.githubusercontent.com'
STAGE = 'preflight'


def require(condition):
    if not condition:
        raise ValueError('Acceptance contract rejected')


def json_value(raw, limit=65536):
    require(isinstance(raw, (str, bytes)) and len(raw) <= limit)
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs)


def sha(value):
    require(isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value))
    return value


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def trusted_keys(raw):
    keys = json_value(raw)
    require(isinstance(keys, dict) and 0 < len(keys) <= 8)
    for name, value in keys.items():
        require(re.fullmatch('[A-Za-z0-9_-]{1,80}', name) and isinstance(value, str))
        require(len(base64.b64decode(value, validate=True)) == 32)
    return keys


def asset_url(value):
    require(isinstance(value, str) and 0 < len(value) <= 8192)
    require(all(32 < ord(character) < 127 for character in value))
    url = urlsplit(value)
    require(url.scheme == 'https' and url.hostname == ASSET_HOST and url.port in (None, 443))
    require(not url.username and not url.password and not url.fragment and bool(url.query))
    return value


def baseline_urls(raw):
    document = json_value(raw, 48 * 1024)
    require(isinstance(document, dict) and set(document) == set(upgrade_baselines(VERSION)))
    targets = {row['target'] for row in TARGETS}
    for slots in document.values():
        require(isinstance(slots, dict) and set(slots) == targets)
        for slot in slots.values():
            require(isinstance(slot, dict) and set(slot) == {'url'})
            asset_url(slot['url'])
    return document


def candidate_identity(tag):
    match = re.fullmatch(r'candidate-3\.2\.0-([1-9][0-9]*)-([1-9][0-9]*)', tag)
    require(match is not None)
    return int(match[1]), int(match[2])


class GitHub:
    def __init__(self):
        self.repo = os.environ['GITHUB_REPOSITORY']
        require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', self.repo))
        self.token = os.environ['GH_TOKEN']
        require(bool(self.token))

    def request(self, path, method='GET', **options):
        require(path.startswith('repos/' + self.repo + '/'))
        with requests.Session() as session:
            session.trust_env = False
            response = session.request(method, 'https://api.github.com/' + path,
                                       headers={'Authorization': 'Bearer ' + self.token,
                                                'Accept': 'application/vnd.github+json',
                                                'X-GitHub-Api-Version': '2022-11-28'},
                                       timeout=(15, 90), allow_redirects=False, **options)
        return response

    def api(self, suffix, method='GET', body=None, absent=False):
        options = {'json': body} if body is not None else {}
        with self.request('repos/' + self.repo + '/' + suffix, method, **options) as response:
            if absent and response.status_code == 404:
                return None
            require(200 <= response.status_code < 300)
            return json_value(response.content, 16 * 1024 * 1024)

    def jobs(self, run, attempt):
        rows = []
        for page in range(1, 11):
            data = self.api(f'actions/runs/{run}/attempts/{attempt}/jobs?per_page=100&page={page}')
            rows.extend(data['jobs'])
            if len(rows) == data['total_count']:
                return rows
        raise ValueError('Too many jobs')

    def asset(self, identifier, destination, expected, size):
        require(type(identifier) is int and identifier > 0)
        with requests.Session() as session:
            session.trust_env = False
            with session.get(f'https://api.github.com/repos/{self.repo}/releases/assets/{identifier}',
                             headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/octet-stream'},
                             stream=True, timeout=(15, 60), allow_redirects=False) as response:
                if response.status_code == 302:
                    download_url(response.headers['Location'], destination, expected, size)
                else:
                    receive(response, destination, expected, size)

    def upload(self, release_id, path):
        require(type(release_id) is int and release_id > 0)
        require(re.fullmatch(r'SOFT-Tracking-[a-z0-9-]+-3\.2\.0\.zip(?:\.sha256)?', path.name))
        with requests.Session() as session, path.open('rb') as stream:
            session.trust_env = False
            with session.post(f'https://uploads.github.com/repos/{self.repo}/releases/{release_id}/assets',
                              params={'name': path.name}, data=stream,
                              headers={'Authorization': 'Bearer ' + self.token,
                                       'Content-Type': 'application/octet-stream',
                                       'Content-Length': str(path.stat().st_size)},
                              timeout=(15, 600), allow_redirects=False) as response:
                require(response.status_code == 201)


def receive(response, destination, expected, size=None):
    require(response.status_code == 200)
    sha(expected)
    limit = MAX_BUNDLE if size is None else size
    require(type(limit) is int and 0 < limit <= MAX_BUNDLE)
    started, count, checksum = time.monotonic(), 0, hashlib.sha256()
    with Path(destination).open('xb') as output:
        for chunk in response.iter_content(1024 * 1024):
            count += len(chunk)
            require(count <= limit and time.monotonic() - started <= 600)
            checksum.update(chunk)
            output.write(chunk)
    require(count > 0 and (size is None or count == size) and checksum.hexdigest() == expected)


def download_url(url, destination, expected, size=None):
    asset_url(url)
    # A separate session has neither the GitHub token nor inherited proxy auth.
    with requests.Session() as session:
        session.trust_env = False
        with session.get(url, stream=True, timeout=(15, 60), allow_redirects=False) as response:
            receive(response, destination, expected, size)


def checkout_sha(path):
    return subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout.strip()


def hosted_context():
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted')
    require(os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch' and os.environ.get('GITHUB_REF') == 'refs/heads/main')
    require(os.environ.get('GITHUB_SERVER_URL') == 'https://github.com')
    require(os.environ.get('GITHUB_WORKFLOW_REF') == os.environ['GITHUB_REPOSITORY'] + '/.github/workflows/accept-release.yml@refs/heads/main')
    require(checkout_sha(ROOT) == os.environ['GITHUB_SHA'])
    require(sys.flags.optimize == 0)


def authorize_dispatch(api):
    for actor in {os.environ['GITHUB_ACTOR'], os.environ.get('GITHUB_TRIGGERING_ACTOR', os.environ['GITHUB_ACTOR'])}:
        require(re.fullmatch('[A-Za-z0-9-]+', actor))
        permission = api.api('collaborators/' + actor + '/permission')
        require(permission.get('permission') == 'admin' or permission.get('role_name') in ('admin', 'maintain'))
    environment = api.api('environments/' + ENVIRONMENT)
    require(environment.get('deployment_branch_policy') ==
            {'protected_branches': False, 'custom_branch_policies': True})
    branches = api.api('environments/' + ENVIRONMENT + '/deployment-branch-policies?per_page=100')
    require(branches.get('total_count') == 1 and len(branches.get('branch_policies', [])) == 1)
    require(branches['branch_policies'][0].get('name') == 'main' and
            branches['branch_policies'][0].get('type') == 'branch')
    # GitHub enforces any additional reviewer rules before these jobs can start.
    # We never write environment settings or treat an input boolean as approval.
    run = api.api('actions/runs/' + os.environ['GITHUB_RUN_ID'])
    require(run.get('head_sha') == os.environ['GITHUB_SHA'] and run.get('head_branch') == 'main')
    require(run.get('event') == 'workflow_dispatch' and run.get('path') == '.github/workflows/accept-release.yml')
    require(run.get('run_attempt') == int(os.environ['GITHUB_RUN_ATTEMPT']))
    require(run.get('head_repository', {}).get('full_name') == api.repo)
    require(run.get('actor', {}).get('login') == os.environ['GITHUB_ACTOR'])
    require(run.get('triggering_actor', {}).get('login') == os.environ.get('GITHUB_TRIGGERING_ACTOR', os.environ['GITHUB_ACTOR']))


def validate_run(run, repo, run_id, attempt):
    require(run.get('id') == run_id and run.get('run_attempt') == attempt)
    require(run.get('event') == 'workflow_dispatch' and run.get('head_branch') == 'main')
    require(run.get('status') == 'completed' and run.get('conclusion') == 'success')
    require(run.get('path') == '.github/workflows/release.yml')
    require(run.get('head_repository', {}).get('full_name') == repo)
    require(re.fullmatch('[a-f0-9]{40}', run.get('head_sha', '')))


def candidate_assets(release, partial=False):
    expected = {'SOFT-Tracking-' + row['target'] + '-' + VERSION + '.zip' + suffix
                for row in TARGETS for suffix in ('', '.sha256')}
    assets = release['assets']
    names = {asset['name'] for asset in assets}
    require(len(expected) == 16 and len(assets) == len(names) and names <= expected)
    require(partial or names == expected)
    result = {}
    for asset in assets:
        require(asset['state'] == 'uploaded' and type(asset['id']) is int and asset['id'] > 0)
        require(type(asset['size']) is int and 0 < asset['size'] <= MAX_BUNDLE)
        if asset['name'].endswith('.sha256'):
            require(asset['size'] <= 1024)
        require(isinstance(asset.get('digest'), str) and asset['digest'].startswith('sha256:'))
        result[asset['name']] = {'id': asset['id'], 'size': asset['size'], 'sha256': sha(asset['digest'][7:])}
    require(len({asset['id'] for asset in result.values()}) == len(result))
    return result


def candidate_snapshot(api, tag):
    run_id, attempt = candidate_identity(tag)
    run = api.api(f'actions/runs/{run_id}')
    validate_run(run, api.repo, run_id, attempt)
    workflow = api.api('actions/workflows/release.yml')
    require(run['workflow_id'] == workflow['id'])
    jobs = api.jobs(run_id, attempt)
    require(jobs and all(job['conclusion'] == 'success' for job in jobs))
    for row in TARGETS:
        matching = [job for job in jobs if job['name'].startswith('build (') and
                    re.search(r'(?<![a-z0-9-])' + re.escape(row['target']) + r'(?![a-z0-9-])', job['name'])]
        require(len(matching) == 1)
    require(any(job['name'] == 'publish-candidate' for job in jobs))
    tag_ref = api.api('git/ref/tags/' + tag)
    require(tag_ref.get('ref') == 'refs/tags/' + tag and tag_ref['object']['type'] == 'commit')
    require(tag_ref['object']['sha'] == run['head_sha'])
    release = api.api('releases/tags/' + tag)
    require(release['tag_name'] == tag and release['draft'] is False and release['prerelease'] is True)
    return {'version': VERSION, 'repository': api.repo, 'candidate_tag': tag, 'candidate_sha': run['head_sha'],
            'candidate_run_id': run_id, 'candidate_run_attempt': attempt, 'candidate_release_id': release['id'],
            'assets': candidate_assets(release)}


def private_root():
    parent = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    root = parent / 'tracking-release-acceptance'
    require(not root.is_symlink() and root.resolve() == root)
    return root


def emit_output(name, value):
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':')) if not isinstance(value, str) else value
    require('\n' not in encoded and '\r' not in encoded)
    with Path(os.environ['GITHUB_OUTPUT']).open('a', encoding='utf-8') as stream:
        stream.write(name + '=' + encoded + '\n')


def prepare():
    global STAGE
    api = GitHub()
    authorize_dispatch(api)
    STAGE = 'candidate'
    plan = candidate_snapshot(api, os.environ['CANDIDATE_TAG'])
    plan.update(harness_sha=os.environ['GITHUB_SHA'], acceptance_run_id=int(os.environ['GITHUB_RUN_ID']),
                acceptance_run_attempt=int(os.environ['GITHUB_RUN_ATTEMPT']),
                dispatch_actor=os.environ['GITHUB_ACTOR'],
                triggering_actor=os.environ.get('GITHUB_TRIGGERING_ACTOR', os.environ['GITHUB_ACTOR']),
                trusted_keys=trusted_keys(os.environ['TRACKING_TRUSTED_UPDATE_KEYS']))
    root = private_root()
    root.mkdir(mode=0o700)
    for row in TARGETS:
        name = 'SOFT-Tracking-' + row['target'] + '-' + VERSION + '.zip'
        checksum = plan['assets'][name + '.sha256']
        path = root / (name + '.sha256')
        api.asset(checksum['id'], path, checksum['sha256'], checksum['size'])
        require(path.read_text(encoding='ascii').strip() == plan['assets'][name]['sha256'] + '  ' + name)
    require(len(TARGETS) == 8 and len({row['target'] for row in TARGETS}) == 8)
    emit_output('plan', plan)
    emit_output('matrix', {'include': TARGETS})
    emit_output('candidate_sha', plan['candidate_sha'])


def read_plan():
    plan = json_value(os.environ['ACCEPTANCE_PLAN'])
    require(plan['version'] == VERSION and plan['repository'] == os.environ['GITHUB_REPOSITORY'])
    require(plan['harness_sha'] == os.environ['GITHUB_SHA'])
    require(plan['acceptance_run_id'] == int(os.environ['GITHUB_RUN_ID']))
    require(plan['acceptance_run_attempt'] == int(os.environ['GITHUB_RUN_ATTEMPT']))
    require(plan['dispatch_actor'] == os.environ['GITHUB_ACTOR'])
    require(plan['triggering_actor'] == os.environ.get('GITHUB_TRIGGERING_ACTOR', os.environ['GITHUB_ACTOR']))
    trusted_keys(json.dumps(plan['trusted_keys']))
    return plan


def revalidate(api, plan):
    snapshot = candidate_snapshot(api, plan['candidate_tag'])
    require(all(plan[key] == value for key, value in snapshot.items()))


def row_for_target():
    return next(row for row in TARGETS if row['target'] == os.environ['RELEASE_TARGET'])


def check_native(row, plan):
    require('-'.join((*runtime_target(), row['profile'])) == row['target'])
    require(checkout_sha(ROOT / 'candidate') == plan['candidate_sha'])


def bounded_member(archive, name):
    require(archive.getinfo(name).file_size <= 65536)
    return json_value(archive.read(name))


def stage_candidate(path, destination, row, plan):
    build = {'os': row['target'].split('-')[0], 'architecture': row['arch']}
    prefix = row['target'] + '/'
    installer = 'SOFT-Tracking-Setup-' + VERSION + {'windows': '.exe', 'macos': '.dmg', 'linux': '.run'}[build['os']]
    expected = {prefix + name for name in ('manifest.json', 'release-' + VERSION + '.zip',
                'provenance.json', 'setup.json', 'dependencies.txt', 'size-report.json', installer)}
    if row['ui'] == 'electron':
        expected.add(prefix + 'desktop-package-lock.json')
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        require(len(entries) == len(expected) and {info.filename for info in entries} == expected)
        require(sum(info.file_size for info in entries) <= MAX_BUNDLE)
        require(all(not stat.S_ISLNK(info.external_attr >> 16) and not info.flag_bits & 1
                    and info.file_size <= MAX_BUNDLE for info in entries))
    manifest = stage_published_archive(path, destination / 'setup-payload', VERSION, row['target'], plan['trusted_keys'], build)
    require(manifest.get('ui') == row['ui'] and manifest.get('launcher_protocol') == 1)
    with zipfile.ZipFile(path) as archive:
        provenance = bounded_member(archive, prefix + 'provenance.json')
        require(set(provenance) == {'version', 'target', 'commit', 'run_url', 'build'})
        require(provenance['version'] == VERSION and provenance['target'] == row['target'])
        require(provenance['commit'] == plan['candidate_sha'])
        require(provenance['run_url'] == 'https://github.com/' + plan['repository'] + '/actions/runs/' + str(plan['candidate_run_id']))
        require(isinstance(provenance['build'], dict) and
                {'version', 'target', 'os', 'architecture', 'ui'} <= set(provenance['build']) and
                all(manifest.get(key) == value for key, value in provenance['build'].items()))
        setup = bounded_member(archive, prefix + 'setup.json')
        require(setup['version'] == VERSION and setup['target'] == row['target'] and setup.get('ui') == row['ui'])
        require(setup['file'] == installer)
        checksum = hashlib.sha256()
        with archive.open(prefix + installer) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                checksum.update(chunk)
        require(checksum.hexdigest() == sha(setup['sha256']))
    release = destination / 'setup-payload/release.zip'
    with zipfile.ZipFile(release) as archive:
        require(bounded_member(archive, 'build.json') == provenance['build'])
    shutil.copyfile(release, destination / ('release-' + VERSION + '.zip'))
    shutil.copyfile(destination / 'setup-payload/manifest.json', destination / 'manifest.json')
    return manifest


def download():
    global STAGE
    plan, row = read_plan(), row_for_target()
    check_native(row, plan)
    api = GitHub()
    revalidate(api, plan)
    raw = os.environ.pop('TRACKING_BASELINE_URLS', '')
    urls = baseline_urls(raw)
    for slots in urls.values():
        for slot in slots.values():
            # Register each URL, because GitHub cannot redact JSON substrings reliably.
            print('::add-mask::' + slot['url'].replace('%', '%25'), flush=True)
    root = private_root()
    root.mkdir(mode=0o700)
    pins = json_value((ROOT / 'tools/upgrade-baselines.json').read_bytes())
    for old in upgrade_baselines(VERSION):
        STAGE = 'baseline-' + old
        pin = pins[old][row['target']]
        require(type(pin[0]) is int and pin[0] > 0)
        download_url(urls[old][row['target']]['url'], root / (old + '.zip'), sha(pin[1]))
    del raw, urls
    STAGE = 'candidate'
    name = 'SOFT-Tracking-' + row['target'] + '-' + VERSION + '.zip'
    for filename in (name, name + '.sha256'):
        asset = plan['assets'][filename]
        api.asset(asset['id'], root / filename, asset['sha256'], asset['size'])
    require((root / (name + '.sha256')).read_text(encoding='ascii').strip() == plan['assets'][name]['sha256'] + '  ' + name)
    stage_candidate(root / name, root / 'current', row, plan)
    for old in upgrade_baselines(VERSION):
        STAGE = 'baseline-' + old
        stage_published_archive(root / (old + '.zip'), root / old / 'setup-payload', old, row['target'],
                                plan['trusted_keys'], {'os': row['target'].split('-')[0], 'architecture': row['arch']})


def smoke_environment(root):
    allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'DISPLAY', 'XAUTHORITY',
               'LANG', 'LC_ALL', 'DBUS_SESSION_BUS_ADDRESS', 'USER', 'USERNAME', 'LOGNAME'}
    if sys.platform == 'win32':
        # Windows resolves native known folders from USERPROFILE, not just APPDATA.
        allowed.add('USERPROFILE')
    env = {name: value for name, value in os.environ.items() if name.upper() in allowed}
    if sys.platform == 'win32':
        require(bool(env.get('USERPROFILE')))
    home, temporary = root / 'home', root / 'tmp'
    home.mkdir(exist_ok=True)
    temporary.mkdir(exist_ok=True)
    env.update(HOME=str(home), TMP=str(temporary), TEMP=str(temporary),
               TMPDIR=str(temporary), APPDATA=str(home), LOCALAPPDATA=str(home),
               TRACKING_SMOKE_CLIENT_ROOT=str(ROOT / 'candidate'), PYTHONNOUSERSITE='1')
    return env


def smoke_failure_stage(path):
    stages = {
        'AssertionError: Original installed launcher did not start the application': 'old-launcher-startup',
        'AssertionError: New frozen version failed its health check': 'new-frozen-health',
        'AssertionError: Application did not shut down gracefully': 'launcher-shutdown',
        'AssertionError: Graceful shutdown left child processes running': 'child-shutdown',
        'AssertionError: Ready marker was written without a running packaged Electron child': 'electron-child',
        'AssertionError: Electron renderer/RPC health was not confirmed': 'renderer-health',
        'AssertionError: Upgrade replaced the original bootstrap': 'bootstrap-retention',
        'agent_tracker.installer.InstallerError: setup_upgrade_failed': 'installer-health',
        'agent_tracker.installer.InstallerError: setup_close_required': 'installer-stop',
    }
    try:
        # Only map exact known diagnostics to constants; never emit captured text.
        with Path(path).open('rb') as log:
            log.seek(max(0, log.seek(0, os.SEEK_END) - 65536))
            lines = log.read(65536).decode('utf-8', errors='replace').splitlines()
        for line in reversed(lines):
            if line in stages:
                return stages[line]
    except OSError:
        pass
    return 'subprocess-failure'


def test():
    global STAGE
    plan, row = read_plan(), row_for_target()
    check_native(row, plan)
    root = private_root()
    env = smoke_environment(root)
    for old in upgrade_baselines(VERSION):
        STAGE = 'upgrade-' + old
        path = root / (old + '.private.log')
        try:
            with path.open('wb') as log:
                subprocess.run([sys.executable, str(ROOT / 'tools/smoke_update.py'), str(root / old),
                                str(root / 'current'), '--published'], env=env, cwd=root,
                               stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                               timeout=1200, check=True)
        except subprocess.CalledProcessError:
            STAGE += '-' + smoke_failure_stage(path)
            raise
        except subprocess.TimeoutExpired:
            STAGE += '-process-timeout'
            raise
        print(row['target'] + ' ' + old + ' PASS', flush=True)


def accepted_jobs(api, plan):
    jobs = api.jobs(plan['acceptance_run_id'], plan['acceptance_run_attempt'])
    native = [job for job in jobs if job['name'].startswith('accept-')]
    expected = {'accept-' + row['target'] for row in TARGETS}
    require(len(native) == len(expected) == 8 and {job['name'] for job in native} == expected)
    require(all(job['status'] == 'completed' and job['conclusion'] == 'success' for job in native))
    for job in native:
        steps = [step for step in job.get('steps', []) if step['name'] == SMOKE_STEP]
        require(len(steps) == 1 and steps[0].get('conclusion') == 'success')


def verify_remote_assets(api, release, plan, root, partial=False):
    assets = candidate_assets(release, partial=partial)
    for name, asset in assets.items():
        require({key: asset[key] for key in ('sha256', 'size')} ==
                {key: plan['assets'][name][key] for key in ('sha256', 'size')})
        api.asset(asset['id'], root / name, asset['sha256'], asset['size'])
        (root / name).unlink()
    return assets


def matching_stable_tag(tag, plan):
    require(isinstance(tag, dict) and tag.get('object', {}).get('type') == 'commit')
    require(tag['object'].get('sha') == plan['candidate_sha'])


def stable_release(api):
    release = api.api('releases/tags/v' + VERSION, absent=True)
    if release is not None:
        return release
    # Tag lookup may omit an unpublished draft; the write-token list includes it.
    matches = []
    for page in range(1, 11):
        releases = api.api(f'releases?per_page=100&page={page}')
        require(isinstance(releases, list))
        matches.extend(item for item in releases if item.get('tag_name') == 'v' + VERSION)
        require(len(matches) <= 1)
        if len(releases) < 100:
            return matches[0] if matches else None
    raise ValueError('Release inventory incomplete')


def promote():
    global STAGE
    STAGE = 'promotion'
    plan, api = read_plan(), GitHub()
    authorize_dispatch(api)
    accepted_jobs(api, plan)
    revalidate(api, plan)
    tag = api.api('git/ref/tags/v' + VERSION, absent=True)
    release = stable_release(api)
    if tag is not None:
        matching_stable_tag(tag, plan)
    if release is not None:
        require(tag is not None and release.get('draft') is True and release.get('tag_name') == 'v' + VERSION)
    root = private_root()
    root.mkdir(mode=0o700)
    body = ('Normal 3.2.0 binaries. All eight native targets and both genuine baseline upgrades per target passed. '
            'Candidate source: ' + plan['candidate_sha'] + '. Harness: ' + plan['harness_sha'] + '. '
            'Dispatch actor: ' + plan['dispatch_actor'] + '. Triggering actor: ' + plan['triggering_actor'] + '. '
            'Candidate: ' + plan['candidate_tag'] + ', Release ID ' + str(plan['candidate_release_id']) + '. '
            'Acceptance run: https://github.com/' + plan['repository'] + '/actions/runs/' + str(plan['acceptance_run_id']) +
            '/attempts/' + str(plan['acceptance_run_attempt']) + '. Production catalog and server acceptance are separate; no deployment performed.')
    body += '\n\nApproved current asset SHA-256 values:\n' + '\n'.join(
        '- `' + name + '`: `' + asset['sha256'] + '`' for name, asset in sorted(plan['assets'].items()))
    existing = verify_remote_assets(api, release, plan, root, partial=True) if release is not None else {}
    if tag is None:
        api.api('git/refs', 'POST', {'ref': 'refs/tags/v' + VERSION, 'sha': plan['candidate_sha']})
    if release is None:
        release = api.api('releases', 'POST', {'tag_name': 'v' + VERSION, 'target_commitish': plan['candidate_sha'],
                          'name': 'SOFT Tracking ' + VERSION, 'body': body, 'draft': True,
                          'prerelease': False, 'make_latest': 'false'})
    release_id = release['id']
    for row in TARGETS:
        name = 'SOFT-Tracking-' + row['target'] + '-' + VERSION + '.zip'
        for filename in (name, name + '.sha256'):
            asset = plan['assets'][filename]
            api.asset(asset['id'], root / filename, asset['sha256'], asset['size'])
        stage_candidate(root / name, root / 'inspection', row, plan)
        shutil.rmtree(root / 'inspection')
        for filename in (name, name + '.sha256'):
            if filename not in existing:
                api.upload(release_id, root / filename)
            (root / filename).unlink()
    revalidate(api, plan)
    remote = api.api('releases/' + str(release_id))
    require(remote['draft'] is True and remote['tag_name'] == 'v' + VERSION)
    # Verify actual uploaded bytes, not only the API-reported digest.
    verify_remote_assets(api, remote, plan, root)
    tag = api.api('git/ref/tags/v' + VERSION)
    matching_stable_tag(tag, plan)
    published = api.api('releases/' + str(release_id), 'PATCH', {'draft': False, 'prerelease': False,
                        'make_latest': 'false', 'name': 'SOFT Tracking ' + VERSION, 'body': body})
    require(published['draft'] is False and published['prerelease'] is False)


def cleanup():
    root = private_root()
    if root.exists():
        require(root.is_dir() and root.resolve() == Path(os.environ['RUNNER_TEMP']).resolve() / root.name)
        shutil.rmtree(root)


def main():
    try:
        hosted_context()
        require(len(sys.argv) == 2 and sys.argv[1] in ('prepare', 'download', 'test', 'promote', 'cleanup'))
        globals()[sys.argv[1]]()
    except BaseException:
        target = os.environ.get('RELEASE_TARGET', 'all')
        if target not in {row['target'] for row in TARGETS}:
            target = 'all'
        print(target + ' ' + VERSION + ' ' + STAGE + ' FAIL', flush=True)
        return 1
    print(os.environ.get('RELEASE_TARGET', 'all') + ' ' + VERSION + ' ' + STAGE + ' PASS', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
