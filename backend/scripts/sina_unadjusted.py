"""Skip a proven-unused AKShare qfq lookup only for the pinned unadjusted minute path.

No installed file is modified. A source fingerprint guards the adapter; unknown
versions use the unchanged AKShare function. Actual data requests retain the
collector's normal request budget. The result still comes from AKShare itself.
"""
import hashlib
import inspect
import threading

SOURCE_SHA256 = 'cd72eb46d4d28d5da5338923104f798fdd8a645160ae91393aa12b3de6e44604'
LOCK = threading.RLock()


def minute_unadjusted(ak, symbol):
    import akshare.stock.stock_zh_a_sina as provider
    function = ak.stock_zh_a_minute
    try:
        known = (getattr(ak, '__version__', '') == '1.18.88' and
                 function is provider.stock_zh_a_minute and
                 hashlib.sha256(inspect.getsource(function).encode()).hexdigest() == SOURCE_SHA256)
    except (TypeError, OSError):
        known = False
    if not known:
        return function(symbol=symbol, period='15', adjust='')
    with LOCK:
        original = provider.stock_zh_a_daily
        # In the pinned function this return value is discarded before adjust==''
        # returns temp_df; no price/volume/corporate-action data is substituted.
        provider.stock_zh_a_daily = lambda **kwargs: None
        try:
            return function(symbol=symbol, period='15', adjust='')
        finally:
            provider.stock_zh_a_daily = original
