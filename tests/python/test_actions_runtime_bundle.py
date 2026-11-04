import _bootstrap
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from deploy.actions import package


ROOT = Path(__file__).resolve().parents[2]
OFFLINE = '''
import socket
def offline(*args, **kwargs):
    raise AssertionError('Runtime bundle smoke must not contact a provider or AWS')
socket.create_connection = socket.getaddrinfo = offline
socket.socket.connect = socket.socket.connect_ex = offline
'''


class RuntimeBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        cls.bundle = (Path(cls.folder.name) / 'bundle').resolve()
        cls.manifest = package.build(cls.bundle)

    def run_exported(self, arguments):
        result = subprocess.run(arguments, cwd=self.bundle, capture_output=True, text=True, timeout=45,
                                env={**os.environ, 'PYTHONPATH': str(self.bundle), 'PYTHONNOUSERSITE': '1',
                                     'PYTHONDONTWRITEBYTECODE': '1', 'AWS_EC2_METADATA_DISABLED': 'true'})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def source_copy(self, folder):
        source = Path(folder) / 'source'
        shutil.copytree(self.bundle, source)
        return source

    def test_exported_sources_requirements_and_workflows_keep_exact_bytes_and_hashes(self):
        names = package.SOURCES + ['requirements.txt'] + ['.github/workflows/' + name for name in package.WORKFLOWS]
        for name in names:
            with self.subTest(source=name):
                raw = (self.bundle / name).read_bytes()
                self.assertEqual(raw, (ROOT / name).read_bytes())
                self.assertEqual(hashlib.sha256(raw).hexdigest(), self.manifest[name])

    def test_all_static_local_imports_are_in_the_export(self):
        search = (ROOT, ROOT / 'backend', ROOT / 'backend/scripts', ROOT / 'backend/server')
        for name in package.SOURCES:
            if not name.endswith('.py'):
                continue
            package_name = '.'.join(Path(name).parts[:-1])
            for node in ast.walk(ast.parse((ROOT / name).read_text())):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ''
                    if node.level:
                        module = importlib.util.resolve_name('.' * node.level + module, package_name)
                    modules = [module] + [module + '.' + alias.name for alias in node.names]
                for module in modules:
                    for directory in search:
                        for suffix in ('.py', '/__init__.py'):
                            source = directory / (module.replace('.', '/') + suffix)
                            if source.is_file():
                                relative = source.relative_to(ROOT).as_posix()
                                with self.subTest(source=name, dependency=relative):
                                    self.assertIn(relative, self.manifest)

    def test_all_python_modules_and_lazy_identity_dependencies_load_in_export(self):
        modules = [name[:-3].replace('/', '.') for name in package.SOURCES if name.endswith('.py')]
        output = self.run_exported([sys.executable, '-B', '-c', OFFLINE + '''
import importlib, json, sys
from pathlib import Path
root = Path.cwd()
sys.path[:0] = [str(root / name) for name in ('backend', 'backend/scripts', 'backend/server')]
for name in json.loads(sys.argv[1]):
    module = importlib.import_module(name)
    assert Path(module.__file__).resolve().is_relative_to(root), name
from backend.chips.runner import implementation_identity
from backend.chips.concentration_two_runner import source_closure
from backend.golden.daily import implementation
from backend.trend_volume.daily import implementation_identity as trend_identity
from backend.golden_projection.daily import implementation_identity as projection_identity
assert implementation_identity()['files']
assert source_closure()
assert len(implementation()) == 64
assert trend_identity()['files']
assert projection_identity()['files']['policy.json']
print(json.dumps({'imported': len(json.loads(sys.argv[1])), 'identity_closures': 5}))
''', json.dumps(modules)])
        self.assertEqual(json.loads(output), {'imported': len(modules), 'identity_closures': 5})

    def test_workflow_python_entrypoints_have_offline_help_or_import_smoke(self):
        commands = (
            ('source-probe', 'path', 'backend/scripts/actions_source_probe.py'),
            ('collector', 'path', 'backend/server/actions_runner.py'),
            ('chips guard', 'path', 'tooling/chip_followup_guard.py'),
            ('chips budget', 'path', 'tooling/chip_workflow_budget.py'),
            ('chips bounded runner', 'path', 'tooling/run_chip_bounded.py'),
            ('chips concentration', 'module', 'backend.chips.concentration_two_runner'),
            ('golden', 'module', 'backend.golden.update'),
            ('golden-projection', 'module', 'backend.golden_projection.daily'),
            ('trend-volume', 'module', 'backend.trend_volume.daily'),
            ('triangles', 'module', 'backend.triangles.daily'),
        )
        script = OFFLINE + '''
import runpy, sys
kind, target = sys.argv[1:]
sys.argv = [target, '--help']
if kind == 'path':
    runpy.run_path(target, run_name='__main__')
else:
    runpy.run_module(target, run_name='__main__')
'''
        for workflow, kind, target in commands:
            with self.subTest(workflow=workflow):
                self.assertIn('usage:', self.run_exported([sys.executable, '-B', '-c', script, kind, target]))
        self.run_exported([sys.executable, '-B', '-c', OFFLINE + 'import backend.chips.probe'])

    def test_node_esm_imports_and_identity_resolve_inside_export(self):
        self.assertEqual(json.loads((self.bundle / 'package.json').read_text())['type'], 'module')
        for name in package.SOURCES:
            if not name.endswith(('.mjs', '.ts')):
                continue
            for dependency in re.findall(r"from\s+['\"](\.[^'\"]+)['\"]", (self.bundle / name).read_text()):
                source = (self.bundle / name).parent.joinpath(dependency).resolve()
                self.assertTrue(source.is_file(), str(source))
                self.assertIn(source.relative_to(self.bundle).as_posix(), self.manifest)
        result = json.loads(self.run_exported(['node', '--experimental-strip-types',
                                               'tooling/build-triangle-daily.mjs', '--identity']))
        self.assertEqual(result['rule_sha256'], self.manifest['src/lib/triangles.ts'])
        for name, digest in result['files'].items():
            self.assertEqual(digest, self.manifest[name])

    def test_node_builder_produces_readable_bundles_from_fictional_prices(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'fictional-input'
            source.mkdir()
            day = '2026-11-03'
            generated = day + 'T17:00:00+08:00'
            values = {
                'manifest.json': {'tradingDates': [day], 'dates': [
                    {'date': day, 'file': day + '.json', 'generatedAt': generated}]},
                'stocks.json': {'rows': [{'code': '000001', 'name': 'Fictional stock', 'market': 'SZ'}]},
                day + '.json': {'date': day, 'generatedAt': generated, 'scope': 'full',
                               'priceDataComplete': True, 'pendingCount': 0, 'rows': [
                                   {'code': '000001', 'name': 'Fictional stock', 'priceStatus': 'available',
                                    'open': 10, 'high': 11, 'low': 9, 'close': 10}]},
            }
            for name, value in values.items():
                (source / name).write_text(json.dumps(value))
            out = Path(folder) / 'publication'
            result = json.loads(self.run_exported(['node', '--experimental-strip-types',
                'tooling/build-triangle-daily.mjs', '--input', str(source), '--out', str(out),
                '--dataset-id', 'abcdef0123456789', '--as-of', day]))
            self.assertEqual(result['universe_total'], 1)
            self.assertEqual(result['bundles'], 1)
            publication = json.loads((out / 'manifest.json').read_bytes())
            for entry in [publication['screening'], *publication['bundles']]:
                raw = (out / entry['file']).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), entry['sha256'])
                self.assertEqual(len(raw), entry['bytes'])
            screen = json.loads((out / publication['screening']['file']).read_bytes())
            self.assertEqual(screen['rows'][0]['security']['code'], '000001')
            self.assertEqual(screen['rows'][0]['latestClose'], 10)

    def test_probe_only_export_remains_a_standalone_source_probe(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'probe'
            manifest = package.build(out, probe_only=True)
            expected = {name for name in package.SOURCES if name.startswith('backend/scripts/')}
            self.assertEqual(set(manifest), expected | {
                '.github/workflows/source-probe.yml', 'requirements.txt', '.gitignore', 'README.md'})
            result = subprocess.run([sys.executable, '-B', 'backend/scripts/actions_source_probe.py', '--help'],
                                    cwd=out, capture_output=True, text=True, timeout=30,
                                    env={**os.environ, 'PYTHONPATH': str(out), 'PYTHONNOUSERSITE': '1'})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('usage:', result.stdout)

    def test_missing_module_or_policy_refuses_export_before_any_write(self):
        for name in ('backend/chips/history.py', 'backend/golden_projection/policy.json'):
            with self.subTest(source=name), tempfile.TemporaryDirectory() as folder:
                source = self.source_copy(folder)
                (source / name).unlink()
                out = Path(folder) / 'out'
                with patch.object(package, 'ROOT', source), self.assertRaisesRegex(ValueError, name):
                    package.build(out)
                self.assertFalse(out.exists())

    def test_requirements_and_policy_receive_the_same_secret_scan_as_code(self):
        for name in ('requirements.txt', 'backend/golden_projection/policy.json', 'backend/chips/model.py'):
            with self.subTest(source=name), tempfile.TemporaryDirectory() as folder:
                source = self.source_copy(folder)
                (source / name).write_bytes(b'AKIA' + b'A' * 16)
                out = Path(folder) / 'out'
                with patch.object(package, 'ROOT', source), self.assertRaisesRegex(ValueError, 'Credential-like'):
                    package.build(out)
                self.assertFalse(out.exists())

    def test_symlinked_sources_and_parent_directories_refuse_export(self):
        for name in ('requirements.txt', 'backend/golden_projection/policy.json',
                     'backend/chips', '.github/workflows'):
            with self.subTest(source=name), tempfile.TemporaryDirectory() as folder:
                source = self.source_copy(folder)
                original = source / name
                target = Path(folder) / 'redirected-source'
                original.rename(target)
                original.symlink_to(target, target_is_directory=target.is_dir())
                out = Path(folder) / 'out'
                with patch.object(package, 'ROOT', source), self.assertRaisesRegex(ValueError, 'symlinked source'):
                    package.build(out)
                self.assertFalse(out.exists())

    def test_nonempty_destination_and_source_directories_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self.source_copy(folder)
            out = Path(folder) / 'out'
            out.mkdir()
            marker = out / 'keep.txt'
            marker.write_text('retain this file')
            with self.assertRaisesRegex(ValueError, 'empty destination'):
                package.build(out)
            self.assertEqual(marker.read_text(), 'retain this file')
            protected = source / 'backend/new-export'
            protected.mkdir()
            with patch.object(package, 'ROOT', source):
                for destination in (source, source.parent, protected):
                    with self.subTest(destination=destination), self.assertRaisesRegex(ValueError, 'overlaps runtime sources'):
                        package.build(destination)
            self.assertEqual(list(protected.iterdir()), [])

    def test_symlinked_destination_or_parent_refuses_all_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'target'
            target.mkdir()
            link = Path(folder) / 'link'
            link.symlink_to(target, target_is_directory=True)
            for destination in (link, link / 'bundle'):
                with self.subTest(destination=destination), self.assertRaisesRegex(ValueError, 'Symlinked destination'):
                    package.build(destination)
            self.assertEqual(list(target.iterdir()), [])

    def test_empty_outputs_and_var_work_directories_are_valid_staging_destinations(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self.source_copy(folder)
            with patch.object(package, 'ROOT', source):
                for name in ('outputs/bundle', 'var/work/bundle'):
                    out = source / name
                    out.mkdir(parents=True)
                    self.assertEqual(package.build(out), self.manifest)


if __name__ == '__main__':
    unittest.main()
