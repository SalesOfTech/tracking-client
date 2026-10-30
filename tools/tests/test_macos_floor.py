from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import check_macos_floor as floor


class MacFloorTests(unittest.TestCase):
    def test_arm_load_command_floor_and_missing_arm_are_checked(self):
        command = (SimpleNamespace(cmd=floor.LC_BUILD_VERSION), SimpleNamespace(minos=11 << 16), b'')
        header = SimpleNamespace(header=SimpleNamespace(cputype=floor.ARM64), commands=[command])
        with patch.object(floor, 'MachO', return_value=SimpleNamespace(headers=[header])):
            self.assertEqual(11 << 16, floor.minimum('fixture'))
            header.header.cputype = 0x01000007
            with self.assertRaises(ValueError): floor.minimum('fixture')

    def test_resources_are_not_treated_as_native_code_but_bad_libraries_fail(self):
        for name, data, fails in [('qt.conf', b'\n# resource', False), ('bad.dylib', b'broken', True)]:
            archive = SimpleNamespace(toc={'Python': (0, 'b'), name: (0, 'b')},
                extract=lambda item: b'\xcf\xfa\xed\xfecode' if item == 'Python' else data)
            with patch.object(floor, 'CArchiveReader', return_value=archive), \
                    patch.object(floor, 'minimum', return_value=11 << 16):
                if fails:
                    with self.assertRaises(ValueError): floor.verify(Path('fixture'))
                else:
                    floor.verify(Path('fixture'))

    def test_newer_dependency_cannot_pass_by_lowering_metadata(self):
        archive = SimpleNamespace(toc={'Python': (0, 'b')}, extract=lambda _: b'\xcf\xfa\xed\xfecode')
        with patch.object(floor, 'CArchiveReader', return_value=archive), \
                patch.object(floor, 'minimum', side_effect=[11 << 16, 12 << 16]):
            with self.assertRaisesRegex(ValueError, 'Embedded binary requires newer macOS'):
                floor.verify(Path('fixture'))
