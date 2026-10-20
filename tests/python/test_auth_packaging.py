import _bootstrap
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).parents[2]


class PackagingTests(unittest.TestCase):
    def test_production_build_rejects_missing_or_external_auth(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith('NEXT_PUBLIC_')}
        script = str(ROOT / 'deploy/auth/prepare_build.py')
        result = subprocess.run([sys.executable, script], cwd=ROOT, env=env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        env.update(NEXT_PUBLIC_AUTH_API='https://api.stock.iteready.com',
                   NEXT_PUBLIC_DATA_BASE='https://api.stock.iteready.com')
        self.assertEqual(subprocess.run([sys.executable, script], cwd=ROOT, env=env, capture_output=True).returncode, 0)
        env['NEXT_PUBLIC_COLLECTOR_API'] = 'https://unprotected.example'
        self.assertNotEqual(subprocess.run([sys.executable, script], cwd=ROOT, env=env, capture_output=True).returncode, 0)

    def test_packaging_removes_data_without_deleting_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'dist/client/data').mkdir(parents=True)
            (root / 'public/data').mkdir(parents=True)
            (root / 'dist/client/index.html').write_text('login')
            (root / 'dist/client/data/manifest.json').write_text('{}')
            source = root / 'public/data/manifest.json'
            source.write_text('{}')
            result = subprocess.run([sys.executable, str(ROOT / 'deploy/auth/strip_public_data.py')],
                                    cwd=root, capture_output=True)
            self.assertEqual(result.returncode, 0)
            self.assertFalse((root / 'dist/client/data').exists())
            self.assertTrue(source.exists())
            self.assertEqual((root / 'dist/client/index.html').read_text(), 'login')


if __name__ == '__main__':
    unittest.main()
