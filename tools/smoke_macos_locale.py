"""Probe the exact frozen DMG installer after cold PKG acceptance on a hosted Mac."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import tempfile

from smoke_helpers import stop_tree


CASES = [('ru-RU', 'en_NL', 'ru', 'NL'),
         ('cs-CZ', 'en_NL', 'cs', 'NL'),
         ('en-US', 'en_RU', 'en', 'RU')]
KEYS = ('AppleLanguages', 'AppleLocale')


class ArgumentPreferencesUnavailable(AssertionError):
    pass


def run(*args):
    return subprocess.run([str(arg) for arg in args], check=True, capture_output=True, timeout=120)


def require_runner():
    if (platform.system() != 'Darwin' or os.getuid() == 0 or os.geteuid() != os.getuid()
            or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'):
        raise RuntimeError('Requires a non-root isolated GitHub-hosted macOS runner')
    uid = os.getuid()
    if int(run('/usr/bin/stat', '-f', '%u', '/dev/console').stdout) != uid:
        raise RuntimeError('Must run as the same console user as the PKG smoke')
    return uid


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def preferences():
    values = plistlib.loads(run('/usr/bin/defaults', 'export', 'NSGlobalDomain', '-').stdout)
    saved = {key: values[key] for key in KEYS if key in values}
    if ('AppleLanguages' in saved and
            (not isinstance(saved['AppleLanguages'], list)
             or not all(isinstance(tag, str) for tag in saved['AppleLanguages']))):
        raise ValueError('Unexpected AppleLanguages preference type')
    if 'AppleLocale' in saved and not isinstance(saved['AppleLocale'], str):
        raise ValueError('Unexpected AppleLocale preference type')
    return saved


def write_preference(key, value):
    if key == 'AppleLanguages':
        run('/usr/bin/defaults', 'write', '-g', key, '-array', *value)
    else:
        run('/usr/bin/defaults', 'write', '-g', key, '-string', value)


@contextmanager
def temporary_preferences(tags, locale):
    require_runner()
    saved = preferences()
    try:
        write_preference('AppleLanguages', tags)
        write_preference('AppleLocale', locale)
        yield
    finally:
        # Restore only our two keys, including absence; never import a whole preferences domain.
        failures = []
        for key in KEYS:
            try:
                if key in saved:
                    write_preference(key, saved[key])
                elif key in preferences():
                    run('/usr/bin/defaults', 'delete', '-g', key)
            except Exception as error:
                failures.append(type(error).__name__)
        if failures or preferences() != saved:
            raise RuntimeError('Runner language preference restoration failed')


def invoke(executable, marker, arguments):
    env = dict(os.environ)
    for key in list(env):
        if key.startswith('SOFT_TRACKING_') or key in ('PYTHONPATH', 'PYTHONHOME'):
            del env[key]
    env.update(SOFT_TRACKING_LOCALE_SMOKE=str(marker), LC_ALL='en_US.UTF-8', LANG='en_US.UTF-8')
    # --health-check makes native launcher failures headless; locale mode exits before any UI.
    command = [str(executable), '--locale-self-test', '--health-check'] + arguments
    process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        process.communicate(timeout=180)
        if process.returncode:
            error = Path(str(marker) + '.error')
            detail = error.read_text() if error.is_file() else 'No setup diagnostic marker'
            raise AssertionError('Frozen locale probe failed: ' + detail)
        return json.loads(marker.read_text(encoding='utf-8'))
    finally:
        if process.poll() is None:
            stop_tree(process)
        process.wait(timeout=15)


def validate(result, executable, engine, uid, case):
    tag, _, language, country = case
    assert result['frozen'] is True, 'Probe was not frozen'
    assert result['uid'] == result['euid'] == uid, 'Probe ran as a different user'
    assert result['architecture'] == platform.machine(), 'Probe architecture differs from native runner'
    assert Path(result['executable']).resolve() == executable.resolve(), 'Different installer wrapper'
    assert Path(result['process_executable']).resolve() == engine.resolve(), 'Different frozen engine'
    root = Path(result['frozen_root']).resolve()
    assert engine.parent.parent.resolve() in root.parents, 'Imports outside the sealed engine'
    assert root in Path(result['i18n_file']).resolve().parents, 'Locale module loaded outside frozen bundle'
    tags = result['native_tags']
    if not tags or tags[0] != tag or result['native_region'] != country:
        raise ArgumentPreferencesUnavailable(
            'CoreFoundation returned {!r}/{!r}, expected {!r}/{!r}'.format(
                tags, result['native_region'], tag, country))
    assert result['system_tags'] == tags and result['system_region'] == country, 'OS detection used fallback'
    assert result['detected'] == result['automatic_enrollment'] == language, 'Incorrect automatic language'
    languages = result['languages']
    assert {'ru', 'cs', 'en', 'uz'} <= set(languages), 'Existing language choices missing'
    assert all(languages.values()), 'Empty language label'
    assert set(result['install_labels']) == set(languages) and all(result['install_labels'].values())
    expected = {key: key for key in languages}
    for key in ('overrides', 'saved_overrides', 'manual_enrollment'):
        assert result[key] == expected, 'Manual language override lost: ' + key


def probe(executable, engine, uid, root, allow_preferences):
    reports = []
    for case in CASES:
        tag, locale, language, _ = case
        tags = [tag] if language == 'en' else [tag, 'en-US']
        array = '(' + ', '.join(json.dumps(item) for item in tags) + ')'
        result = invoke(executable, root / (language + '-arguments.json'),
                        ['-AppleLanguages', array, '-AppleLocale', locale])
        mode = 'arguments'
        try:
            validate(result, executable, engine, uid, case)
        except ArgumentPreferencesUnavailable:
            if not allow_preferences:
                raise
            with temporary_preferences(tags, locale):
                result = invoke(executable, root / (language + '-preferences.json'), [])
                validate(result, executable, engine, uid, case)
            mode = 'restored-runner-preferences'
        reports.append(dict(result, preference_mode=mode))
    return reports


def remove_created_cache(cache, paths, existing, cache_existed):
    for path in paths:
        if path in existing or not path.exists():
            continue
        if path.is_symlink() or path.stat().st_uid != os.getuid():
            raise RuntimeError('Unexpected locale-probe cache ownership or link')
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    if not cache_existed and cache.exists():
        # Refuse to remove unexpected entries belonging to another task.
        cache.rmdir()


def check(image, evidence=None, allow_preferences=False):
    uid = require_runner()
    before = preferences()
    cache = Path.home() / 'Library/Caches/SOFTTrackingSetup-engine'
    cache_existed = cache.exists()
    existing = set(cache.iterdir()) if cache_existed else set()
    paths = []
    attached = False
    with tempfile.TemporaryDirectory(prefix='soft-macos-locale-') as temporary:
        root = Path(temporary)
        mount = root / 'image'
        mount.mkdir()
        try:
            run('/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint', mount, image)
            attached = True
            app = mount / 'SOFT-Tracking-Setup.app'
            run('/usr/bin/codesign', '--verify', '--deep', '--strict', app)
            executable = app / 'Contents/MacOS/SOFT-Tracking-Setup'
            archives = list((app / 'Contents/Resources').glob('engine-*.zip'))
            assert len(archives) == 1, 'Expected exactly one native frozen engine'
            archive_hash = digest(archives[0])
            assert archives[0].name == 'engine-' + archive_hash + '.zip', 'Engine archive digest mismatch'
            paths = [cache / archive_hash, cache / ('.engine-' + archive_hash + '.lock')]
            engine = paths[0] / 'SOFT Tracking Engine.app/Contents/MacOS/soft-tracking-engine'
            wrapper_hash = digest(executable)
            reports = probe(executable, engine, uid, root, allow_preferences)
            assert digest(executable) == wrapper_hash and digest(archives[0]) == archive_hash
            run('/usr/bin/codesign', '--verify', '--deep', '--strict', engine.parent.parent.parent)
            assert preferences() == before, 'Language preferences changed during probe'
            summary = {'ok': True, 'uid': uid, 'wrapper_sha256': wrapper_hash,
                       'engine_archive_sha256': archive_hash, 'cases': reports}
        finally:
            try:
                remove_created_cache(cache, paths, existing, cache_existed)
            finally:
                if attached:
                    run('/usr/bin/hdiutil', 'detach', mount)
    if evidence:
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('PASS: exact frozen installer, console UID, native ru/cs/en, manual overrides, all languages; preferences restored and temporary cache cleaned')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--allow-runner-preferences', action='store_true',
                        help='If process arguments are ignored, temporarily set and restore hosted-runner preferences')
    args = parser.parse_args()
    check(args.image.resolve(), args.evidence, args.allow_runner_preferences)
