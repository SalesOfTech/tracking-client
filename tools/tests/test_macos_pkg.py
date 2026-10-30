import sys
import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from macos_pkg import distribution, instructions, IDENTIFIER


class PackageTests(unittest.TestCase):
    def test_intel_package_architecture_and_separate_receipt(self):
        root = ET.fromstring(distribution('3.5.2', 'x64'))
        self.assertEqual(root.find('options').get('hostArchitectures'), 'x86_64')
        self.assertEqual(root.find('pkg-ref').get('id'), 'com.soft.tracking.setup.intel')
        with self.assertRaises(ValueError):
            distribution('3.5.2', 'x86')

    def test_package_version_follows_payload(self):
        for version in ('3.5.2', '3.6.1-rc.2'):
            self.assertEqual(ET.fromstring(distribution(version)).find('pkg-ref').get('version'),
                             version.split('-')[0])
        for version in ('', '../3.5.2', '3.5.2\n', 'v3.5.2'):
            with self.assertRaises(ValueError):
                distribution(version)

    def test_only_arm_system_volume_admin_preparation(self):
        root = ET.fromstring(distribution())
        self.assertEqual(root.find('options').get('hostArchitectures'), 'arm64')
        self.assertEqual(root.find('options').get('rootVolumeOnly'), 'true')
        self.assertEqual(root.find('domains').get('enable_anywhere'), 'false')
        self.assertEqual(root.find('allowed-os-versions/os-version').get('min'), '13.0')
        reference = root.find('pkg-ref')
        self.assertEqual(reference.get('id'), IDENTIFIER)
        self.assertEqual(reference.get('auth'), 'Root')
        self.assertEqual(reference.text, 'component.pkg')

    def test_instructions_embed_localized_key_first_flow(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            instructions(root)
            self.assertEqual({path.name for path in root.iterdir()}, {'en.lproj', 'cs.lproj', 'ru.lproj'})
            for language in ('en', 'cs', 'ru'):
                for page in ('welcome.html', 'conclusion.html'):
                    text = (root / (language + '.lproj') / page).read_text(encoding='utf-8')
                    self.assertIn('SOFT Tracking', text)
                    self.assertNotIn('http', text)
            self.assertIn('NOT installed', (root / 'en.lproj/conclusion.html').read_text(encoding='utf-8'))
