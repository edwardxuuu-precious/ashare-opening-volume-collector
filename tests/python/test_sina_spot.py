import _bootstrap
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from scripts import refresh, sina_spot


DAY = '2026-09-08'
MOMENT = datetime(2026, 9, 8, 15, 35, tzinfo=ZoneInfo('Asia/Shanghai'))


class SinaSpotSnapshotTests(unittest.TestCase):
    def test_missing_symbol_uses_same_day_sina_close_quote(self):
        bulk = pd.DataFrame([
            {'代码': f'sh600{i:03d}', '最新价': 10.0, '昨收': 10.0, '今开': 10.0,
             '最高': 10.1, '最低': 9.9, '成交量': 1000.0, '时间戳': '15:36:00'}
            for i in range(99)
        ])
        fields = ['九号公司-WD', '37.000', '37.100', '36.560', '37.200', '36.010',
                  '36.550', '36.560', '6981392', '255525194.000'] + ['0'] * 20 + [
                  DAY, '15:34:59', '00', '']
        response = Mock(text='var hq_str_sh689009="' + ','.join(fields) + '";')
        provider = SimpleNamespace(stock_zh_a_spot=lambda: bulk,
            stock_zh_a_hist_tx=Mock(side_effect=AssertionError('Unavailable target-day history')))
        universe = [{'code': f'600{i:03d}'} for i in range(99)] + [{'code': '689009'}]
        with patch('requests.get', return_value=response) as get:
            result = sina_spot.load_snapshot(provider, universe, DAY, moment=MOMENT)

        self.assertEqual(result['availableCount'], 100)
        row = result['rows']['689009']
        self.assertEqual(row['dailyVolume'], 6981392)
        self.assertEqual(row['close'], 36.56)
        self.assertEqual(row['priceStatus'], 'available')
        self.assertEqual(row['sourceProvider'], 'sina')
        self.assertEqual(row['priceSourceProvider'], 'sina')
        self.assertEqual(row['dailyAdapter'], 'sina_symbol_close_quote')
        get.assert_called_once()
        provider.stock_zh_a_hist_tx.assert_not_called()
        get.assert_called_with('https://hq.sinajs.cn/list=sh689009',
            headers={'Referer': 'https://finance.sina.com.cn/'}, timeout=20)
        with patch.object(refresh, 'AK', provider):
            fetched = refresh.fetch((dict(code='689009', name='九号公司'), DAY,
                984224, dict(status='missing'), 'close', row))
        self.assertEqual(fetched['row']['status'], 'ok')
        self.assertEqual(fetched['row']['first15Volume'], 984224)
        self.assertEqual(fetched['row']['dailyVolume'], 6981392)
        self.assertEqual(fetched['row']['ratio'], round(984224 / 6981392 * 100, 6))
        self.assertEqual(fetched['row']['priceStatus'], 'available')

    def test_symbol_close_quote_rejects_wrong_identity_date_time_volume_or_price(self):
        fields = ['九号公司-WD', '37.000', '37.100', '36.560', '37.200', '36.010',
                  '36.550', '36.560', '6981392', '255525194.000'] + ['0'] * 20 + [
                  DAY, '15:34:59', '00', '']
        invalid = [(None, None, 'sh600519'), (30, '2026-09-07', 'sh689009'),
                   (31, '09:45:00', 'sh689009'), (31, '15:20:00', 'sh689009'),
                   (31, '25:00:00', 'sh689009'),
                   (8, '-1', 'sh689009'), (8, '0', 'sh689009'),
                   (8, '12.5', 'sh689009'), (8, 'NaN', 'sh689009'),
                   (1, '38.0', 'sh689009'), (2, '0', 'sh689009'),
                   (5, '', 'sh689009')]
        for index, value, symbol in invalid:
            with self.subTest(index=index, value=value, symbol=symbol):
                changed = fields.copy()
                if index is not None:
                    changed[index] = value
                response = Mock(text='var hq_str_' + symbol + '="' + ','.join(changed) + '";')
                with patch('requests.get', return_value=response):
                    self.assertIsNone(sina_spot._sina_close_quote('689009', DAY))

    def test_symbol_close_quote_rejects_empty_or_unrecognizable_response(self):
        for text in ['', 'var hq_str_sh689009="";', '<html>unavailable</html>']:
            with self.subTest(text=text), patch('requests.get', return_value=Mock(text=text)):
                self.assertIsNone(sina_spot._sina_close_quote('689009', DAY))

    def test_missing_symbols_keep_the_existing_bounded_fallback_limit(self):
        bulk = pd.DataFrame([
            {'代码': f'sh600{i:03d}', '最新价': 10.0, '昨收': 10.0, '今开': 10.0,
             '最高': 10.1, '最低': 9.9, '成交量': 1000.0, '时间戳': '15:36:00'}
            for i in range(980)
        ])
        provider = SimpleNamespace(stock_zh_a_spot=lambda: bulk,
            stock_zh_a_hist_tx=Mock(side_effect=ValueError('No exact-day history')))
        universe = [{'code': f'600{i:03d}'} for i in range(1000)]
        with patch('requests.get', return_value=Mock(text='')) as get:
            result = sina_spot.load_snapshot(provider, universe, DAY, moment=MOMENT)
        self.assertEqual(get.call_count, 10)
        self.assertEqual(provider.stock_zh_a_hist_tx.call_count, 10)
        self.assertEqual(result['availableCount'], 980)
        self.assertEqual(len(result['missingCodes']), 20)

    def test_zero_volume_snapshot_does_not_fabricate_a_total_loss(self):
        row = {'最新价': 0.0, '昨收': 10.0, '今开': 0.0, '最高': 0.0, '最低': 0.0, '成交量': 0.0}

        self.assertEqual(sina_spot._metrics(row), (None, None))

    def test_missing_snapshot_code_uses_daily_ohlc_quote_fallback(self):
        rows = [
            {'代码': f'sh600{i:03d}', '最新价': 10.0, '昨收': 10.0, '今开': 10.0,
             '最高': 10.1, '最低': 9.9, '成交量': 1000.0, '时间戳': '15:36:00'}
            for i in range(99)
        ]
        daily = pd.DataFrame([
            {'date': '2026-09-07', 'open': 39.00, 'close': 40.58, 'high': 40.76, 'low': 38.85},
            {'date': DAY, 'open': 40.10, 'close': 39.47, 'high': 40.84, 'low': 39.35},
        ])
        provider = SimpleNamespace(
            stock_zh_a_spot=lambda: pd.DataFrame(rows),
            stock_zh_a_hist_tx=lambda **kwargs: daily,
        )
        universe = [{'code': f'600{i:03d}'} for i in range(99)] + [{'code': '689009'}]

        with patch('requests.get', side_effect=requests.exceptions.Timeout):
            result = sina_spot.load_snapshot(provider, universe, DAY, moment=MOMENT)

        self.assertEqual(result['availableCount'], 100)
        self.assertEqual(result['missingCodes'], [])
        self.assertEqual(result['rows']['689009']['pctChange'], -2.7353)
        self.assertEqual(result['rows']['689009']['amplitude'], 3.6718)
        self.assertEqual(result['rows']['689009']['open'], 40.10)
        self.assertEqual(result['rows']['689009']['high'], 40.84)
        self.assertEqual(result['rows']['689009']['low'], 39.35)
        self.assertEqual(result['rows']['689009']['close'], 39.47)
        self.assertEqual(result['rows']['689009']['priceStatus'], 'available')
        self.assertEqual(result['rows']['689009']['priceSourceProvider'], 'tencent')
        self.assertNotIn('dailyVolume', result['rows']['689009'])

    def test_post_close_snapshot_provides_exact_share_volume_for_all_target_markets(self):
        frame = pd.DataFrame([
            {'代码': 'sz000001', '名称': '平安银行', '最新价': 11.78, '昨收': 11.70, '今开': 11.72,
             '最高': 11.81, '最低': 11.65, '成交量': 74051597.0, '时间戳': '15:36:00'},
            {'代码': 'sh600519', '名称': '贵州茅台', '最新价': 1309.30, '昨收': 1316.01, '今开': 1318.00,
             '最高': 1323.00, '最低': 1309.05, '成交量': 1753404.0, '时间戳': '15:34:59'},
            {'代码': 'bj920002', '名称': '万达轴承', '最新价': 53.00, '昨收': 53.84, '今开': 53.70,
             '最高': 54.38, '最低': 52.74, '成交量': 729266.0, '时间戳': '15:30:02'},
            {'代码': 'sz000002', '名称': '额外代码', '最新价': 1.0, '昨收': 1.0, '今开': 1.0,
             '最高': 1.0, '最低': 1.0, '成交量': 100.0, '时间戳': '15:30:01'},
        ])
        provider = SimpleNamespace(stock_zh_a_spot=lambda: frame)
        universe = [
            {'code': '000001', 'name': '平安银行'},
            {'code': '600519', 'name': '贵州茅台'},
            {'code': '920002', 'name': '万达轴承'},
        ]

        result = sina_spot.load_snapshot(provider, universe, DAY, moment=MOMENT)

        self.assertEqual(result['availableCount'], 3)
        self.assertEqual(result['missingCodes'], [])
        self.assertEqual(result['extraCodes'], ['000002'])
        self.assertEqual(result['rows']['000001']['dailyVolume'], 74051597)
        self.assertEqual(result['rows']['600519']['dailyVolume'], 1753404)
        self.assertEqual(result['rows']['920002']['dailyVolume'], 729266)
        self.assertEqual(result['rows']['000001']['pctChange'], 0.6838)
        self.assertEqual(result['rows']['000001']['amplitude'], 1.3675)
        self.assertEqual(
            {key: result['rows']['000001'][key] for key in ('open', 'high', 'low', 'close', 'priceStatus', 'priceSourceProvider')},
            {'open': 11.72, 'high': 11.81, 'low': 11.65, 'close': 11.78,
             'priceStatus': 'available', 'priceSourceProvider': 'sina'},
        )

    def test_invalid_partial_or_out_of_range_ohlc_is_atomic_missing(self):
        frame = pd.DataFrame([
            {'代码': 'sz000001', '最新价': 11.78, '昨收': 11.70, '今开': 12.00,
             '最高': 11.81, '最低': 11.65, '成交量': 74051597.0, '时间戳': '15:36:00'},
        ])
        provider = SimpleNamespace(stock_zh_a_spot=lambda: frame)

        result = sina_spot.load_snapshot(provider, [{'code': '000001'}], DAY, moment=MOMENT)
        row = result['rows']['000001']

        self.assertEqual(row['priceStatus'], 'missing')
        self.assertTrue(all(row[key] is None for key in ('open', 'high', 'low', 'close')))

    def test_sparse_snapshot_is_rejected_before_full_market_fanout(self):
        frame = pd.DataFrame([
            {'代码': 'sz000001', '名称': '平安银行', '最新价': 11.78, '昨收': 11.70, '今开': 11.72,
             '最高': 11.81, '最低': 11.65, '成交量': 74051597.0, '时间戳': '15:36:00'},
        ])
        provider = SimpleNamespace(stock_zh_a_spot=lambda: frame)
        universe = [
            {'code': '000001', 'name': '平安银行'},
            {'code': '600519', 'name': '贵州茅台'},
            {'code': '920002', 'name': '万达轴承'},
        ]

        with self.assertRaisesRegex(ValueError, 'coverage'):
            sina_spot.load_snapshot(provider, universe, DAY, moment=MOMENT)

    def test_stale_market_snapshot_is_rejected(self):
        frame = pd.DataFrame([
            {'代码': 'sz000001', '最新价': 11.78, '昨收': 11.70, '今开': 11.72, '最高': 11.81,
             '最低': 11.65, '成交量': 74051597.0, '时间戳': '09:45:00'},
            {'代码': 'sh600519', '最新价': 1309.30, '昨收': 1316.01, '今开': 1318.00, '最高': 1323.00,
             '最低': 1309.05, '成交量': 1753404.0, '时间戳': '09:45:00'},
            {'代码': 'bj920002', '最新价': 53.00, '昨收': 53.84, '今开': 53.70, '最高': 54.38,
             '最低': 52.74, '成交量': 729266.0, '时间戳': '09:45:00'},
        ])
        provider = SimpleNamespace(stock_zh_a_spot=lambda: frame)
        universe = [{'code': code} for code in ('000001', '600519', '920002')]

        with self.assertRaisesRegex(ValueError, 'stale'):
            sina_spot.load_snapshot(provider, universe, DAY, moment=MOMENT)


if __name__ == '__main__':
    unittest.main()
