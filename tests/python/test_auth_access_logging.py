import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('access_logging', ROOT/'deploy/auth/access_logging.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AccessLoggingTests(unittest.TestCase):
    def before(self):
        template = json.loads((ROOT/'deploy/auth/template.json').read_text())
        template['Resources'].pop('AccessLogs', None)
        template['Resources']['Stage']['Properties'].pop('AccessLogSettings', None)
        template['Resources']['Function']['Properties']['Code'] = {'ZipFile': 'existing production code'}
        return template

    def test_changes_only_log_resource_and_stage(self):
        before = self.before()
        original = json.loads(json.dumps(before))
        after = module.add_access_logs(before)
        self.assertEqual(before, original)
        for name, resource in before['Resources'].items():
            if name != 'Stage':
                self.assertEqual(after['Resources'][name], resource)
        stage = after['Resources']['Stage']['Properties']
        self.assertEqual(stage['DefaultRouteSettings'], before['Resources']['Stage']['Properties']['DefaultRouteSettings'])
        self.assertEqual(after['Resources']['AccessLogs']['Properties']['RetentionInDays'], 14)
        module.assert_log_only_change(before, after)

    def test_no_credentials_user_identity_or_request_body_in_logs(self):
        allowed = {'requestId', 'timeEpoch', 'method', 'path', 'status', 'responseLatencyMs',
                   'integrationStatus', 'integrationResponseStatus', 'integrationRequestId',
                   'integrationError', 'errorType'}
        self.assertEqual(set(module.LOG_FIELDS), allowed)
        values = ' '.join(module.LOG_FIELDS.values()).lower()
        for forbidden in ['cookie', 'authorization', 'body', 'username', 'sourceip', 'claims', 'useragent']:
            self.assertNotIn(forbidden, values)

    def test_refuses_changed_stage_existing_logs_and_unrelated_writes(self):
        before = self.before()
        before['Resources']['Stage']['Properties']['StageName'] = 'other'
        with self.assertRaises(ValueError): module.add_access_logs(before)
        before = self.before()
        before['Resources']['Stage']['Properties']['AccessLogSettings'] = {'Format': 'old'}
        with self.assertRaises(ValueError): module.add_access_logs(before)
        before = self.before()
        after = module.add_access_logs(before)
        after['Resources']['Function']['Properties']['Timeout'] = 99
        with self.assertRaises(ValueError): module.assert_log_only_change(before, after)

    def test_patch_is_idempotent(self):
        after = module.add_access_logs(self.before())
        self.assertEqual(module.add_access_logs(after), after)
