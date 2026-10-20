import _bootstrap
"""The diagnostic never retries access denial or treats a partial pair as verified."""
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend/scripts'))
from probe_eastmoney_endpoint import probe


class Response(io.BytesIO):
    status = 200

    def __init__(self, rows):
        super().__init__(json.dumps({'data': {'klines': rows}}).encode())


class EndpointProbeTest(unittest.TestCase):
    def test_access_denial_stops_without_second_request(self):
        with patch('probe_eastmoney_endpoint.urllib.request.urlopen',
                   side_effect=HTTPError('https://example.invalid', 429, 'limited', {}, None)) as fetch:
            report = probe('600519', '2026-09-04')
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(report['stopReason'], 'access_denied_or_rate_limited')
        self.assertNotIn('comparison', report)

    def test_empty_pair_is_not_a_successful_comparison(self):
        with patch('probe_eastmoney_endpoint.urllib.request.urlopen', side_effect=[Response([]), Response([])]), \
             patch('probe_eastmoney_endpoint.time.sleep'):
            report = probe('600519', '2026-09-04')
        self.assertNotIn('comparison', report)
        self.assertTrue(all(item['status'] == 'empty' for item in report['observations']))

    def test_comparison_preserves_fractional_lots_for_diagnostics(self):
        with patch('probe_eastmoney_endpoint.urllib.request.urlopen', side_effect=[
                Response(['2026-09-04 09:45,10,10,10,10,2.01', '2026-09-04 15:00,10,10,10,10,3']),
                Response(['2026-09-04,10,10,10,10,5.02'])]), \
             patch('probe_eastmoney_endpoint.time.sleep'):
            report = probe('600519', '2026-09-04')
        self.assertEqual(report['comparison']['differenceShares'], '-1.00')
        self.assertEqual(report['comparison']['first15VolumeShares'], '201.00')
        self.assertNotIn('verified', report['comparison'])


if __name__ == '__main__':
    unittest.main()
