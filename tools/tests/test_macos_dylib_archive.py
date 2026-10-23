"""Validate the standalone Python runtime used by Intel macOS CI images."""
from pathlib import Path
import tempfile
import unittest

from tools.macos_archive import verify_python_archive


class MacOSDylibArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary = self.root / 'library'
        self.binary.write_bytes(b'\xcf\xfa\xed\xfe' + b'\0' * 60)

    def archive(self, library='libpython3.10.dylib', entries=None):
        from PyInstaller.archive.writers import CArchiveWriter
        destination = self.root / 'runtime'
        CArchiveWriter(str(destination), entries if entries is not None else [
            ('libpython3.10.dylib', str(self.binary), True, 'b')], library)
        return destination

    def test_standalone_library_is_valid_without_a_framework(self):
        verify_python_archive(self.archive())

    def test_missing_or_different_bootloader_library_is_rejected(self):
        for library in ('Python', 'libpython3.11.dylib', '../libpython3.10.dylib', 'python310.dll'):
            with self.subTest(library=library), self.assertRaises(ValueError):
                verify_python_archive(self.archive(library))
        with self.assertRaises(ValueError):
            verify_python_archive(self.archive(entries=[]))

    def test_library_must_be_an_embedded_macho_binary(self):
        for kind in ('x', 'n'):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                verify_python_archive(self.archive(entries=[('libpython3.10.dylib', str(self.binary), True, kind)]))
        for content in (b'', b'not-a-library' * 8):
            self.binary.write_bytes(content)
            with self.subTest(content=content), self.assertRaises(ValueError):
                verify_python_archive(self.archive())

    def test_partial_framework_cannot_be_hidden_by_a_valid_dylib(self):
        with self.assertRaises(ValueError):
            verify_python_archive(self.archive(entries=[
                ('libpython3.10.dylib', str(self.binary), True, 'b'),
                ('Python.framework', str(self.binary), True, 'x')]))


if __name__ == '__main__':
    unittest.main()
