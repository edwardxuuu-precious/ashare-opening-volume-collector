"""Credential migration must not discard secrets or claim failed resets succeeded."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'deploy/auth'))
import credential_store as store


class CredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'accounts.json'
        self.mock = patch.object(store, 'store_path', return_value=self.path)
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def test_migration_preserves_credentials_and_permissions(self):
        legacy = self.path.parent / 'accounts'
        legacy.mkdir()
        expected = {'username': 'demo', 'password': 'test-only'}
        source = legacy / 'demo.json'
        source.write_text(json.dumps(expected))
        self.assertEqual(store.load_credentials('demo'), expected)
        self.assertFalse(source.exists())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(store.load_credentials('demo'), expected)

    def test_conflicting_migration_keeps_both_originals(self):
        self.path.write_text(json.dumps({'accounts': {'demo': {'username': 'demo', 'password': 'old'}}}))
        original = self.path.read_bytes()
        legacy = self.path.parent / 'accounts'
        legacy.mkdir()
        source = legacy / 'demo.json'
        source.write_text(json.dumps({'username': 'demo', 'password': 'different'}))
        with self.assertRaises(ValueError):
            store.load_credentials('demo')
        self.assertEqual(self.path.read_bytes(), original)
        self.assertTrue(source.exists())

    def test_failed_remote_reset_preserves_current_and_pending_password(self):
        store.set_password('demo', 'current', lambda: None)
        def fail():
            raise RuntimeError('simulated remote failure')
        with self.assertRaises(RuntimeError):
            store.set_password('demo', 'candidate', fail)
        data = json.loads(self.path.read_text())
        self.assertEqual(data['accounts']['demo']['password'], 'current')
        self.assertEqual(data['pending_password_changes']['demo'], 'candidate')
        store.set_password('demo', 'confirmed', lambda: None)
        data = json.loads(self.path.read_text())
        self.assertEqual(data['accounts']['demo']['password'], 'confirmed')
        self.assertEqual(data['pending_password_changes'], {})


if __name__ == '__main__':
    unittest.main()
