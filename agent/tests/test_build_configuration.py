import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import release


class BuildConfigurationTests(unittest.TestCase):
    def test_every_frozen_entry_includes_signature_backend(self):
        for entry in ('runtime_entry.py', 'native_entry.py', 'bootstrap_entry.py',
                      'bootstrap_native_entry.py', 'setup_entry.py'):
            with self.subTest(entry=entry), patch.object(release.platform, 'system', return_value='Windows'), patch.object(release.subprocess, 'run') as run:
                release.freeze(entry, 'test', Path('dist'), Path('work'))
                args = run.call_args.args[0]
                imports = [args[i + 1] for i, value in enumerate(args)
                           if value == '--hidden-import']
                self.assertIn('_cffi_backend', imports)
                self.assertTrue(run.call_args.kwargs['check'])


if __name__ == '__main__':
    unittest.main()
