import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import smoke_package


class PublishedPayloadTests(unittest.TestCase):
    def test_build_smoke_still_requires_offline_guide(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)
            (bundle / 'setup-build.json').write_text(json.dumps({'os': 'macos'}))
            with patch.object(smoke_package, 'install') as install:
                with self.assertRaisesRegex(AssertionError, 'Missing offline setup guide'):
                    smoke_package.main(bundle)
                install.assert_not_called()

    def test_published_runtime_reaches_verified_installer_without_external_guide(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)
            (bundle / 'setup-build.json').write_text(json.dumps({'os': 'macos'}))
            with patch.object(smoke_package, 'install', side_effect=RuntimeError('install-reached')) as install:
                with self.assertRaisesRegex(RuntimeError, '^install-reached$'):
                    smoke_package.main(bundle, published_payload=True)
                self.assertEqual(install.call_args.args[0], bundle)
                self.assertFalse(install.call_args.kwargs['integrate'])


if __name__ == '__main__':
    unittest.main()
