"""Native/frozen uninstall smoke; only self-created temporary files may be removed."""
import json
import os
from pathlib import Path
import plistlib
import stat
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch


def check_bootloader_parent(base, root):
    import psutil
    from . import macos_uninstall as mac
    parent_pid = 127
    extraction = base / '_MEI0000007fABC123'
    extraction.mkdir(mode=0o700)
    executable = str(root / 'versions/1.2.3/app/soft-tracking-app')
    arguments = ['--uninstall', '--installed-root', str(root)]
    parent, agent = Mock(pid=parent_pid), Mock(pid=126)
    for process in (parent, agent):
        process.exe.return_value = executable
        process.uids.return_value = SimpleNamespace(real=os.getuid(), effective=os.getuid())
    parent.cmdline.return_value = [executable, *arguments]
    agent.cmdline.return_value = [executable, '--autostart']
    with patch.object(sys, 'frozen', True, create=True), patch.object(sys, '_MEIPASS', str(extraction), create=True), \
            patch.object(sys, 'executable', executable), patch.object(sys, 'argv', [executable, *arguments]), \
            patch.object(psutil, 'Process') as current, patch.object(psutil, 'process_iter', return_value=[parent, agent]), \
            patch.object(psutil, 'wait_procs', return_value=([], [agent])) as wait:
        current.return_value.parents.return_value = [parent, agent]
        assert mac.bootloader_ancestors(os.getuid()) == {parent_pid}
        try:
            mac.stop_native_hosts(root)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Running installed agent was incorrectly exempted')
        wait.assert_called_once_with([agent], timeout=5)
        parent.terminate.assert_not_called()
        agent.terminate.assert_not_called()


def run(require_onefile=False):
    if sys.platform != 'darwin':
        return 1
    from . import macos_uninstall as mac, uninstaller
    from .native_host import host_manifest
    if require_onefile:
        # This must use real psutil ancestry and the actual frozen extraction path.
        assert getattr(sys, 'frozen', False), 'Onefile smoke must be frozen'
        assert mac.bootloader_ancestors(os.getuid()), 'Actual onefile bootloader parent was not recognized'
    with tempfile.TemporaryDirectory(prefix='soft-uninstall-smoke-') as temporary:
        base = Path(temporary).resolve()
        home = base / 'home'
        root = home / mac.WORKSPACE / 'install'
        payload = root / 'versions/1.2.3/app'
        payload.mkdir(parents=True)
        (root / 'current.json').write_text('{"version":"1.2.3"}')
        (payload / 'soft-tracking-app').write_bytes(b'fixture')
        (payload / 'soft-tracking-app').chmod(0o500)
        pending = root.parent / 'pending-events.json'
        pending.write_text('{"events":["durable pending fixture"]}')
        outside = base / 'outside'
        outside.mkdir()
        sentinel = outside / 'keep'
        sentinel.write_bytes(b'unchanged')
        framework = payload / 'UI.app/Contents/Frameworks/Test.framework/Versions/A'
        framework.mkdir(parents=True)
        (framework / 'binary').write_bytes(b'framework fixture')
        (framework.parent / 'Current').symlink_to('A', target_is_directory=True)
        (payload / 'external-alias').symlink_to(outside, target_is_directory=True)
        (payload / 'dangling').symlink_to('missing')
        framework.chmod(0o500)
        if hasattr(os, 'chflags'):
            os.chflags(framework / 'binary', stat.UF_IMMUTABLE)
        wrapper = home / mac.WRAPPER
        script = wrapper / 'Contents/MacOS/tracking'
        script.parent.mkdir(parents=True)
        script.write_text(mac.wrapper_script(root), encoding='utf-8')
        (wrapper / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'com.soft.tracking', 'CFBundleExecutable': 'tracking'}))
        other = home / 'Applications/Other.app'
        other.mkdir()
        (other / 'keep').write_bytes(b'other app')
        launch = home / 'Library/LaunchAgents' / (mac.LABEL + '.plist')
        launch.parent.mkdir(parents=True)
        launch.write_bytes(plistlib.dumps({'Label': mac.LABEL,
                                         'ProgramArguments': [str(root / 'soft-tracking'), '--autostart']}))
        with patch.dict(os.environ, HOME=str(home)), patch.object(mac, 'current_user', return_value=(home, os.getuid())):
            check_bootloader_parent(base, root)
            if getattr(sys, 'frozen', False):
                # Exercise the real parent/worker topology of the packaged executable.
                mac.stop_native_hosts(Path(sys.executable).parent)
            paths = mac.registration_paths()
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                engine = 'gecko' if path.parent.parent.name == 'Mozilla' else 'chromium'
                path.write_text(json.dumps(host_manifest(root / 'soft-tracking-host', engine)))
            foreign = paths[1]
            foreign.write_text(json.dumps(host_manifest(outside / 'foreign-host')))
            foreign_bytes = foreign.read_bytes()
            alias = paths[2]
            alias.unlink()
            alias.symlink_to(sentinel)
            with patch.object(mac.subprocess, 'run') as commands, patch.object(mac, 'stop_native_hosts'):
                assert uninstaller.can_uninstall(root)
                assert uninstaller.uninstall(root, confirm=lambda: False, cancel_code=2) == 2
                assert pending.exists() and wrapper.exists() and launch.exists()
                commands.assert_not_called()
                events = []
                assert uninstaller.uninstall(root, confirm=lambda: True, lifecycle_hook=lambda _: events.append('uninstall_requested')) == 0
                assert events == ['uninstall_requested']
                commands.assert_called_once()
                assert commands.call_args.args[0] == ['/bin/launchctl', 'bootout', 'gui/' + str(os.getuid()), str(launch)]
            assert not root.parent.exists() and not wrapper.exists() and not launch.exists()
            assert sentinel.read_bytes() == b'unchanged'
            assert foreign.read_bytes() == foreign_bytes and alias.is_symlink()
            assert (other / 'keep').read_bytes() == b'other app'
            assert all(not path.exists() for path in paths if path not in (foreign, alias))
    print('PASS: isolated native macOS uninstall cleanup')
    return 0
