"""Adopted 1%/90%/90% daily-close top-region stability, with exact geometry.

No network, model tuning, or ownership inference. Model nodes are represented
by uniform half-step cells solely for this explicitly adopted comparison.
"""
from bisect import bisect_left
from copy import deepcopy
from decimal import Decimal, localcontext, ROUND_CEILING, ROUND_FLOOR
from fractions import Fraction
import hashlib
import json

from .calendar import sessions, last_ten
from .eastmoney_model import VERSION, estimate_snapshots
from .model import Bar
from .ohlc4_input import validate_bar

RULE_ID = 'TOP_REGION_STABLE_1_90_90_V1'
THRESHOLDS = {'max_shift_fraction': '0.01', 'min_mass_ratio': '0.90', 'top_height_ratio': '0.90'}
POLICY_METADATA = {
    'rule_id': RULE_ID, 'version': '1',
    'source_id': 'USER-20261025-APPROVED-STABILITY-1-90-90',
    'contract_sha256': 'd6a6c1ada28d23d9691b6d397a73eedd0ac7d496801d761ce7f90c037e804f82',
    'approval_sha256': '4b3b865c968127332c40355f04c66a578c186311bc22fe97482da0727162e05f',
    'policy_known_at': '2026-10-25T12:02:40.551515+00:00',
    'known_at_basis': 'Current user approval observed during implementation; not a historical market-data clock',
}
SHIFT, RETENTION, HEIGHT = Fraction(1, 100), Fraction(9, 10), Fraction(9, 10)
MAX_CELLS = 20000


class InputError(ValueError):
    pass


class ResourceBound(InputError):
    pass


def number(value):
    if not isinstance(value, str) or not 0 < len(value) <= 200:
        raise InputError('DECIMAL_STRING_REQUIRED')
    try:
        d = Decimal(value)
        if not d.is_finite() or (d and abs(d.adjusted()) > 100):
            raise InputError('FINITE_BOUNDED_NUMBER_REQUIRED')
        return Fraction(d)
    except (ArithmeticError, ValueError) as exc:
        raise InputError('FINITE_BOUNDED_NUMBER_REQUIRED') from exc


def text(value, *, upper=False):
    if value is None:
        return None
    with localcontext() as ctx:
        ctx.prec = 50
        ctx.rounding = ROUND_CEILING if upper else ROUND_FLOOR
        return str(Decimal(value.numerator) / Decimal(value.denominator))


def empty(reason):
    return {'rule_id': RULE_ID, 'status': 'UNKNOWN', 'baseline_date': None,
            'first_limit_up_date': None, 'evaluated_sessions': 0, 'required_sessions': 0,
            'max_shift_fraction': None, 'min_mass_ratio': None, 'reasons': [reason]}


def calculation_complete(row):
    """A terminal resolution limitation is a result, not an unfinished run."""
    if row.get('screen_status') in ('PASS', 'FAIL'):
        return True
    summary=row.get('chip_stability') or {}
    reasons=set(summary.get('reasons', []))
    terminal={'NATIVE_RESOLUTION_INSUFFICIENT','PRICE_BASIS_INCOMPARABLE','COMPARISON_RESOURCE_BOUND'}
    return (summary.get('rule_id')==RULE_ID and summary.get('status')=='UNKNOWN'
            and bool(reasons) and reasons<=terminal)


def cells(snapshot):
    """Return positive-price uniform cells with full-distribution unit mass."""
    try:
        step = number(snapshot['price_grid']['step_cny'])
        profile = snapshot['profile']
        if step <= 0 or not isinstance(profile, list) or not 1 <= len(profile) <= 150:
            raise InputError('NATIVE_GRID_INVALID')
        values = [(number(p['price_cny']), number(p['share_percent'])) for p in profile]
        if any(p <= 0 or m < 0 or m > 100 for p, m in values) or any(a[0] >= b[0] for a, b in zip(values, values[1:])):
            raise InputError('NATIVE_PROFILE_INVALID')
        mass = sum(m for _, m in values)
        if mass <= 0 or abs(mass-100)>Fraction(1,1000000):
            raise InputError('NO_USABLE_DISTRIBUTION')
        return [(max(Fraction(0), p-step/2), p+step/2, m/mass) for p, m in values], step
    except (KeyError, TypeError) as exc:
        raise InputError('NATIVE_PROFILE_MISSING') from exc


def rebin(source, origin, step):
    """Sparse exact overlap allocation; grid extends across the ENTIRE support."""
    first = min((lo-origin)//step for lo, _, _ in source)
    last = max(-((origin-hi)//step) for _, hi, _ in source)
    if last-first > MAX_CELLS:
        raise ResourceBound('COMPARISON_RESOURCE_BOUND')
    mass = {}
    for lo, hi, value in source:
        if hi <= lo:
            raise InputError('CELL_WIDTH_INVALID')
        if not value:
            continue
        start, end = (lo-origin)//step, -((origin-hi)//step)
        for i in range(start, end):
            left, right = max(Fraction(0), origin+i*step), origin+(i+1)*step
            overlap = max(Fraction(0), min(hi, right)-max(lo, left))
            if overlap:
                mass[i] = mass.get(i, Fraction(0)) + value*overlap/(hi-lo)
    if sum(mass.values()) != sum(q for _, _, q in source):
        raise InputError('MASS_CONSERVATION_FAILED')
    density = {i: m/(origin+(i+1)*step-max(Fraction(0), origin+i*step)) for i, m in mass.items()}
    peak = max(density.values(), default=Fraction(0))
    if peak <= 0:
        raise InputError('NO_USABLE_DISTRIBUTION')
    top = sorted((max(Fraction(0), origin+i*step)+origin+(i+1)*step)/2
                 for i, d in density.items() if d >= HEIGHT*peak)
    if not top or any(p <= 0 for p in top):
        raise InputError('TOP_REGION_INVALID')
    return top, {'total_mass': text(sum(mass.values())), 'comparison_cells': last-first,
                 'nonzero_cells': len(mass), 'peak_density': text(peak)}


def relative_distance(baseline, current):
    """Symmetric max/min, always divided by the positive BASELINE center."""
    if not baseline or not current:
        raise InputError('TOP_REGION_MISSING')
    def candidates(sorted_values, value):
        i = bisect_left(sorted_values, value)
        return sorted_values[max(0, i-1):i+1]
    # For fixed q the denominator is constant. For fixed p, |p-q|/q is
    # monotone on either side of p, so the two surrounding q are sufficient.
    forward = max(min(abs(p-q)/q for p in candidates(current, q)) for q in baseline)
    backward = max(min(abs(p-q)/q for q in candidates(baseline, p)) for p in current)
    return max(forward, backward)


def anchor_bands(top, step):
    bands = []
    for p in top:
        lo, hi = max(Fraction(0), p-step/2-SHIFT*p), p+step/2+SHIFT*p
        if bands and lo <= bands[-1][1]:
            bands[-1] = (bands[-1][0], max(hi, bands[-1][1]))
        else:
            bands.append((lo, hi))
    return bands


def band_mass(source, band):
    lo, hi = band
    return sum(m*max(Fraction(0), min(hi, b)-max(lo, a))/(b-a) for a, b, m in source)


def judge_snapshots(snapshots, baseline_date, required, *, first_limit_up_date):
    summary = empty('CHIP_HISTORY_INCOMPLETE')
    summary.update(baseline_date=baseline_date, first_limit_up_date=first_limit_up_date,
                   required_sessions=len(required))
    evidence = {'rule_id': RULE_ID, 'thresholds': dict(THRESHOLDS), 'policy': dict(POLICY_METADATA),
                'daily': [], 'anchor_bands': []}
    try:
        base = snapshots[baseline_date]
        if base.get('model_version') != VERSION:
            raise InputError('SOURCE_MODEL_MISMATCH')
        original, step = cells(base)
        origin = original[0][0]
        top, audit = rebin(original, origin, step)
        bands = anchor_bands(top, step)
        base_mass = [band_mass(original, b) for b in bands]
        if any(m <= 0 for m in base_mass):
            raise InputError('NO_USABLE_DISTRIBUTION')
        evidence.update(baseline_date=baseline_date, baseline_top_centers=[text(p) for p in top],
                        baseline_audit=audit)
        evidence['anchor_bands'] = [{'lower_cny': text(b[0]), 'upper_cny': text(b[1]),
                                    'baseline_share_percent': text(m*100)} for b, m in zip(bands, base_mass)]
        # The smallest original top price sets the strictest native resolution
        # requirement. Refining the comparison grid does not relax this gate.
        max_native_step = min(top)*SHIFT
        maximum, minimum, reasons, unknown = None, None, [], []
        for day in required:
            snapshot = snapshots.get(day)
            if not snapshot:
                unknown.append('CHIP_HISTORY_INCOMPLETE')
                continue
            if (snapshot.get('model_version') != VERSION or snapshot.get('security_id') != base.get('security_id')
                or snapshot.get('seed_source_date') != base.get('seed_source_date')):
                unknown.append('SOURCE_MODEL_MISMATCH')
                continue
            try:
                current, native_step = cells(snapshot)
                if max(step, native_step) > max_native_step:
                    unknown.append('NATIVE_RESOLUTION_INSUFFICIENT')
                    continue
                current_top, day_audit = rebin(current, origin, step)
                shift = relative_distance(top, current_top)
                masses = [band_mass(current, b) for b in bands]
                ratios = [m/b for m, b in zip(masses, base_mass)]
                retention = min(ratios)
                maximum = shift if maximum is None else max(maximum, shift)
                minimum = retention if minimum is None else min(minimum, retention)
                summary['evaluated_sessions'] += 1
                failed = []
                if shift > SHIFT:
                    failed.append('PEAK_REGION_MOVED')
                if retention < RETENTION:
                    failed.append('ANCHOR_BAND_MASS_DECREASED')
                reasons.extend(failed)
                evidence['daily'].append({'date': day, 'status': 'FAIL' if failed else 'PASS',
                    'max_shift_fraction': text(shift, upper=True), 'min_mass_ratio': text(retention),
                    'native_step_cny': text(native_step), 'top_centers': [text(p) for p in current_top],
                    'band_share_percent': [text(m*100) for m in masses], 'audit': day_audit,
                    'model_input_prefix_sha256': snapshot.get('normalized_input_prefix_sha256')})
            except ResourceBound:
                unknown.append('COMPARISON_RESOURCE_BOUND')
            except InputError:
                unknown.append('INVALID_STABILITY_INPUT')
        summary.update(max_shift_fraction=text(maximum, upper=True), min_mass_ratio=text(minimum))
        summary['status'] = 'FAIL' if reasons else 'UNKNOWN' if unknown or not required else 'PASS'
        summary['reasons'] = sorted(set(reasons + unknown))
    except ResourceBound:
        summary['reasons'] = ['COMPARISON_RESOURCE_BOUND']
    except (InputError, KeyError):
        summary['reasons'] = ['INVALID_STABILITY_INPUT']
    evidence['summary'] = deepcopy(summary)
    return summary, evidence


def _reference_no_trade(reference_evidence, security_id, as_of):
    """Accept only explicit, dated BaoStock suspension rows from checked evidence."""
    reference_evidence=reference_evidence or {}
    digest=reference_evidence.get('source_sha256')
    if (reference_evidence.get('provider')!='BAOSTOCK_0.9.3'
        or not isinstance(digest,str) or len(digest)!=64
        or any(c not in '0123456789abcdef' for c in digest)
        or len(security_id)!=6 or not security_id.isdigit()
        or not security_id.startswith(('0','3','6'))):
        return set()
    symbol=('sh.' if security_id.startswith('6') else 'sz.')+security_id
    result=set()
    rows=reference_evidence.get('rows',{})
    if not isinstance(rows,dict):return result
    for day,row in rows.items():
        try:
            if (not isinstance(row,dict) or row.get('tradestatus')!='0'
                or row.get('date')!=day or row.get('code')!=symbol
                or row.get('adjustflag')!='3' or day>as_of or sessions(day,day)!=[day]):
                continue
            quantities=[row.get(k) for k in ('volume','amount')]
            if 'turn' in row:quantities.append(row['turn'])
            if any(not isinstance(value,str) or (value.strip() and
                (not Decimal(value).is_finite() or Decimal(value)!=0)) for value in quantities):
                continue
            result.add(day)
        except (TypeError,ValueError,ArithmeticError):
            continue
    return result


def classify(row, history, days, *, security_id, no_trade=(), reference_evidence=None,
             actual_limits=None, expected_current=None, incomparable=False):
    """Build the common-window daily-close comparisons from existing inputs."""
    if 'FAIL' in (row.get('prefilter_status'), row.get('price_position_status')):
        return empty('OTHER_CONDITION_FAILED_NOT_EVALUATED'), None
    if row.get('chip_status') != 'READY':
        return empty('CHIP_HISTORY_INCOMPLETE'), None
    try:
        if list(days) != last_ten(days[-1]):
            raise InputError('OFFICIAL_SESSIONS_INVALID')
        hits = sorted(set(row.get('recent_limit_up_dates', [])))
        if not hits or not set(hits) <= set(days):
            return empty('LIMIT_EVENT_NOT_ESTABLISHED'), None
        first = hits[0]
        states = {e['date']: e['status'] for e in row.get('limit_evidence', [])}
        if any(states.get(d) != 'FAIL' for d in days if d < first):
            return empty('EARLIER_LIMIT_STATUS_UNKNOWN'), None
        before = sessions('2026-01-01', first)
        if len(before) < 2:
            return empty('BASELINE_NOT_AVAILABLE'), None
        baseline = before[-2]
        required = [d for d in days if d >= first]
        if incomparable:
            return empty('PRICE_BASIS_INCOMPARABLE'), None
        bars = []
        for entry in history:
            if entry['date'] > days[-1]:
                continue
            bar = Bar(entry['date'], *(Decimal(entry[k]) for k in
                ('open', 'high', 'low', 'close', 'volume_shares', 'amount_cny', 'turnover_percent')))
            validate_bar(bar)
            bars.append(bar)
        if not bars or any(a.source_date >= b.source_date for a, b in zip(bars, bars[1:])):
            raise InputError('HISTORY_ORDER_INVALID')
        observed = {b.source_date: b for b in bars}
        no_trade = set(no_trade)
        no_trade.update(_reference_no_trade(reference_evidence,security_id,days[-1]))
        targets=[baseline]+required
        conflicts={}
        # The observed candle used to carry a missing baseline must not itself
        # contradict a no-trade reference just before the comparison period.
        carry_start=max((day for day in observed if day<=baseline),default=baseline)
        conflict_days=set(targets)|{day for day in no_trade if carry_start<=day<=days[-1]}
        for day in sorted(conflict_days):
            reference=(reference_evidence or {}).get('rows',{}).get(day,{})
            quote=(actual_limits or {}).get(day,{}).get(security_id,{})
            if ((day in observed and (reference.get('tradestatus')=='0' or day in no_trade))
                or (day not in observed and day in no_trade and reference.get('tradestatus')=='1')):
                conflicts[day]='DATA_TRADE_STATUS_CONFLICT'
            if day in observed:
                fields=('open','high','low','close')
                if (all(reference.get(k) is not None for k in fields)
                    and any(Decimal(reference[k])!=getattr(observed[day],k) for k in fields)):
                    conflicts[day]='DATA_PRICE_EVIDENCE_CONFLICT'
                if (quote.get('date')==day and quote.get('close') is not None
                    and Decimal(quote['close'])!=observed[day].close):
                    conflicts[day]='DATA_PRICE_EVIDENCE_CONFLICT'
        trusted_no_trade=no_trade-set(conflicts)
        # Observed reference-price discontinuities make the unadjusted price
        # spaces incomparable. Absence of such a signal is not event certification.
        for day in required:
            reference = (reference_evidence or {}).get('rows', {}).get(day, {})
            quote = (actual_limits or {}).get(day, {}).get(security_id, {})
            matched_reference=(day in observed and all(reference.get(k) is not None for k in ('open','high','low','close'))
                and day not in conflicts)
            matched_quote=(day in observed and quote.get('date')==day and quote.get('close') is not None
                and day not in conflicts)
            value = reference.get('preclose') if matched_reference else None
            if value is None and matched_quote:value=quote.get('reference_close')
            prior = max((d for d in observed if d < day), default=None)
            if value and prior and prior not in conflicts:
                between=set(sessions(prior,day)[:-1])-set(observed)
                if not between<=trusted_no_trade:
                    # preclose belongs to the preceding official session, not
                    # an older bar across an unconfirmed missing-data gap.
                    continue
                reported = Decimal(value)
                if reported.is_finite() and reported > 0 and reported.quantize(Decimal('.01')) != observed[prior].close.quantize(Decimal('.01')):
                    summary = empty('PRICE_BASIS_INCOMPARABLE')
                    summary.update(baseline_date=baseline, first_limit_up_date=first, required_sessions=len(required))
                    return summary, None
        anchors, missing_from = {}, None
        for day in targets:
            if missing_from is not None and day >= missing_from:
                continue
            if day in conflicts:
                if day==baseline:
                    summary=empty(conflicts[day])
                    summary.update(baseline_date=baseline,first_limit_up_date=first,required_sessions=len(required))
                    return summary,None
                missing_from=day
                continue
            if day in observed:
                anchors[day] = day
                continue
            prior = max((d for d in observed if d < day), default=None)
            if prior in conflicts:
                if day==baseline:
                    summary=empty(conflicts[prior])
                    summary.update(baseline_date=baseline,first_limit_up_date=first,required_sessions=len(required))
                    return summary,None
                missing_from=day
                continue
            if day not in trusted_no_trade or not prior or not (set(sessions(prior, day))-set(observed)) <= trusted_no_trade:
                if day == baseline:
                    summary = empty('BASELINE_NOT_AVAILABLE')
                    summary.update(baseline_date=baseline, first_limit_up_date=first, required_sessions=len(required))
                    return summary, None
                # Later prefixes are incomplete too; earlier supported failures
                # remain evidence and must not be hidden by this later gap.
                missing_from = day
                continue
            anchors[day] = prior
        support_date = bars[-1].source_date
        calculated = estimate_snapshots(bars, sorted(set(anchors.values())|{support_date}), security_id=security_id)
        snapshots = {day: deepcopy(calculated[anchor]) for day, anchor in anchors.items()}
        if expected_current and (calculated[support_date]['normalized_input_prefix_sha256'] != expected_current.get('normalized_input_prefix_sha256')):
            return empty('SOURCE_MODEL_MISMATCH'), None
        summary, evidence = judge_snapshots(snapshots, baseline, required, first_limit_up_date=first)
        if conflicts:
            summary['reasons']=sorted(set(summary['reasons'])|set(conflicts.values()))
            evidence['summary']=deepcopy(summary)
            evidence['data_conflicts']=conflicts
        evidence.update(security_id=security_id, as_of=days[-1], observed_anchor_dates=anchors,
                        input_history_sha256=hashlib.sha256(json.dumps([r for r in history if r['date']<=days[-1]], sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                        corporate_action_coverage_authenticated=False,
                        meaning='Daily-close model distribution stability; not original holder retention')
        return summary, evidence
    except (ValueError, ArithmeticError, KeyError, TypeError):
        return empty('INVALID_STABILITY_INPUT'), None
