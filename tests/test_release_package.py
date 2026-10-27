import hashlib
import importlib
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests.test_workbench import _TEMP


class ReleasePackageTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('scripts.build_release'), 'source release builder is missing')
        self.builder = importlib.import_module('scripts.build_release')
        self.temporary = tempfile.TemporaryDirectory(dir=_TEMP)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)/'source'
        self.output = Path(self.temporary.name)/'output'
        for name in ('.gitignore', 'README.md', 'LICENSE', 'app.py', 'config.py', 'start.ps1', 'start.bat',
                     'workbench.example.json', 'docs/RELEASE.md', 'docs/examples/models.json',
                     'scripts/build_release.py', 'scripts/install-opencode.ps1'):
            self.write(name, '{}' if name.endswith('.json') else '# Source release fixture\n')
        self.write('workbench/static/chat.html', '<html>fixture</html>')
        self.write('tests/fixtures/td/sample.log', 'RESOURCE fixture\n')

    def write(self, name, content):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')

    def test_source_allowlist_excludes_local_data_and_embedded_tools(self):
        secret = 'sk-' + 'A'*32
        for path in ('work/private/secret.json', '.tools/vendor/installer.exe', 'workbench.local.json',
                     '.env', 'docs/validation/private.md', 'docs/superpowers/plans/private.md',
                     'workbench/__pycache__/cached.pyc', 'tools/node_modules/sdk/key.json',
                     'tests/private.lic', 'exp_calculator/wave.vcd'):
            self.write(path, secret)
        sources = self.builder.collect_sources(self.root)
        self.assertIn('README.md', sources)
        self.assertIn('workbench/static/chat.html', sources)
        self.assertIn('tests/fixtures/td/sample.log', sources)
        self.assertFalse(any(secret.encode() in value for value in sources.values()))
        self.assertNotIn('workbench.local.json', sources)

    def test_credentials_in_included_source_stop_packaging_without_echoing_them(self):
        secret = 'sk-' + 'B'*32
        self.write('workbench/accidental_key.py', 'KEY = "'+secret+'"\n')
        with self.assertRaises(ValueError) as caught:
            self.builder.build_release(self.root, self.output, '0.1.0')
        self.assertIn('accidental_key.py', str(caught.exception))
        self.assertNotIn(secret, str(caught.exception))
        self.assertFalse(self.output.exists())

    def test_archive_manifest_and_checksum_match_and_existing_output_is_preserved(self):
        result = self.builder.build_release(self.root, self.output, '0.1.0')
        archive = Path(result['archive'])
        original = archive.read_bytes()
        with zipfile.ZipFile(archive) as package:
            self.assertIsNone(package.testzip())
            prefix = 'rtl-lab-v0.1.0/'
            self.assertTrue(all(name.startswith(prefix) and '..' not in Path(name).parts for name in package.namelist()))
            manifest = json.loads(package.read(prefix+'RELEASE_MANIFEST.json'))
            for name, digest in manifest['files'].items():
                self.assertEqual(hashlib.sha256(package.read(prefix+name)).hexdigest(), digest)
            self.assertIn(prefix+'README.md', package.namelist())
        self.assertEqual(result['sha256'], hashlib.sha256(original).hexdigest())
        with self.assertRaises(FileExistsError):
            self.builder.build_release(self.root, self.output, '0.1.0')
        self.assertEqual(archive.read_bytes(), original)

    def test_version_cannot_escape_the_output_directory(self):
        with self.assertRaises(ValueError):
            self.builder.build_release(self.root, self.output, '../escape')


if __name__ == '__main__':
    unittest.main()
