"""Pure, fixed V3 saved-bar classification without orders or parameter search."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
import hashlib
import json

from .types import DailySeries, FeatureResult, Status


RULE_ID = 'TREND_LOCAL_OR_CONTEXT_SHRINK70_FIXED_BASELINE_SURGE_V3_CANDIDATE'
PARAMETER_SHA256 = '96ca6b8ae2a85b054037b960e89fabf9722986ab1a63616d26a0ce5dc075bfff'


@dataclass(frozen=True)
class V3Parameters:
    contract_sha256: str
    trend_context_bars: int = 20
    trend_local_bars: int = 5
    trend_close_ols_slope_lt: Decimal = Decimal('0')
    setup_close_to_context_high_max: Decimal = Decimal('0.95')
    shrink_recent_bars: int = 3
    shrink_reference_bars: int = 10
    shrink_recent_to_reference_max: Decimal = Decimal('0.70')
    watch_valid_bars: int = 5
    surge_volume_to_setup_mean_min: Decimal = Decimal('2.00')
    surge_close_return_pct_min: Decimal = Decimal('4.00')
    surge_close_gt_open: bool = True
    require_signal_prior_trend: bool = False
    rule_id: str = RULE_ID
    parameter_sha256: str = PARAMETER_SHA256

    def __post_init__(self):
        if (self.rule_id != RULE_ID or self.parameter_sha256 != PARAMETER_SHA256
                or len(self.contract_sha256) != 64
                or any(char not in '0123456789abcdef' for char in self.contract_sha256)):
            raise ValueError('FIXED_V3_RULE_IDENTITY_REQUIRED')
        expected = parameter_values()
        for name, value in expected.items():
            actual = getattr(self, name)
            parsed = Decimal(value) if isinstance(value, str) else value
            if type(actual) is not type(parsed) or actual != parsed:
                raise ValueError('FIXED_V3_PARAMETERS_REQUIRED_' + name.upper())


def parameter_values():
    return {'trend_context_bars': 20, 'trend_local_bars': 5,
            'trend_close_ols_slope_lt': '0', 'setup_close_to_context_high_max': '0.95',
            'shrink_recent_bars': 3, 'shrink_reference_bars': 10,
            'shrink_recent_to_reference_max': '0.70', 'watch_valid_bars': 5,
            'surge_volume_to_setup_mean_min': '2.00', 'surge_close_return_pct_min': '4.00',
            'surge_close_gt_open': True, 'require_signal_prior_trend': False}


def canonical_parameter_sha256(values):
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def load_parameters(contract_bytes):
    contract = json.loads(contract_bytes)
    values = contract['parameters']
    if (contract.get('schema') != 'filter.trend-volume-v3-candidate-contract.v1'
            or contract.get('rule_id') != RULE_ID or contract.get('version') != 'V3'
            or values != parameter_values() or canonical_parameter_sha256(values) != PARAMETER_SHA256
            or contract.get('parameter_sha256') != PARAMETER_SHA256 or contract.get('fibonacci') is not False):
        raise ValueError('FIXED_V3_CANDIDATE_CONTRACT_MISMATCH')
    parsed = {name: Decimal(value) if isinstance(value, str) else value for name, value in values.items()}
    return V3Parameters(contract_sha256=hashlib.sha256(contract_bytes).hexdigest(), **parsed)


@dataclass(frozen=True)
class SetupCandidate:
    day: date | None
    age_bars: int
    status: Status
    trend: FeatureResult
    shrink: FeatureResult
    recent_volume_sum: Decimal | None
    recent_volume_count: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class SetupChoice:
    status: Status
    setup: SetupCandidate | None
    checked: tuple[SetupCandidate, ...]
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class V3DayResult:
    day: date
    rule_id: str
    contract_sha256: str
    parameter_sha256: str
    setup: SetupChoice
    surge: FeatureResult
    status: Status
    used_prefix_sha256: str
    can_generate_order: bool = False


def prefix_sha256(series, day):
    used = [[bar.day.isoformat(), str(bar.open), str(bar.close), str(bar.high), str(bar.low),
             str(bar.volume), bar.complete] for bar in series.bars if bar.day <= day]
    return hashlib.sha256(json.dumps(used, separators=(',', ':')).encode('utf-8')).hexdigest()


def _text(value):
    if isinstance(value, Decimal):
        return format(value, '.12f').rstrip('0').rstrip('.') or '0'
    return value


def _feature(status, metrics=None, windows=None, reasons=()):
    return FeatureResult(status, tuple((key, _text(value)) for key, value in (metrics or {}).items()),
                         tuple((key, tuple(bar.day for bar in bars)) for key, bars in (windows or {}).items()),
                         tuple(reasons))


def _aggregate(*statuses):
    if Status.FAIL in statuses:
        return Status.FAIL
    return Status.PASS if all(value == Status.PASS for value in statuses) else Status.UNKNOWN


def _problem(bars, count):
    if len(bars) != count:
        return 'INSUFFICIENT_HISTORY'
    if not all(bar.complete for bar in bars):
        return 'INCOMPLETE_BAR_IN_WINDOW'
    return None


def _ols(window):
    count, sx = len(window), sum(range(len(window)))
    numerator = count * sum(Decimal(index) * bar.close for index, bar in enumerate(window)) - sx * sum(bar.close for bar in window)
    denominator = Decimal(count * sum(index * index for index in range(count)) - sx * sx)
    return numerator, denominator


def _trend(bars, parameters):
    context, local = bars[-parameters.trend_context_bars:], bars[-parameters.trend_local_bars:]
    windows = {'context': context, 'local': local}
    problem = _problem(context, parameters.trend_context_bars)
    if problem:
        return _feature(Status.UNKNOWN, windows=windows, reasons=(problem,))
    if any(bar.close <= 0 or bar.high <= 0 for bar in context):
        return _feature(Status.UNKNOWN, windows=windows, reasons=('NONPOSITIVE_TREND_PRICE',))
    context_num, context_den = _ols(context)
    local_num, local_den = _ols(local)
    highest, close = max(bar.high for bar in context), context[-1].close
    slope_ok = (context_num < parameters.trend_close_ols_slope_lt * context_den
                or local_num < parameters.trend_close_ols_slope_lt * local_den)
    depth_ok = close <= parameters.setup_close_to_context_high_max * highest
    reasons = tuple(reason for ok, reason in ((slope_ok, 'BOTH_CONTEXT_AND_LOCAL_SLOPES_NOT_NEGATIVE'),
                                             (depth_ok, 'CLOSE_ABOVE_RETRACEMENT_THRESHOLD')) if not ok)
    return _feature(Status.PASS if not reasons else Status.FAIL,
                    {'context_close_ols_slope': context_num / context_den,
                     'local_close_ols_slope': local_num / local_den,
                     'context_slope_negative': context_num < 0, 'local_slope_negative': local_num < 0,
                     'window_close': close, 'window_high': highest,
                     'close_to_window_high': close / highest,
                     'pullback_pct': 100 * (highest - close) / highest}, windows, reasons)


def _shrink(bars, parameters):
    recent_count, reference_count = parameters.shrink_recent_bars, parameters.shrink_reference_bars
    recent = bars[-recent_count:]
    end = max(0, len(bars) - recent_count)
    reference = bars[max(0, end - reference_count):end]
    windows = {'recent': recent, 'reference': reference}
    problems = tuple(dict.fromkeys(problem for problem in (_problem(recent, recent_count),
                                                          _problem(reference, reference_count)) if problem))
    if problems:
        return _feature(Status.UNKNOWN, windows=windows, reasons=problems)
    if any(bar.volume < 0 for bar in recent + reference):
        return _feature(Status.UNKNOWN, windows=windows, reasons=('NEGATIVE_VOLUME',))
    recent_sum, reference_sum = sum(bar.volume for bar in recent), sum(bar.volume for bar in reference)
    if reference_sum <= 0 or recent_sum <= 0:
        reason = 'ZERO_REFERENCE_VOLUME' if reference_sum <= 0 else 'ZERO_SETUP_BASELINE'
        return _feature(Status.UNKNOWN, windows=windows, reasons=(reason,))
    passed = recent_sum * reference_count <= parameters.shrink_recent_to_reference_max * reference_sum * recent_count
    return _feature(Status.PASS if passed else Status.FAIL,
                    {'recent_volume_sum_exact': str(recent_sum), 'recent_volume_count': recent_count,
                     'reference_volume_sum_exact': str(reference_sum), 'reference_volume_count': reference_count,
                     'recent_mean_volume': recent_sum / recent_count,
                     'reference_mean_volume': reference_sum / reference_count,
                     'recent_to_reference_volume': recent_sum * reference_count / (reference_sum * recent_count)},
                    windows, () if passed else ('SHRINK_VOLUME_RATIO_ABOVE_THRESHOLD',))


def _choose_setup(prior, parameters):
    checked = []
    for age in range(1, parameters.watch_valid_bars + 1):
        index = len(prior) - age
        history = prior[:index + 1] if index >= 0 else ()
        trend, shrink = _trend(history, parameters), _shrink(history, parameters)
        status = _aggregate(trend.status, shrink.status)
        reasons = tuple(dict.fromkeys(trend.reasons + shrink.reasons))
        recent = history[-parameters.shrink_recent_bars:]
        checked.append(SetupCandidate(history[-1].day if history else None, age, status, trend, shrink,
                                      sum(bar.volume for bar in recent) if recent else None, len(recent), reasons))
    for candidate in checked:
        if candidate.status == Status.UNKNOWN:
            return SetupChoice(Status.UNKNOWN, None, tuple(checked), ('NEWEST_UNRESOLVED_SETUP',) + candidate.reasons)
        if candidate.status == Status.PASS:
            return SetupChoice(Status.PASS, candidate, tuple(checked))
    return SetupChoice(Status.FAIL, None, tuple(checked), ('NO_VALID_SETUP_IN_WATCH_WINDOW',))


def _surge(prefix, choice, parameters):
    bars = prefix[-2:]
    windows = {'signal_and_previous': bars}
    problem = _problem(bars, 2)
    if problem:
        return _feature(Status.UNKNOWN, windows=windows, reasons=(problem,))
    previous, today = bars
    if previous.close <= 0 or min(today.close, today.open) <= 0:
        return _feature(Status.UNKNOWN, windows=windows, reasons=('NONPOSITIVE_SIGNAL_PRICE',))
    price_ok = 100 * (today.close - previous.close) >= parameters.surge_close_return_pct_min * previous.close
    bullish = today.close > today.open
    metrics = {'volume': today.volume, 'previous_volume': previous.volume,
               'volume_to_previous': today.volume / previous.volume if previous.volume > 0 else None,
               'open': today.open, 'close': today.close, 'previous_close': previous.close,
               'close_return_pct': 100 * (today.close - previous.close) / previous.close,
               'close_gt_open': bullish}
    reasons = [reason for ok, reason in ((price_ok, 'SURGE_CLOSE_RETURN_BELOW_THRESHOLD'),
                                         (bullish, 'SURGE_NOT_BULLISH_CANDLE')) if not ok]
    if choice.status != Status.PASS:
        return _feature(Status.FAIL if reasons else Status.UNKNOWN, metrics, windows,
                        tuple(reasons) + ('SETUP_BASELINE_UNAVAILABLE',))
    setup = choice.setup
    volume_ok = today.volume * setup.recent_volume_count >= parameters.surge_volume_to_setup_mean_min * setup.recent_volume_sum
    metrics.update(setup_mean_volume=setup.recent_volume_sum / setup.recent_volume_count,
                   volume_to_setup_mean=today.volume * setup.recent_volume_count / setup.recent_volume_sum)
    if not volume_ok:
        reasons.append('SURGE_VOLUME_TO_SETUP_BASELINE_BELOW_THRESHOLD')
    return _feature(Status.PASS if not reasons else Status.FAIL, metrics, windows, reasons)


def screen_v3_day(series: DailySeries, day: date, parameters: V3Parameters) -> V3DayResult:
    """Choose a prior setup, then test this completed close without a second trend gate."""
    prefix = tuple(bar for bar in series.bars if bar.day <= day)
    prefix_hash = prefix_sha256(series, day)
    if not prefix or prefix[-1].day != day:
        choice = SetupChoice(Status.UNKNOWN, None, (), ('SIGNAL_DAY_MISSING',))
        surge = _feature(Status.UNKNOWN, reasons=('SIGNAL_DAY_MISSING',))
        status = Status.UNKNOWN
    else:
        with localcontext() as context:
            context.prec = 200
            choice = _choose_setup(prefix[:-1], parameters)
            surge = _surge(prefix, choice, parameters)
            status = _aggregate(choice.status, surge.status)
    return V3DayResult(day, parameters.rule_id, parameters.contract_sha256, parameters.parameter_sha256,
                       choice, surge, status, prefix_hash)


def _feature_dict(feature):
    return {'status': feature.status.value, 'metrics': dict(feature.metrics),
            'windows': {key: [day.isoformat() for day in days] for key, days in feature.windows},
            'reasons': list(feature.reasons)}


def _candidate_dict(candidate):
    return {'date': candidate.day.isoformat() if candidate.day else None, 'age_bars': candidate.age_bars,
            'status': candidate.status.value, 'reasons': list(candidate.reasons),
            'baseline_volume_sum_exact': str(candidate.recent_volume_sum) if candidate.recent_volume_sum is not None else None,
            'baseline_volume_count': candidate.recent_volume_count,
            'trend_at_setup': _feature_dict(candidate.trend), 'shrink_at_setup': _feature_dict(candidate.shrink)}


def result_dict(result):
    chosen = result.setup.setup
    setup = {'status': result.setup.status.value, 'date': chosen.day.isoformat() if chosen else None,
             'age_bars': chosen.age_bars if chosen else None, 'reasons': list(result.setup.reasons),
             'checked_candidates': [_candidate_dict(candidate) for candidate in result.setup.checked]}
    if chosen:
        setup.update(_candidate_dict(chosen))
        with localcontext() as context:
            context.prec = 200
            setup['baseline_mean_volume'] = _text(chosen.recent_volume_sum / chosen.recent_volume_count)
    return {'date': result.day.isoformat(), 'rule_id': result.rule_id, 'contract_sha256': result.contract_sha256,
            'parameter_sha256': result.parameter_sha256, 'status': result.status.value,
            'conditions': {'setup': setup, 'surge': _feature_dict(result.surge)},
            'trend_scope': 'chosen prior setup only; no signal-prior trend gate',
            'arithmetic': 'Decimal precision200; decisions use unrounded cross multiplication; metric displays use12 decimal places',
            'used_prefix_sha256': result.used_prefix_sha256, 'can_generate_order': result.can_generate_order}
