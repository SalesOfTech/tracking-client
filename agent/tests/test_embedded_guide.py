import json
import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / 'agent_tracker'
SECTION_IDS = ['install', 'browsers', 'permissions', 'updates', 'troubleshooting']


class EmbeddedGuideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guides = json.loads((ROOT / 'assets/guide.json').read_text(encoding='utf-8'))

    def test_react_and_qt_share_the_complete_offline_contract(self):
        self.assertEqual({'en', 'ru', 'cs', 'uz'}, set(self.guides))
        for language, guide in self.guides.items():
            with self.subTest(language=language):
                self.assertEqual({'intro', 'note', 'sections'}, set(guide))
                self.assertTrue(guide['intro'].strip())
                self.assertTrue(guide['note'].strip())
                self.assertEqual(SECTION_IDS, [section['id'] for section in guide['sections']])
                for section in guide['sections']:
                    self.assertEqual({'id', 'title', 'steps'}, set(section))
                    self.assertTrue(section['title'].strip())
                    self.assertGreaterEqual(len(section['steps']), 3)
                    for step in section['steps']:
                        self.assertIsInstance(step, str)
                        self.assertTrue(step.strip())
                        self.assertNotIn('<', step)
                        self.assertNotIn('https://', step)

    def test_each_language_documents_all_browser_families_and_firefox_gate(self):
        for language, guide in self.guides.items():
            with self.subTest(language=language):
                browsers = ' '.join(guide['sections'][1]['steps'])
                for browser in ('Chrome', 'Edge', 'Opera', 'Brave', 'Vivaldi', 'Firefox', 'Safari', 'XPI'):
                    self.assertIn(browser, browsers)
                for page in ('chrome://extensions', 'edge://extensions', 'browser://extensions',
                             'opera://extensions', 'brave://extensions', 'vivaldi://extensions', 'about:addons'):
                    self.assertIn(page, browsers)
                self.assertIn('Qt', browsers)
        english = ' '.join(self.guides['en']['sections'][1]['steps'])
        self.assertIn('If no signed XPI is available, Firefox setup is not available', english)
        self.assertIn('Safari is unsupported', english)
        self.assertIn('Do not use temporary loading or turn off signature verification', english)

    def test_qt_help_does_not_open_the_legacy_html(self):
        bridge = (ROOT / 'ui/quick/bridge.py').read_text(encoding='utf-8')
        self.assertNotIn('setup.html', bridge)
        self.assertIn('guideRequested.emit()', bridge)
        self.assertIn("'assets/guide.json'", bridge)
        qml = (ROOT / 'ui/quick/Guide.qml').read_text(encoding='utf-8')
        self.assertIn('bridge.view.guide', qml)
        self.assertIn('Text.PlainText', qml)
        self.assertNotIn('openUrlExternally', qml)

    def test_installer_filenames_match_the_release_recipe(self):
        source = (ROOT.parents[1] / 'tools/release.py').read_text(encoding='utf-8')
        filenames = {node.value for node in ast.walk(ast.parse(source))
                     if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        for filename in ('SOFT-Tracking-Setup.exe', 'SOFT-Tracking-Setup.dmg',
                         'SOFT-Tracking-Setup.app', 'SOFT-Tracking-Setup.run'):
            self.assertIn(filename, filenames)
            for language, guide in self.guides.items():
                with self.subTest(language=language, filename=filename):
                    self.assertIn(filename, guide['sections'][0]['steps'][1])


if __name__ == '__main__':
    unittest.main()
