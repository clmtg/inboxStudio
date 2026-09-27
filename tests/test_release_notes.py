import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/release_notes.py'
spec = importlib.util.spec_from_file_location('release_notes', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReleaseNotesTests(unittest.TestCase):
    def test_only_matching_version_preserves_markdown(self):
        source = '# Changelog\n\n## Unreleased\n\n- Future\n\n## 0.2.2 - 2026-09-27\n\n### Added\n\n- **Any** conditions\n\nMigration details.\n\n## 0.2.1\n\n- Older\n'
        self.assertEqual(module.extract_notes(source, '0.2.2'),
                         '### Added\n\n- **Any** conditions\n\nMigration details.\n')

    def test_last_section_and_exact_version(self):
        self.assertEqual(module.extract_notes('## 0.2.20\nWrong\n## 0.2.2\nCorrect\n', '0.2.2'), 'Correct\n')

    def test_missing_empty_duplicate_and_invalid_version(self):
        for source, version in [('## Unreleased\nFuture', '0.2.2'),
                                ('## 0.2.20\nWrong', '0.2.2'),
                                ('## 0.2.2\n\n## 0.2.1\nOlder', '0.2.2'),
                                ('## 0.2.2\nOne\n## 0.2.2\nTwo', '0.2.2'),
                                ('## 0.2.2\nNotes', 'v0.2.2')]:
            with self.subTest(source=source, version=version), self.assertRaises(ValueError):
                module.extract_notes(source, version)

    def test_cli_writes_notes_and_missing_section_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / 'CHANGELOG.md', Path(folder) / 'notes.md'
            source.write_text('## 0.2.2\n\n- Café\n', encoding='utf-8')
            args = [sys.executable, str(SCRIPT), '0.2.2', '--changelog', str(source), '--output', str(output)]
            result = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text(encoding='utf-8'), '- Café\n')
            output.unlink()
            source.write_text('## Unreleased\nFuture')
            result = subprocess.run(args, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Release notes error', result.stderr)
            self.assertFalse(output.exists())
