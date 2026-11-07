from pathlib import Path
import tempfile
import unittest
from multishutter.web import published_source


class PublishedFileTests(unittest.TestCase):
    def test_moved_cli_session_resolves_only_its_published_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / ('a' * 32) / 'manifest.json'
            photo = manifest.parent / 'takes/001/sony/photo.JPG'
            photo.parent.mkdir(parents=True)
            photo.write_bytes(b'photo')
            legacy = '/mnt/old/project/captures/' + photo.relative_to(root).as_posix()
            self.assertEqual(published_source(root, manifest, '001', 'sony', {'path': legacy}), photo.resolve())
            self.assertIsNone(published_source(root, manifest, '002', 'sony', {'path': legacy}))
            self.assertIsNone(published_source(root, manifest, '001', '..', {'path': legacy}))
