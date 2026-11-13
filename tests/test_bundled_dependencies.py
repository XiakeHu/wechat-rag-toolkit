# SPDX-License-Identifier: Apache-2.0
"""Offline checks for bundled source selection and local command entry point."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from bootstrap_vendor import activate

ROOT = Path(__file__).resolve().parents[1]


class BundledTests(unittest.TestCase):
    def test_reader_and_helper_resolve_to_bundle(self):
        activate()
        import wechat_cli, wechatauto
        for module, folder in [(wechat_cli, 'wechat-cli'), (wechatauto, 'wechatauto-replica')]:
            self.assertTrue(Path(module.__file__).resolve().is_relative_to(ROOT/'vendor'/folder))

    def test_upstream_copied_files_match_recorded_revision_bytes(self):
        for dependency in json.loads((ROOT/'vendor/SOURCES.json').read_text(encoding='utf-8')):
            for name, expected in dependency['bundled_files'].items():
                self.assertEqual(hashlib.sha256((ROOT/'vendor'/dependency['project']/name).read_bytes()).hexdigest(), expected, name)

    def test_cli_help_without_installing_external_project(self):
        result = subprocess.run([sys.executable, str(ROOT/'tools/wechat_reader.py'), '--help'], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'contacts', result.stdout)

    def test_accessibility_hash_guard_remains_active(self):
        from replica_integrity import verify_replica_sources
        verify_replica_sources()


if __name__ == '__main__':
    unittest.main()
