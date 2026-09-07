"""Bounded, anonymous BaoStock SH/SZ source pairs. Never changes requests or socket defaults."""
from __future__ import annotations
import contextlib
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import io
import json
from pathlib import Path
import re
import socket
import threading
import time
from unittest.mock import patch
import pandas as pd

HOST, PORT = 'public-api.baostock.com', 10030
_SDK_LOCK = threading.Lock()
TIMES = {'0945', '1000', '1015', '1030', '1045', '1100', '1115', '1130',
         '1315', '1330', '1345', '1400', '1415', '1430', '1445', '1500'}


class UnsupportedMarket(ValueError):
    pass


class BaoStockTransportError(ConnectionError):
    pass


class BaoStockDataError(ValueError):
    pass


def normalized_code(code):
    if re.fullmatch(r'(?:sh\.|sz\.)?\d{6}', code):
        digits = code.split('.')[-1]
        market = 'sh' if digits.startswith('6') else 'sz' if digits.startswith(('0', '3')) else None
        if market and ('.' not in code or code.startswith(market + '.')):
            return market + '.' + digits
    raise UnsupportedMarket('BaoStock仅支持沪深A股，不支持该市场或代码')


class _BoundedSocket:
    def __init__(self, raw, timeout, interval):
        self.raw, self.timeout, self.interval = raw, timeout, interval
        self.last_send = 0.0
        self.deadline = 0.0
        self.received = 0
        self.failure = None
        self.requests = 0

    def send(self, message):
        delay = self.interval - (time.monotonic() - self.last_send)
        if delay > 0:
            time.sleep(delay)
        self.deadline = time.monotonic() + self.timeout
        self.received, self.failure = 0, None
        self.last_send = time.monotonic()
        self.requests += 1
        try:
            self.raw.settimeout(self.timeout)
            self.raw.sendall(message)
        except Exception as exc:
            self.failure = type(exc).__name__
            raise
        return len(message)

    def recv(self, size):
        try:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('BaoStock receive deadline')
            self.raw.settimeout(remaining)
            value = self.raw.recv(size)
            if not value:
                raise ConnectionError('BaoStock closed connection')
            self.received += len(value)
            if self.received > 4 * 1024 * 1024:
                raise ValueError('BaoStock page exceeds byte limit')
            return value
        except Exception as exc:
            self.failure = type(exc).__name__
            raise

    def close(self):
        self.raw.close()


class BaoStockSession:
    def __init__(self, timeout=20, interval=0.75):
        if not 0 < timeout <= 60 or not 0 <= interval <= 5:
            raise ValueError('invalid bounded connection settings')
        self.timeout, self.interval = timeout, interval
        self._sdk = None
        self._wire = None
        self._owns_lock = False
        self._operation_lock = threading.Lock()
        self.last_raw = {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _call(self, fn, *args, **kwargs):
        # SDK logs transport exceptions; retain typed failure without emitting socket details.
        with contextlib.redirect_stdout(io.StringIO()):
            result = fn(*args, **kwargs)
        if self._wire and self._wire.failure:
            raise BaoStockTransportError('BaoStock连接或读取失败：' + self._wire.failure)
        return result

    def _ensure_connected(self):
        if self._sdk is not None:
            return
        if not _SDK_LOCK.acquire(blocking=False):
            raise BaoStockTransportError('BaoStock单连接正在使用')
        self._owns_lock = True
        try:
            import baostock as sdk
            import baostock.common.context as context
            import baostock.util.socketutil as socketutil
            if getattr(context, 'default_socket', None) is not None or hasattr(context, 'apiKey'):
                raise BaoStockTransportError('BaoStock已有其他会话，无法建立独立匿名连接')
            raw = socket.create_connection((HOST, PORT), timeout=self.timeout)
            self._wire = _BoundedSocket(raw, self.timeout, self.interval)
            self._sdk = sdk
            def connect(_socket_util):
                context.default_socket = self._wire
            # Scoped SDK connection factory only; all other clients and socket defaults stay intact.
            with patch.object(socketutil.SocketUtil, 'connect', connect):
                result = self._call(sdk.login)
            if result.error_code != '0':
                raise BaoStockTransportError('BaoStock匿名登录失败：' + result.error_code)
        except Exception as exc:
            self.close()
            if isinstance(exc, BaoStockTransportError):
                raise
            raise BaoStockTransportError('BaoStock连接失败：' + type(exc).__name__) from exc

    def close(self):
        try:
            if self._sdk and self._wire and not self._wire.failure:
                try:
                    self._call(self._sdk.logout)
                except Exception:
                    pass
        finally:
            if self._wire:
                with contextlib.suppress(Exception):
                    self._wire.close()
                import baostock.common.context as context
                if getattr(context, 'default_socket', None) is self._wire:
                    context.default_socket = None
            self._wire, self._sdk = None, None
            if self._owns_lock:
                self._owns_lock = False
                _SDK_LOCK.release()

    def _query(self, code, start, end, frequency):
        fields = 'date,time,code,volume,close' if frequency == '15' else 'date,code,volume,close'
        result = self._call(self._sdk.query_history_k_data_plus, code, fields,
            start_date=start, end_date=end, frequency=frequency, adjustflag='3')
        if result.error_code != '0':
            raise BaoStockDataError('BaoStock行情查询未成功：' + result.error_code)
        if result.fields != fields.split(','):
            raise BaoStockDataError('BaoStock返回字段与请求不一致')
        rows, pages = [], set()
        while self._call(result.next):
            pages.add(str(result.cur_page_num))
            if len(pages) > 4 or len(rows) >= (3000 if frequency == '15' else 186):
                raise BaoStockDataError('BaoStock分页或行数超过半年查询上限')
            values = result.get_row_data()
            if len(values) != len(result.fields):
                raise BaoStockDataError('BaoStock返回行字段数量错误')
            rows.append(dict(zip(result.fields, values)))
        if result.error_code != '0':
            raise BaoStockDataError('BaoStock分页失败：' + result.error_code)
        return rows

    @staticmethod
    def _normalize(rows, code, dates, minute):
        wanted = set(dates)
        start, end = min(dates), max(dates)
        normalized, seen = [], set()
        for row in rows:
            try:
                day = row['date']
                if row['code'] != code or date.fromisoformat(day).isoformat() != day or not start <= day <= end:
                    raise ValueError('code/date')
                volume, close = Decimal(row['volume']), Decimal(row['close'])
                if not volume.is_finite() or volume < 0 or volume != volume.to_integral_value() or not close.is_finite() or close < 0:
                    raise ValueError('volume/close')
                timestamp = day
                if minute:
                    raw_time = row['time']
                    if not re.fullmatch(r'\d{17}', raw_time) or raw_time[:8] != day.replace('-', '') or raw_time[8:12] not in TIMES or raw_time[12:] != '00000':
                        raise ValueError('time')
                    timestamp = datetime.strptime(raw_time, '%Y%m%d%H%M%S%f').strftime('%Y-%m-%d %H:%M:%S')
                if timestamp in seen:
                    raise ValueError('duplicate timestamp')
                seen.add(timestamp)
                if day in wanted:
                    normalized.append({'day' if minute else 'date': timestamp,
                        'volume': int(volume), 'close': float(close)})
            except (KeyError, ValueError, InvalidOperation, TypeError) as exc:
                raise BaoStockDataError('BaoStock原始代码、日期、时间或成交量校验失败') from exc
        return pd.DataFrame(normalized, columns=['day' if minute else 'date', 'volume', 'close'])

    def pair(self, code, dates):
        symbol = normalized_code(code)  # Unsupported markets never open the connection.
        dates = sorted(set(dates))
        if not dates or len(dates) > 186:
            raise BaoStockDataError('BaoStock日期数量无效')
        try:
            if any(not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day) for day in dates):
                raise ValueError('invalid date format')
            parsed = [date.fromisoformat(day) for day in dates]
            if (parsed[-1] - parsed[0]).days > 186:
                raise ValueError('range too long')
        except (ValueError, TypeError) as exc:
            raise BaoStockDataError('BaoStock仅允许有效的半年内日期区间') from exc
        with self._operation_lock:
            self._ensure_connected()
            try:
                minute = self._query(symbol, dates[0], dates[-1], '15')
                daily = self._query(symbol, dates[0], dates[-1], 'd')
                self.last_raw = {'source': 'baostock', 'code': symbol, 'dates': dates,
                    'adjustflag': '3', 'minute': minute, 'daily': daily}
                return self._normalize(minute, symbol, dates, True), self._normalize(daily, symbol, dates, False)
            except Exception:
                self.close()
                raise


if __name__ == '__main__':
    import argparse
    import hashlib
    parser = argparse.ArgumentParser(description='Read-only BaoStock probe; writes scratch evidence only.')
    parser.add_argument('--symbols', default='600519,000001,600000')
    parser.add_argument('--dates', required=True, help='Comma-separated ISO trading dates, at most six months')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    codes = args.symbols.split(',')
    if len(codes) > 3:
        parser.error('probe is limited to three stocks')
    output = Path(args.out).resolve()
    project = Path(__file__).resolve().parents[1]
    if project.name == 'backend':
        project = project.parent
    if any(protected == output or protected in output.parents for protected in
           (project / 'public/data', project / 'var/data', (project / 'data').resolve())):
        parser.error('probe output must be a separate scratch directory')
    output.mkdir(parents=True, exist_ok=True)
    from collect import evaluate
    summary = []
    with BaoStockSession() as session:
        for code in codes:
            minute, daily = session.pair(code, args.dates.split(','))
            raw = json.dumps(session.last_raw, ensure_ascii=False, indent=2).encode()
            (output / (code + '-raw.json')).write_bytes(raw)
            checked = [evaluate(code.split('.')[-1], code, day, minute, daily) for day in args.dates.split(',')]
            for day, row in zip(args.dates.split(','), checked):
                row['date'] = day
                row['sourceProvider'] = 'baostock'
            (output / (code + '-evaluated.json')).write_text(json.dumps(checked, ensure_ascii=False, indent=2))
            item = {'code': code, 'dates': len(checked), 'matched': sum(r['status'] == 'ok' for r in checked),
                'statuses': {s: sum(r['status'] == s for r in checked) for s in sorted({r['status'] for r in checked})},
                'minuteRows': len(minute), 'dailyRows': len(daily), 'rawSha256': hashlib.sha256(raw).hexdigest(),
                'firstTimestamp': str(minute.day.min()) if not minute.empty else None,
                'lastTimestamp': str(minute.day.max()) if not minute.empty else None}
            summary.append(item)
            print(json.dumps(item, ensure_ascii=False), flush=True)
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
