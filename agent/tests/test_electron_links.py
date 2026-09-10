import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_tracker import electron_links as links


class ElectronLinkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'electron'
        self.framework = 'SoftTrackingUI.app/Contents/Frameworks/Example.framework'
        self.file(self.framework + '/Versions/A/Example', b'binary')
        self.file(self.framework + '/Versions/A/Resources/info', b'resource')
        self.file('SoftTrackingUI.app/Contents/MacOS/SoftTrackingUI', b'launcher')
        self.aliases = [
            {'path': self.framework + '/Resources', 'target': 'Versions/Current/Resources'},
            {'path': self.framework + '/Example', 'target': 'Versions/Current/Example'},
            {'path': self.framework + '/Versions/Current', 'target': 'A'},
        ]

    def file(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        return path

    def document(self, entries=None):
        return {'version': 1, 'links': self.aliases if entries is None else entries}

    def map(self, document=None):
        return self.file(links.MAP_NAME, json.dumps(self.document() if document is None else document).encode())

    def symlink(self, path, target, directory=False):
        try:
            path.symlink_to(target, target_is_directory=directory)
        except OSError as error:
            if os.name == 'nt' and getattr(error, 'winerror', None) == 1314:
                self.skipTest('Windows account lacks symlink privilege; POSIX CI must execute this test')
            raise

    def require_symlink(self):
        path = self.root / 'probe'
        self.symlink(path, 'SoftTrackingUI.app', directory=True)
        path.unlink()

    def assert_pristine(self):
        for entry in self.aliases:
            self.assertFalse(os.path.lexists(self.root / entry['path']))

    def test_missing_map_leaves_regular_bundle_unchanged(self):
        links.restore_links(self.root)
        self.assert_pristine()
        self.assertFalse((self.root / links.MAP_NAME).exists())

    def test_plan_resolves_dependencies_before_dependents_without_writing(self):
        plan = links.plan_links(self.root, self.document())
        self.assertEqual('/'.join(plan[0][0]), self.framework + '/Versions/Current')
        self.assertEqual('/'.join(plan[1][2]), self.framework + '/Versions/A/Resources')
        self.assert_pristine()

    def test_restoration_is_idempotent_and_preserves_original_targets(self):
        self.require_symlink()
        self.map()
        before = (self.root / links.MAP_NAME).read_bytes()
        links.restore_links(self.root)
        for entry in self.aliases:
            self.assertEqual(os.readlink(self.root / entry['path']), entry['target'])
        self.assertEqual((self.root / self.framework / 'Example').read_bytes(), b'binary')
        self.assertEqual((self.root / self.framework / 'Resources/info').read_bytes(), b'resource')
        with patch.object(links, '_create_link', side_effect=AssertionError('Already restored')):
            links.restore_links(self.root)
        self.assertEqual((self.root / links.MAP_NAME).read_bytes(), before)

    def test_interrupted_restoration_resumes_exact_existing_links(self):
        self.require_symlink()
        self.map()
        real_create = links._create_link
        created = []
        def interrupt(*args):
            if created:
                raise OSError('Simulated interruption')
            real_create(*args)
            created.append(args)
        with patch.object(links, '_create_link', side_effect=interrupt), self.assertRaises(OSError):
            links.restore_links(self.root)
        links.restore_links(self.root)
        self.assertEqual((self.root / self.framework / 'Resources/info').read_bytes(), b'resource')

    def test_invalid_map_shapes_versions_and_limits_are_rejected(self):
        invalid = [[], {}, {'version': True, 'links': []}, {'version': 2, 'links': []},
                   {'version': 1, 'links': {}}, {'version': 1, 'links': [], 'extra': True},
                   self.document([{}]), self.document([dict(self.aliases[0], extra=True)]),
                   self.document(self.aliases * (links.MAX_LINKS + 1))]
        for document in invalid:
            with self.subTest(document=str(document)[:80]):
                self.map(document)
                with self.assertRaises(ValueError):
                    links.restore_links(self.root)
                self.assert_pristine()

    def test_duplicate_json_keys_and_oversized_or_invalid_json_rejected(self):
        for raw in (b'{"version":1,"version":1,"links":[]}',
                    b'{"version":1,"links":[{"path":"a","path":"b","target":"a"}]}',
                    b'x' * (links.MAX_MAP_BYTES + 1), b'{', b'\xff'):
            self.file(links.MAP_NAME, raw)
            with self.assertRaises(ValueError):
                links.restore_links(self.root)
            self.assert_pristine()

    def test_duplicate_casefolded_paths_and_alias_parents_rejected(self):
        first = self.aliases[0]
        invalid = [[first, first], [first, dict(first, path=first['path'].replace('Resources', 'resources'))],
                   [first, {'path': first['path'] + '/child', 'target': 'info'}]]
        for entries in invalid:
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                links.plan_links(self.root, self.document(entries))
        self.assert_pristine()

    def test_alias_paths_and_targets_cannot_escape_or_use_platform_tricks(self):
        bad_paths = ['/tmp/link', '../link', 'SoftTrackingUI.app/../link', 'outside/link',
                     'SoftTrackingUI.app/Contents//link', 'SoftTrackingUI.app/Contents/./link',
                     'SoftTrackingUI.app/Contents/link:stream', 'SoftTrackingUI.app/Contents/link\\child',
                     'SoftTrackingUI.app/Contents/link\x00', 'SoftTrackingUI.app/Contents/' + 'a' * 1025]
        for path in bad_paths:
            with self.subTest(path=path), self.assertRaises(ValueError):
                links.plan_links(self.root, self.document([{'path': path, 'target': 'A'}]))
        bad_targets = ['/tmp/file', 'C:/file', '\\server\\file', '../../../..',
                       '../../../../outside/../SoftTrackingUI.app', 'A//Example', 'A/Example/../Example']
        for target in bad_targets:
            with self.subTest(target=target), self.assertRaises((ValueError, FileNotFoundError)):
                links.plan_links(self.root, self.document([dict(self.aliases[-1], target=target)]))
        self.assert_pristine()

    def test_internal_parent_relative_target_is_supported(self):
        self.require_symlink()
        self.map(self.document([{'path': self.framework + '/Versions/Alias', 'target': '../Versions/A'}]))
        links.restore_links(self.root)
        self.assertEqual((self.root / self.framework / 'Versions/Alias/Example').read_bytes(), b'binary')

    def test_missing_target_is_rejected_before_any_alias_is_created(self):
        self.map(self.document(self.aliases + [{'path': self.framework + '/bad', 'target': 'missing'}]))
        with self.assertRaises(FileNotFoundError):
            links.restore_links(self.root)
        self.assert_pristine()

    def test_direct_indirect_and_directory_traversal_cycles_are_rejected(self):
        for entries in ([{'path': self.framework + '/x', 'target': 'x'}],
                        [{'path': self.framework + '/x', 'target': 'y'},
                         {'path': self.framework + '/y', 'target': 'x'}],
                        [{'path': self.framework + '/x', 'target': '.'}],
                        [{'path': self.framework + '/Versions/A/x', 'target': '../B'},
                         {'path': self.framework + '/Versions/B/y', 'target': '../A'}]):
            (self.root / self.framework / 'Versions/B').mkdir(exist_ok=True)
            self.map(self.document(entries))
            with self.subTest(entries=entries), self.assertRaisesRegex(ValueError, 'Cyclic'):
                links.restore_links(self.root)
            self.assert_pristine()

    def test_existing_regular_file_or_directory_is_never_overwritten(self):
        self.map()
        target = self.root / self.aliases[0]['path']
        for directory in (False, True):
            if directory:
                target.mkdir()
            else:
                target.write_bytes(b'keep')
            with self.assertRaisesRegex(ValueError, 'replace'):
                links.restore_links(self.root)
            if directory:
                self.assertTrue(target.is_dir())
                target.rmdir()
            else:
                self.assertEqual(target.read_bytes(), b'keep')
                target.unlink()
            self.assert_pristine()

    def test_existing_wrong_or_dangling_symlink_is_rejected(self):
        for target in ('Versions/A/Resources', '/outside', 'missing'):
            path = self.root / self.aliases[0]['path']
            self.symlink(path, target, directory=True)
            self.map()
            with self.assertRaisesRegex(ValueError, 'replace'):
                links.restore_links(self.root)
            self.assertEqual(os.readlink(path), target)
            path.unlink()

    def test_unlisted_parent_symlink_and_target_symlink_are_rejected(self):
        self.symlink(self.root / self.framework / 'hidden', 'Versions/A', directory=True)
        for entry in ({'path': self.framework + '/hidden/x', 'target': 'Example'},
                      {'path': self.framework + '/x', 'target': 'hidden/Example'}):
            self.map(self.document([entry]))
            with self.assertRaises(ValueError):
                links.restore_links(self.root)
        self.assert_pristine()

    def test_bundle_root_and_map_symlinks_are_rejected(self):
        alias = self.root.parent / 'alias'
        self.symlink(alias, self.root, directory=True)
        with self.assertRaises(ValueError):
            links.restore_links(alias)
        target = self.file('original.json', json.dumps(self.document()).encode())
        path = self.root / links.MAP_NAME
        self.symlink(path, 'original.json')
        with self.assertRaises(ValueError):
            links.restore_links(self.root)
        path.unlink()

    def test_hardlinked_map_is_rejected_without_writing(self):
        target = self.file('original.json', json.dumps(self.document()).encode())
        path = self.root / links.MAP_NAME
        os.link(target, path)
        with self.assertRaises(ValueError):
            links.restore_links(self.root)
        self.assert_pristine()

    def test_special_target_is_rejected_on_posix(self):
        if not hasattr(os, 'mkfifo'):
            self.skipTest('POSIX special-file test')
        os.mkfifo(self.root / self.framework / 'pipe')
        self.map(self.document([{'path': self.framework + '/x', 'target': 'pipe'}]))
        with self.assertRaises(ValueError):
            links.restore_links(self.root)


if __name__ == '__main__':
    unittest.main()
