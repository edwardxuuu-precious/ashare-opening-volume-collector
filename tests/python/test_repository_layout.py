"""Verify that directory moves preserve runtime packaging and probe boundaries."""
import _bootstrap
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from deploy.collector.runtime_bundle import archive_name, runtime_files

ROOT = _bootstrap.ROOT


class RepositoryLayoutTests(unittest.TestCase):
    def test_runtime_bundle_preserves_cloud_entrypoints(self):
        files = runtime_files(ROOT)
        names = {archive_name(path, ROOT) for path in files}
        self.assertTrue({'scripts/collect.py', 'scripts/daily_collector.py',
                         'server/cloud_worker.py', 'server/publisher_state.py',
                         'deploy/collector/install-service.sh'} <= names)
        self.assertFalse(any(name.startswith('backend/') for name in names))
        self.assertEqual(len(names), len(files))
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            archive = directory / 'runtime.tar'
            with tarfile.open(archive, 'w') as bundle:
                for path in files:
                    bundle.add(path, arcname=archive_name(path, ROOT))
            extracted = directory / 'runtime'
            with tarfile.open(archive) as bundle:
                # This archive contains only the explicit local files just written above.
                bundle.extractall(extracted)
            for entry in ('server/cloud_worker.py', 'scripts/daily_collector.py'):
                with self.subTest(entry=entry):
                    result = subprocess.run([sys.executable, str(extracted / entry), '--help'],
                                            cwd=extracted, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_probe_rejects_relocated_protected_data(self):
        for relative in ('var/data', 'public/data', 'data'):
            with self.subTest(relative=relative):
                result = subprocess.run([sys.executable, str(ROOT / 'backend/scripts/baostock_source.py'),
                                         '--dates', '2026-09-04', '--out', str(ROOT / relative)],
                                        cwd=ROOT, capture_output=True, text=True, timeout=30)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('probe output must be a separate scratch directory', result.stderr)


if __name__ == '__main__':
    unittest.main()
