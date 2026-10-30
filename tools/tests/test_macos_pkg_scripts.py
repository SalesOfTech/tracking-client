"""Exercise test-only copies with macOS commands mocked, never the host installer."""

import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'macos_pkg_scripts'
NAMES = ('postinstall', 'user-prepare', 'runner.sh')

# These mocks are injected into temporary copies, not selected by production env vars.
COMMANDS = {
    '/usr/bin/stat', '/usr/bin/id', '/bin/launchctl', '/usr/bin/sudo',
    '/usr/bin/mktemp', '/usr/bin/ditto', '/usr/sbin/chown', '/bin/chmod',
    '/usr/bin/codesign', '/usr/bin/xattr', '/usr/bin/getconf', '/bin/rm', '/bin/cp',
    '/usr/bin/logger',
}
MOCK = r'''
import json, os, secrets, shutil, stat, sys
from pathlib import Path
base = Path(BASE)
config = json.loads((base / 'config.json').read_text())
log = base / 'events.jsonl'
cmd = Path(sys.argv[0]).name
args = sys.argv[1:]
role = os.environ.get('_PKG_TEST_ROLE', 'root')
events = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
with log.open('a') as stream:
    stream.write(json.dumps({'cmd': cmd, 'args': args, 'role': role}) + '\n')
if config.get('fail') == cmd and config.get('fail_role', role) == role:
    sys.exit(19)
if cmd == 'stat':
    if args[-1] == '/dev/console':
        count = sum(e['cmd'] == 'stat' and e['args'][-1] == '/dev/console' for e in events)
        values = config.get('console', ['501:alice'])
        print(values[min(count, len(values) - 1)])
    else:
        print(oct(stat.S_IMODE(Path(args[-1]).stat().st_mode))[2:])
elif cmd == 'id':
    print(config.get('account_uid', '501'))
elif cmd == 'launchctl':
    if args[0] == 'asuser':
        os.execv(args[2], args[2:])
    elif args[0] == 'print':
        count = sum(e['cmd'] == 'launchctl' and e['args'][0] == 'print' for e in events)
        if not config.get('gui', True) or (count and config.get('disconnect')):
            sys.exit(20)
    elif args[0] == 'submit':
        if config.get('submit_fail'):
            sys.exit(21)
    elif args[0] != 'remove':
        sys.exit(22)
elif cmd == 'sudo':
    assert args[:3] == ['-H', '-u', 'alice'], args
    os.environ['_PKG_TEST_ROLE'] = 'user'
    os.environ['HOME'] = str(base / 'home')
    os.execv(args[3], args[3:])
elif cmd == 'mktemp':
    assert args[0] == '-d'
    template = args[1]
    count = len(template) - len(template.rstrip('X'))
    path = Path(template[:-count] + secrets.token_hex(count)[:count])
    path.mkdir(mode=0o700)
    print(path)
elif cmd == 'ditto':
    shutil.copytree(args[0], args[1], symlinks=True)
elif cmd == 'cp':
    destination = Path(args[-1])
    for source in args[:-1]:
        shutil.copy2(source, destination / Path(source).name if destination.is_dir() else destination)
elif cmd == 'chmod':
    root = Path(args[-1])
    for path in [root, *root.rglob('*')]:
        if not path.is_symlink():
            mode = stat.S_IMODE(path.stat().st_mode)
            path.chmod(((mode | 0o444 | (0o111 if path.is_dir() or mode & 0o111 else 0)) & ~0o022))
elif cmd == 'getconf':
    assert args == ['DARWIN_USER_TEMP_DIR']
    print(config.get('temp_root', str(base / 'user temp') + '/'))
elif cmd == 'rm':
    path = Path(args[-1])
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)
elif cmd not in ('chown', 'codesign', 'xattr', 'logger'):
    sys.exit(23)
'''
NATIVE = r'''
import json, os, sys, time
from pathlib import Path
base = Path(BASE)
config = json.loads((base / 'config.json').read_text())
def event(name):
    with (base / 'events.jsonl').open('a') as stream:
        stream.write(json.dumps({'cmd': name, 'args': sys.argv[1:],
            'role': os.environ.get('_PKG_TEST_ROLE'), 'tmpdir': os.environ.get('TMPDIR'),
            'pgid': os.getpgrp(), 'parent_pgid': os.getpgid(os.getppid()),
            'health': os.environ.get('SOFT_TRACKING_HEALTH')}) + '\n')
event('native-start')
Path(os.environ['SOFT_TRACKING_HEALTH']).write_text(json.dumps({'ready': True, 'ui': 'electron'}))
if config.get('wait_native'):
    deadline = time.monotonic() + 10
    while not (base / 'close-window').exists() and time.monotonic() < deadline:
        time.sleep(0.01)
event('native-exit')
sys.exit(config.get('native_status', 0))
'''


@unittest.skipIf(os.name == 'nt', 'macOS PKG scripts require POSIX process and filesystem semantics')
class PackageScriptsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='soft-pkg-scripts-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.package = self.base / 'package scripts'
        self.package.mkdir()
        self.root_temp = self.base / 'root temp'
        self.root_temp.mkdir()
        self.user_temp = self.base / 'user temp'
        self.user_temp.mkdir(mode=0o700)
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        for command in COMMANDS:
            self.write_python(self.bin / Path(command).name, MOCK)
        self.config = {}
        self.configure()
        for name in NAMES:
            self.copy_script(name)
        self.app = self.package / 'SOFT-Tracking-Setup.app'
        self.executable = self.app / 'Contents/MacOS/SOFT-Tracking-Setup'
        self.executable.parent.mkdir(parents=True)
        self.write_python(self.executable, NATIVE)
        self.existing = self.base / 'home/Applications/SOFT Tracking.app/existing-data'
        self.existing.parent.mkdir(parents=True)
        self.existing.write_text('keep installed Tracking and its data')

    def write_python(self, path, body):
        path.write_text(f'#!{sys.executable}\nBASE = {str(self.base)!r}\n' + body)
        path.chmod(0o755)

    def copy_script(self, name, uid=None):
        source = (SCRIPTS / name).read_text()
        for command in COMMANDS:
            source = source.replace(command, str(self.bin / Path(command).name))
        source = source.replace('/private/tmp/SOFTTrackingPackage.XXXXXX',
                                shlex.quote(str(self.root_temp / 'SOFTTrackingPackage.XXXXXX')))
        # Bash's readonly UIDs cannot be overridden. Rewrite only isolated test copies.
        source = re.sub(r'\b(EUID|UID)\b', r'TEST_\1', source)
        role = 'root' if name == 'postinstall' else 'user'
        if uid is None:
            uid = 0 if role == 'root' else 501
        source = source.replace('set -u', f'set -u\nTEST_UID={uid}\nTEST_EUID={uid}\nexport _PKG_TEST_ROLE={role}', 1)
        (self.package / name).write_text(source)

    def configure(self, **values):
        self.config.update(values)
        (self.base / 'config.json').write_text(json.dumps(self.config))

    def events(self, command=None):
        path = self.base / 'events.jsonl'
        events = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [event for event in events if command is None or event['cmd'] == command]

    def run_script(self, name, *args):
        return subprocess.run(['/bin/bash', '-p', str(self.package / name), *map(str, args)],
                              capture_output=True, text=True, timeout=15)

    def install(self, volume='/'):
        return self.run_script('postinstall', '/tmp/setup.pkg', '/', volume)

    def assert_empty_stages(self):
        self.assertEqual(list(self.root_temp.iterdir()), [])
        self.assertEqual(list(self.user_temp.iterdir()), [])
        self.assertEqual(self.existing.read_text(), 'keep installed Tracking and its data')

    def prepared(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'STP_OK_SUBMITTED\n')
        submit = self.events('launchctl')[-1]
        self.assertEqual(submit['args'][0], 'submit')
        return Path(submit['args'][-2]), submit['args'][-1]

    def test_shell_syntax_and_fixed_production_boundary(self):
        for name in NAMES:
            with self.subTest(name=name):
                path = SCRIPTS / name
                subprocess.run(['/bin/bash', '-n', str(path)], check=True, capture_output=True)
                source = path.read_text()
                self.assertIn('export PATH=/usr/bin:/bin:/usr/sbin:/sbin', source)
                self.assertNotIn('_PKG_TEST', source)
                self.assertNotIn('--health-check', source)
                self.assertNotIn('spctl', source)
                self.assertNotIn('LaunchAgents', source)
                self.assertNotIn('/Applications/', source)
                self.assertTrue(path.stat().st_mode & stat.S_IXUSR)
        self.assertTrue((SCRIPTS / 'postinstall').read_text().startswith('#!/bin/bash -p\n'))
        for name in ('user-prepare', 'runner.sh'):
            self.assertNotIn('chown', (SCRIPTS / name).read_text())

    def test_refuses_non_root_and_non_boot_volume_before_copy(self):
        self.copy_script('postinstall', uid=501)
        result = self.install()
        self.assertEqual(result.stderr, 'STP_E_ROOT\n')
        self.copy_script('postinstall')
        for volume in ('/Volumes/Other', '', '/System/Volumes/Data'):
            with self.subTest(volume=volume):
                result = self.install(volume)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stderr, 'STP_E_VOLUME\n')
        self.assertEqual(self.events(), [])
        self.assert_empty_stages()

    def test_refuses_invalid_console_and_missing_gui(self):
        for console in ('0:root', '501:root', '501:loginwindow', '500:alice', '0:loginwindow',
                        'alice', '-501:alice', '0501:alice', '501:-alice', '501:alice\n502:bob'):
            with self.subTest(console=console):
                self.configure(console=[console])
                result = self.install()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stderr, 'STP_E_CONSOLE\n')
        self.configure(console=['501:alice'], gui=False)
        self.assertEqual(self.install().stderr, 'STP_E_CONSOLE\n')
        self.configure(gui=True, account_uid='502')
        self.assertEqual(self.install().stderr, 'STP_E_CONSOLE\n')
        self.assertEqual(self.events('mktemp'), [])
        self.assert_empty_stages()

    def test_missing_or_symlink_payload_is_rejected(self):
        target = self.base / 'source.app'
        self.app.rename(target)
        self.assertEqual(self.install().stderr, 'STP_E_PAYLOAD\n')
        self.app.symlink_to(target, target_is_directory=True)
        self.assertEqual(self.install().stderr, 'STP_E_PAYLOAD\n')
        self.assert_empty_stages()

    def test_successful_handoff_keeps_only_user_copy(self):
        userdir, label = self.prepared()
        self.assertEqual(list(self.root_temp.iterdir()), [])
        self.assertEqual(userdir.parent, self.user_temp)
        self.assertRegex(userdir.name, r'^SOFTTrackingGUI\.[a-zA-Z0-9]{8}$')
        self.assertEqual(stat.S_IMODE(userdir.stat().st_mode), 0o700)
        self.assertRegex(label, r'^com\.soft\.tracking\.setup\.501\.[a-zA-Z0-9]{6}$')
        self.assertEqual((userdir / 'runner.sh').read_text(), (self.package / 'runner.sh').read_text())
        signatures = self.events('codesign')
        self.assertEqual([e['role'] for e in signatures], ['root', 'user'])
        self.assertTrue(all(e['args'][:3] == ['--verify', '--deep', '--strict'] for e in signatures))
        self.assertEqual(self.events('xattr'), [{'cmd': 'xattr', 'role': 'user', 'args': [
            '-dr', '-s', 'com.apple.quarantine', str(userdir / self.app.name)]}])
        for event in self.events('chown') + self.events('chmod'):
            self.assertEqual(event['role'], 'root')
            self.assertEqual(Path(event['args'][-1]).parent, self.root_temp)
        for event in self.events('sudo'):
            self.assertEqual(event['args'][:3], ['-H', '-u', 'alice'])
        self.assertEqual(self.events('native-start'), [])
        self.assertEqual(self.existing.read_text(), 'keep installed Tracking and its data')

    def test_root_failures_clean_private_stage_without_running_payload(self):
        for command in ('mktemp', 'ditto', 'cp', 'chown', 'codesign', 'chmod'):
            with self.subTest(command=command):
                self.configure(fail=command, fail_role='root')
                result = self.install()
                self.assertNotEqual(result.returncode, 0)
                self.assertRegex(result.stderr, r'^STP_E_[A-Z_]+\n$')
                self.assert_empty_stages()
        self.assertEqual(self.events('sudo'), [])
        self.assertEqual(self.events('native-start'), [])

    def test_user_failures_clean_both_stages(self):
        for command in ('getconf', 'mktemp', 'ditto', 'codesign', 'xattr', 'cp'):
            with self.subTest(command=command):
                self.configure(fail=command, fail_role='user')
                result = self.install()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('STP_E_PREPARE\n', result.stderr)
                self.assert_empty_stages()
        self.assertEqual(self.events('native-start'), [])
        self.assertFalse(any(e['args'][0] == 'submit' for e in self.events('launchctl')))

    def test_signature_failure_never_removes_quarantine(self):
        self.configure(fail='codesign', fail_role='user')
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(self.events('xattr'), [])
        self.assert_empty_stages()

    def test_console_drift_or_disconnect_cleans_user_copy_as_user(self):
        for values in ({'console': ['501:alice', '502:bob']},
                       {'console': ['501:alice', '0:loginwindow']},
                       {'console': ['501:alice'], 'disconnect': True}):
            with self.subTest(values=values):
                self.configure(**values)
                result = self.install()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stderr, 'STP_E_CONSOLE_CHANGED\n')
                self.assert_empty_stages()
                # Reset call counts for the next isolated console transition.
                (self.base / 'events.jsonl').unlink()

    def test_submit_failure_cleanup_is_not_root_userdir_mutation(self):
        self.configure(submit_fail=True)
        result = self.install()
        self.assertEqual(result.stderr, 'STP_E_SUBMIT\n')
        removals = self.events('rm')
        self.assertEqual([e['role'] for e in removals], ['user', 'root'])
        self.assertEqual(Path(removals[0]['args'][-1]).parent, self.user_temp)
        self.assertEqual(Path(removals[1]['args'][-1]).parent, self.root_temp)
        self.assert_empty_stages()

    def test_cleanup_rejects_other_paths_and_symlinks(self):
        outside = self.base / 'SOFTTrackingGUI.abcdefgh'
        outside.mkdir()
        linked = self.user_temp / 'SOFTTrackingGUI.abcdefgh'
        linked.symlink_to(outside, target_is_directory=True)
        for path in (self.user_temp, outside, linked, self.user_temp / '../SOFTTrackingGUI.abcdefgh'):
            with self.subTest(path=path):
                result = self.run_script('user-prepare', '--cleanup', path)
                self.assertEqual(result.stderr, 'STP_E_USER_PATH\n')
        self.assertTrue(outside.exists())
        self.assertEqual(self.events('rm'), [])

    def test_user_scripts_refuse_root(self):
        for name in ('user-prepare', 'runner.sh'):
            self.copy_script(name, uid=0)
            self.assertEqual(self.run_script(name, 'x', 'y').stderr, 'STP_E_USER\n')
        self.assertEqual([e for e in self.events() if e['cmd'] != 'logger'], [])

    def test_canonical_temp_path_and_real_gui_lifetime(self):
        alias = self.base / 'temp-alias'
        alias.symlink_to(self.user_temp, target_is_directory=True)
        self.configure(temp_root=str(alias) + '/', wait_native=True)
        userdir, label = self.prepared()
        self.assertEqual(userdir.parent, self.user_temp)
        command = ['/bin/bash', str(userdir / 'runner.sh'), str(userdir), label]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            marker = userdir / 'setup-ready.json'
            deadline = time.monotonic() + 5
            while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(marker.exists())
            self.assertIsNone(process.poll())
            self.assertTrue(userdir.exists())
            self.assertEqual(json.loads(marker.read_text()), {'ready': True, 'ui': 'electron'})
            start = self.events('native-start')[0]
            self.assertEqual(start['args'], ['--prepared-setup-launch'])
            self.assertEqual(start['role'], 'user')
            self.assertEqual(start['tmpdir'], str(self.user_temp) + '/')
            self.assertEqual(start['health'], str(marker))
            self.assertNotEqual(start['pgid'], start['parent_pgid'])
        finally:
            (self.base / 'close-window').touch()
            stdout, stderr = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(stdout, '')
        events = self.events()
        exit_index = next(i for i, e in enumerate(events) if e['cmd'] == 'native-exit')
        remove_index = next(i for i, e in enumerate(events) if e['cmd'] == 'rm' and e['role'] == 'user')
        self.assertLess(exit_index, remove_index)
        self.assertEqual(events[-1], {'cmd': 'launchctl', 'args': ['remove', label], 'role': 'user'})
        self.assert_empty_stages()

    def test_native_failure_still_cleans_directory_and_job(self):
        self.configure(native_status=9)
        userdir, label = self.prepared()
        result = self.run_script('runner.sh', userdir, label)
        self.assertEqual(result.stderr, 'STP_E_SETUP\n')
        self.assertEqual(self.events()[-1]['args'], ['remove', label])
        self.assert_empty_stages()

    def test_runner_invalid_path_removes_job_without_deleting_path(self):
        label = 'com.soft.tracking.setup.501.abcdef'
        result = self.run_script('runner.sh', self.user_temp, label)
        self.assertEqual(result.stderr, 'STP_E_USER_PATH\n')
        self.assertTrue(self.user_temp.exists())
        self.assertEqual(self.events('rm'), [])
        self.assertEqual(self.events()[-1]['args'], ['remove', label])

    def test_runner_refuses_foreign_job_label(self):
        result = self.run_script('runner.sh', self.user_temp, 'com.other.job')
        self.assertEqual(result.stderr, 'STP_E_LABEL\n')
        self.assertEqual([e for e in self.events() if e['cmd'] != 'logger'], [])


if __name__ == '__main__':
    unittest.main()
