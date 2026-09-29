import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from agent_tracker.installer import bootstrap_code
from agent_tracker.macos_legacy import APP, owned_bundle, replace_macos_legacy


class MacInstallationTests(unittest.TestCase):
    def test_translocated_installer_matches_mounted_image(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            relative = Path('SOFT-Tracking-Setup.app/Contents/MacOS/setup')
            executable = root / 'AppTranslocation/random/d' / relative
            original = root / 'volume' / relative
            for path in (executable, original):
                path.parent.mkdir(parents=True)
                path.write_bytes(b'matching-installer')
            images = [{'image-path': '/Downloads/setup_'+'a'*32+'.dmg',
                       'system-entities': [{'mount-point':str(root/'volume')}]}]
            with patch('agent_tracker.installer.sys.platform','darwin'), \
                 patch('agent_tracker.installer.subprocess.run',return_value=Mock(stdout=plistlib.dumps({'images':images}))):
                self.assertEqual(bootstrap_code(executable), 'a'*32)
                original.write_bytes(b'other-installer')
                self.assertEqual(bootstrap_code(executable), '')

    def test_ambiguous_translocated_company_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            relative=Path('Setup.app/Contents/MacOS/setup')
            exe=root/'AppTranslocation/d'/relative
            exe.parent.mkdir(parents=True);exe.write_bytes(b'fixture')
            images=[]
            for code in ('a','b'):
                original=root/code/relative
                original.parent.mkdir(parents=True);original.write_bytes(b'fixture')
                images.append({'image-path':'setup_'+code*32+'.dmg','system-entities':[{'mount-point':str(root/code)}]})
            with patch('agent_tracker.installer.sys.platform','darwin'), \
                 patch('agent_tracker.installer.subprocess.run',return_value=Mock(stdout=plistlib.dumps({'images':images}))):
                self.assertEqual(bootstrap_code(exe), '')

    def test_only_current_home_bundle_is_retired(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder)/'home';root=home/'install';root.mkdir(parents=True)
            bundle=home/'Applications'/APP
            binary=bundle/'Contents/MacOS/SOFT Agent Tracking'
            binary.parent.mkdir(parents=True);binary.write_bytes(b'fixture')
            (bundle/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':'SOFT Agent Tracking'}))
            uid=bundle.stat().st_uid
            self.assertTrue(owned_bundle(bundle,home,uid))
            self.assertFalse(owned_bundle(bundle,home/'other',uid))
            self.assertFalse(owned_bundle(bundle,home,uid+1))
            process=Mock(pid=77)
            process.uids.return_value=Mock(real=uid)
            process.exe.return_value=str(binary)
            foreign=Mock()
            foreign.uids.return_value=Mock(real=uid+1)
            with patch('agent_tracker.macos_legacy.Path.home',return_value=home), \
                 patch('agent_tracker.macos_legacy.os.getuid',return_value=uid,create=True), \
                 patch('agent_tracker.macos_legacy.psutil.process_iter',return_value=[process,foreign]):
                report=replace_macos_legacy(root)
            self.assertEqual(report['state'],'complete')
            self.assertFalse(bundle.exists())
            self.assertEqual(len(list((home/'.Trash').glob('*.app'))),1)
            process.kill.assert_called_once();foreign.kill.assert_not_called()
