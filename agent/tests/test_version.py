import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker.core.files import atomic_json
from agent_tracker.core.version import application_version


class VersionTests(unittest.TestCase):
    def test_source_version_is_stable(self):
        root = Path(__file__).resolve().parents[2]
        version = application_version()
        self.assertRegex(version, r'^\d+\.\d+\.\d+$')
        for name in ('package.json', 'desktop/package.json'):
            self.assertEqual(json.loads((root / name).read_text())['version'], version)
        self.assertEqual(json.loads((root / 'extension/manifest.json').read_text())['version_name'], version)

    def test_frozen_version_belongs_to_running_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = Path(tmp) / 'versions' / '3.0.0'
            executable = payload / 'app' / 'tracking.exe'
            atomic_json(payload / 'build.json', {'version': '3.0.0'})
            atomic_json(Path(tmp) / 'current.json', {'version': '3.0.1'})
            with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(executable)):
                self.assertEqual('3.0.0', application_version())
                atomic_json(payload / 'build.json', {'version': '3.0.0-beta.9'})
                self.assertEqual('3.0.0-beta.9', application_version())


if __name__ == '__main__':
    unittest.main()
