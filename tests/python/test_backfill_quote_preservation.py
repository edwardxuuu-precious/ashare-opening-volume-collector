import _bootstrap
import json
import tempfile
import unittest
from pathlib import Path

from scripts import backfill_quotes


def core_row(code='000001'):
    return dict(code=code, name='保存测试', market='SZ', status='ok',
                first15Volume=10, dailyVolume=100, ratio=10,
                verificationVersion=3, pctChange=1.0, amplitude=2.0)


def quote_frame():
    return dict(open=9.8, high=10.3, low=9.7, close=10.0,
                priceStatus='available', priceSourceProvider='tencent',
                dailyAdapter='tencent_daily_quote_backfill',
                pctChange=1.1, amplitude=6.0)


class BackfillQuotePreservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)

    def write_day(self, day, rows):
        path = self.out / (day + '.json')
        path.write_text(json.dumps(dict(date=day, generatedAt='saved',
                                       rows=rows, methodology='fixture')))
        return path

    def test_sparse_cache_fills_missing_stock_without_changing_saved_quote(self):
        day = '2026-09-18'
        saved = dict(core_row(), **quote_frame(), verificationVersion=2,
                     quoteTime='2026-09-18T15:00:00+08:00')
        missing = dict(core_row('000002'), open=None, high=None, low=None,
                       close=None, priceStatus='missing')
        path = self.write_day(day, [saved, missing])
        sparse_cache = {'000002': {'days': {day: quote_frame()}}}

        summary = backfill_quotes.patch_files(self.out, sparse_cache, 'filled', [day])

        result = json.loads(path.read_text())
        self.assertEqual(result['rows'][0], saved)
        self.assertEqual(result['rows'][1], dict(missing, **quote_frame()))
        self.assertEqual(summary['filesChanged'], 1)
        self.assertEqual((result['priceAvailable'], result['priceMissing']), (2, 0))

    def test_empty_selection_does_not_modify_any_saved_file(self):
        day = '2026-09-18'
        missing = dict(core_row(), open=None, high=None, low=None, close=None,
                       priceStatus='missing', verificationVersion=2)
        cache = {'000001': {'days': {day: quote_frame()}}}
        for selected_dates in ([], None):
            with self.subTest(selected_dates=selected_dates):
                path = self.write_day(day, [missing])
                original = path.read_bytes()

                result = backfill_quotes.patch_files(
                    self.out, cache, 'unexpected', selected_dates)

                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(result, dict(filledRows=0, unfilledRows=0,
                                              filesChanged=0))

    def test_selected_batch_keeps_other_days_and_core_observations(self):
        day, other_day = '2026-09-18', '2026-09-17'
        missing = dict(core_row(), status='missing', first15Volume=None,
                       dailyVolume=None, ratio=None, reason='核心量待补',
                       open=None, high=None, low=None, close=None,
                       priceStatus='missing')
        path = self.write_day(day, [missing])
        other_path = self.write_day(other_day, [core_row('000002')])
        other_original = other_path.read_bytes()
        cache = {'000001': {'days': {day: quote_frame()}},
                 '000002': {'days': {other_day: quote_frame()}}}

        backfill_quotes.patch_files(self.out, cache, 'filled', [day])

        actual = json.loads(path.read_text())['rows'][0]
        self.assertEqual(actual, dict(missing, **quote_frame()))
        self.assertEqual(other_path.read_bytes(), other_original)

    def test_valid_saved_no_trade_frame_is_preserved_without_quote_cache(self):
        day = '2026-09-18'
        saved = dict(core_row(), status='suspended', first15Volume=None,
                     dailyVolume=None, ratio=None, pctChange=None, amplitude=None,
                     open=None, high=None, low=None, close=None,
                     priceStatus='not_traded', verificationVersion=2,
                     noTradeEvidence={'kind': 'company_notice', 'date': day})
        missing = dict(core_row('000002'), open=None, high=None, low=None,
                       close=None, priceStatus='missing')
        path = self.write_day(day, [saved, missing])

        backfill_quotes.patch_files(self.out,
            {'000002': {'days': {day: quote_frame()}}}, 'filled', [day])

        self.assertEqual(json.loads(path.read_text())['rows'][0], saved)

    def test_available_label_does_not_preserve_an_invalid_saved_frame(self):
        day = '2026-09-18'
        invalid = dict(core_row(), **dict(quote_frame(), high=9.0))
        path = self.write_day(day, [invalid])

        backfill_quotes.patch_files(self.out, {}, 'normalized', [day])

        actual = json.loads(path.read_text())['rows'][0]
        self.assertEqual({key: actual[key] for key in
                          ('open', 'high', 'low', 'close', 'priceStatus')},
                         dict(open=None, high=None, low=None, close=None,
                              priceStatus='missing'))
        self.assertEqual((actual['first15Volume'], actual['dailyVolume'],
                          actual['ratio'], actual['status']), (10, 100, 10, 'ok'))


if __name__ == '__main__':
    unittest.main()
