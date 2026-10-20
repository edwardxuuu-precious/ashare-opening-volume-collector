import _bootstrap
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend/scripts'))
from baostock_source import BaoStockSession, BaoStockDataError, UnsupportedMarket, _BoundedSocket

DAY = '2026-09-04'
MINUTE = {'date': DAY, 'time': '20260904094500000', 'code': 'sh.600519', 'volume': '775476', 'close': '1313.0100'}
DAILY = {'date': DAY, 'code': 'sh.600519', 'volume': '4541564', 'close': '1330.0000'}


class BaoStockSourceTests(unittest.TestCase):
    def test_unsupported_market_rejected_before_connection(self):
        session = BaoStockSession()
        with patch.object(session, '_ensure_connected') as connect:
            for code in ('920002', 'bj.920002', 'sh.000001'):
                with self.assertRaises(UnsupportedMarket): session.pair(code, [DAY])
            connect.assert_not_called()

    def test_whole_pair_is_unadjusted_same_symbol_and_integer_shares(self):
        session = BaoStockSession()
        with patch.object(session, '_ensure_connected'), patch.object(session, '_query', side_effect=[[MINUTE], [DAILY]]) as query:
            minute, daily = session.pair('600519', [DAY])
        self.assertEqual([c.args for c in query.call_args_list], [('sh.600519', DAY, DAY, '15'), ('sh.600519', DAY, DAY, 'd')])
        self.assertEqual(minute.volume.iloc[0], 775476)
        self.assertEqual(daily.volume.iloc[0], 4541564)
        self.assertEqual(minute.day.iloc[0], DAY + ' 09:45:00')
        self.assertEqual(session.last_raw['adjustflag'], '3')

    def test_wrong_code_date_time_or_fractional_shares_rejected(self):
        for change in ({'code': 'sz.000001'}, {'date': '2026-09-03'}, {'time': '20260904093100000'},
                       {'time': '20260903094500000'}, {'volume': '1.1'}, {'volume': '-1'}, {'close': 'NaN'}):
            with self.subTest(change=change), self.assertRaises(BaoStockDataError):
                BaoStockSession._normalize([dict(MINUTE, **change)], 'sh.600519', [DAY], True)

    def test_duplicate_rows_rejected_and_empty_data_remains_empty(self):
        with self.assertRaises(BaoStockDataError):
            BaoStockSession._normalize([MINUTE, MINUTE], 'sh.600519', [DAY], True)
        data = BaoStockSession._normalize([], 'sh.600519', [DAY], True)
        self.assertEqual(list(data.columns), ['day', 'volume', 'close'])
        self.assertTrue(data.empty)

    def test_failed_pair_closes_session(self):
        session = BaoStockSession()
        with patch.object(session, '_ensure_connected'), patch.object(session, '_query', side_effect=BaoStockDataError('failed')), patch.object(session, 'close') as close:
            with self.assertRaises(BaoStockDataError): session.pair('600519', [DAY])
            close.assert_called_once()

    def test_range_longer_than_six_months_never_connects(self):
        session = BaoStockSession()
        with patch.object(session, '_ensure_connected') as connect:
            with self.assertRaises(BaoStockDataError): session.pair('600519', ['2025-01-01', DAY])
            connect.assert_not_called()

    def test_socket_eof_is_failure_instead_of_sdk_infinite_receive_loop(self):
        raw = MagicMock(); raw.recv.return_value = b''
        wire = _BoundedSocket(raw, 20, 0); wire.send(b'public request')
        with self.assertRaises(ConnectionError): wire.recv(8192)
        self.assertEqual(wire.failure, 'ConnectionError')
        raw.sendall.assert_called_once_with(b'public request')

    def test_socket_total_deadline_and_byte_bound(self):
        raw = MagicMock(); raw.recv.return_value = b'a'
        wire = _BoundedSocket(raw, 20, 0)
        with patch('baostock_source.time.monotonic', return_value=10): wire.send(b'request')
        with patch('baostock_source.time.monotonic', return_value=31), self.assertRaises(TimeoutError): wire.recv(8192)
        wire.last_send = 0
        wire.send(b'request'); wire.received = 4 * 1024 * 1024
        with self.assertRaises(ValueError): wire.recv(8192)

    def test_query_checks_pagination_failure_and_request_adjustment(self):
        session = BaoStockSession(); session._sdk = MagicMock()
        result = MagicMock(); result.error_code = '0'; result.fields = ['date', 'code', 'volume', 'close']
        result.cur_page_num = '1'; result.get_row_data.return_value = [DAY, 'sh.600519', '4541564', '1330']
        result.next.side_effect = [True, False]; session._sdk.query_history_k_data_plus.return_value = result
        self.assertEqual(len(session._query('sh.600519', DAY, DAY, 'd')), 1)
        self.assertEqual(session._sdk.query_history_k_data_plus.call_args.kwargs['adjustflag'], '3')
        result.next.side_effect = None
        def bad_page(): result.error_code = '10002007'; return False
        result.next.side_effect = bad_page
        with self.assertRaises(BaoStockDataError): session._query('sh.600519', DAY, DAY, 'd')

    def test_query_row_limit_is_bounded(self):
        session = BaoStockSession(); session._sdk = MagicMock()
        result = MagicMock(); result.error_code = '0'; result.fields = ['date', 'code', 'volume', 'close']
        result.cur_page_num = '1'; result.next.return_value = True
        result.get_row_data.return_value = [DAY, 'sh.600519', '4541564', '1330']
        session._sdk.query_history_k_data_plus.return_value = result
        with self.assertRaises(BaoStockDataError): session._query('sh.600519', DAY, DAY, 'd')
        self.assertEqual(result.get_row_data.call_count, 186)


if __name__ == '__main__': unittest.main()
