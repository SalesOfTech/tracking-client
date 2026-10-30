import plistlib
from pathlib import Path
import tempfile
import unittest
from tools.macos_archive import verify_setup_branding


class MacInstallerBrandingTests(unittest.TestCase):
    def test_actual_bundle_must_contain_matching_icon_identity_and_os_floor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / 'setup.app'
            resources = bundle / 'Contents/Resources'
            resources.mkdir(parents=True)
            icon = root / 'brand.icns'
            icon.write_bytes(b'icns-brand-fixture')
            bundled = resources / 'brand.icns'
            bundled.write_bytes(icon.read_bytes())
            metadata = dict(CFBundleName='SOFT Tracking Setup', CFBundleDisplayName='SOFT Tracking Setup',
                            CFBundleIdentifier='com.soft.tracking.setup', CFBundleIconFile='brand.icns',
                            LSMinimumSystemVersion='11.0')
            plist = bundle / 'Contents/Info.plist'
            plist.write_bytes(plistlib.dumps(metadata))
            verify_setup_branding(bundle, icon, '11.0')
            for field, value in [('CFBundleDisplayName', 'Python'), ('CFBundleIdentifier', 'Python'),
                                 ('CFBundleIconFile', '../brand.icns'), ('LSMinimumSystemVersion', '13.0')]:
                plist.write_bytes(plistlib.dumps(dict(metadata, **{field: value})))
                with self.assertRaises(ValueError):
                    verify_setup_branding(bundle, icon, '11.0')
            plist.write_bytes(plistlib.dumps(metadata))
            bundled.write_bytes(b'Python default icon')
            with self.assertRaises(ValueError):
                verify_setup_branding(bundle, icon, '11.0')
