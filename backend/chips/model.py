"""Standalone retrospective chip-distribution estimate. No strategy imports or I/O.

The user fixed the historical-turnover coefficient at 2. All other model choices
are explicit in MODEL. This is not a reimplementation certified against a vendor.
Prices use fixed 0.01 CNY cells, so an old peak is compared at the same price.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import hashlib
import json
from typing import Mapping

VERSION = "chip-estimate.triangle-vwap.fixed-cent.decay2.v1"
STEP = Decimal("0.01")
HALF = STEP / 2
COEFFICIENT = Decimal("2")
PRECISION = 50
MODEL = {
    "version": VERSION,
    "historical_turnover_decay_coefficient": "2",
    "price_bin_width_cny": "0.01",
    "price_bin_definition": "centred on p, [p-0.005,p+0.005); a flat bar is a point mass",
    "daily_allocation": "triangle on [low,high], mode=amount_cny/volume_shares",
    "effective_replacement": "min(1,2*turnover_percent/100)",
    "update": "D[t]=(1-a[t])*D[t-1]+a[t]*Triangle[t]",
    "initialization": "first valid observed positive-volume bar is an estimated full triangle",
    "vwap_rounding": "outside OHLC only accepted if rounding to source price tick puts it on a boundary; adjustment disclosed",
    "working_decimal_precision": PRECISION,
    "corporate_action_treatment": "unadjusted; corporate-action history not certified or invented",
    "peak_ties": "all exactly equal maximum cells retained",
    "unchanged_threshold": None,
    "vendor_equivalence": "NOT_CALIBRATED",
    "historical_PIT_authenticated": False,
    "can_generate_order": False,
}


def number(text, *, minimum_exponent=-20):
    if not isinstance(text, str) or not text or len(text) > 100:
        raise ValueError("EXACT_DECIMAL_STRING_REQUIRED")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("INVALID_DECIMAL") from exc
    if not value.is_finite() or (value and not minimum_exponent <= value.adjusted() <= 20):
        raise ValueError("FINITE_BOUNDED_DECIMAL_REQUIRED")
    return value


def iso_day(text):
    if not isinstance(text, str) or date.fromisoformat(text).isoformat() != text:
        raise ValueError("ISO_SOURCE_DATE_REQUIRED")
    return text


def decimal_text(value):
    """Preserve significant digits without expanding tiny seed bounds into pages."""
    return str(value)


def price_text(index):
    return f"{Decimal(index) * STEP:.2f}"


@dataclass(frozen=True)
class Bar:
    source_date: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume_shares: Decimal
    amount_cny: Decimal
    turnover_pct: Decimal

    def mode(self):
        iso_day(self.source_date)
        values = (self.open, self.high, self.low, self.close,
                  self.volume_shares, self.amount_cny, self.turnover_pct)
        if any(not isinstance(v, Decimal) or not v.is_finite() for v in values):
            raise ValueError("FINITE_DECIMAL_BAR_REQUIRED")
        if not (0 < self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high):
            raise ValueError("POSITIVE_ORDERED_OHLC_REQUIRED")
        if any(v != v.quantize(STEP) for v in (self.open, self.high, self.low, self.close)):
            raise ValueError("SOURCE_PRICES_MUST_USE_CENT_TICKS")
        if self.high / STEP > 1000000:
            raise ValueError("PRICE_GRID_CAP_EXCEEDED")
        if min(self.volume_shares, self.amount_cny, self.turnover_pct) < 0:
            raise ValueError("NONNEGATIVE_VOLUME_AMOUNT_TURNOVER_REQUIRED")
        if self.volume_shares == 0:
            if self.amount_cny != 0 or self.turnover_pct != 0:
                raise ValueError("ZERO_VOLUME_REQUIRES_EXPLICIT_ZERO_AMOUNT_AND_TURNOVER")
            return None, None
        if self.amount_cny <= 0:
            raise ValueError("POSITIVE_AMOUNT_WITH_POSITIVE_VOLUME_REQUIRED")
        raw = self.amount_cny / self.volume_shares
        if self.low <= raw <= self.high:
            return raw, None
        rounded = raw.quantize(STEP, rounding=ROUND_HALF_UP)
        if self.low <= rounded <= self.high:
            return rounded, {"source_date": self.source_date, "raw_vwap": str(raw),
                             "used_mode": str(rounded), "reason": "SOURCE_CENT_ROUNDING_BOUNDARY"}
        raise ValueError("AMOUNT_VOLUME_VWAP_OUTSIDE_OHLC")


def _triangle(bar, mode):
    if bar.low == bar.high:
        return {int(bar.low / STEP): Decimal(1)}
    low, high = bar.low, bar.high

    def cdf(x):
        if x <= low:
            return Decimal(0)
        if x >= high:
            return Decimal(1)
        if mode == low:
            return 1 - ((high - x) / (high - low)) ** 2
        if mode == high:
            return ((x - low) / (high - low)) ** 2
        if x <= mode:
            return (x - low) ** 2 / ((high - low) * (mode - low))
        return 1 - (high - x) ** 2 / ((high - low) * (high - mode))

    cells = {}
    for index in range(int(low / STEP), int(high / STEP) + 1):
        centre = Decimal(index) * STEP
        mass = cdf(centre + HALF) - cdf(centre - HALF)
        if mass > 0:
            cells[index] = mass
    total = sum(cells.values(), Decimal(0))
    if total <= 0:
        raise ValueError("EMPTY_DAILY_ALLOCATION")
    return {i: mass / total for i, mass in cells.items()}


def triangle(bar):
    with localcontext() as ctx:
        ctx.prec = PRECISION
        mode, _ = bar.mode()
        if mode is None:
            raise ValueError("NO_TRADES_NO_DAILY_TRIANGLE")
        return _triangle(bar, mode)


def prepare_eastmoney(raw, *, expected_code, cutoff, allow_valid_suffix=False):
    """Parse declared hand/CNY source units. Never infer missing turnover as zero.

    Strict by default. The case runner may explicitly initialize after the last
    invalid source row; the rejected prefix and all anomalies remain recorded.
    Future rows are excluded before numeric validation or reseed selection.
    """
    iso_day(cutoff)
    if type(raw) is not bytes or not 0 < len(raw) <= 2 * 1024 * 1024:
        raise ValueError("BOUNDED_SOURCE_BYTES_REQUIRED")
    value = json.loads(raw)
    data = value.get("data") if isinstance(value, dict) else None
    if not isinstance(value, dict) or value.get("rc") != 0 or not isinstance(data, dict) or data.get("code") != expected_code:
        raise ValueError("SUCCESSFUL_SAME_SECURITY_RESPONSE_REQUIRED")
    lines = data.get("klines")
    if not isinstance(lines, list) or not 0 < len(lines) <= 10000:
        raise ValueError("BOUNDED_NONEMPTY_HISTORY_REQUIRED")
    bars, issues = [], []
    previous = None
    last_invalid = -1
    eligible_count = 0
    with localcontext() as ctx:
        ctx.prec = PRECISION
        for source_index, line in enumerate(lines):
            if not isinstance(line, str):
                raise ValueError("ORIGINAL_SOURCE_ROW_STRING_REQUIRED")
            fields = line.split(",")
            label = iso_day(fields[0])
            if previous is not None and label <= previous:
                raise ValueError("UNIQUE_INCREASING_SOURCE_DATES_REQUIRED")
            previous = label
            if label > cutoff:
                continue
            eligible_count += 1
            try:
                if len(fields) != 11:
                    raise ValueError("EXACT_DAILY_FIELD_COUNT_REQUIRED")
                nums = {i: number(fields[i]) for i in (1, 2, 3, 4, 5, 6, 10)}
                bar = Bar(label, nums[1], nums[3], nums[4], nums[2],
                          nums[5] * 100, nums[6], nums[10])
                bar.mode()
                bars.append((source_index, bar))
            except ValueError as exc:
                issues.append({"source_row_index": source_index, "source_date": label,
                               "reason": str(exc), "original_row": line})
                last_invalid = source_index
    if issues and not allow_valid_suffix:
        raise ValueError("INVALID_SOURCE_HISTORY:" + issues[-1]["source_date"] + ":" + issues[-1]["reason"])
    kept = [bar for index, bar in bars if index > last_invalid]
    if not kept:
        raise ValueError("NO_CONTIGUOUS_VALID_SOURCE_SUFFIX")
    return kept, {
        "source_sha256": hashlib.sha256(raw).hexdigest(), "source_bytes": len(raw),
        "security_id": expected_code, "source_name": data.get("name"),
        "source_volume_unit": "HAND_AS_DOCUMENTED_BY_AKSHARE", "volume_conversion": "source_volume*100",
        "amount_unit": "CNY_AS_DOCUMENTED_BY_AKSHARE", "source_rows": len(lines),
        "source_rows_at_or_before_cutoff": eligible_count,
        "cutoff": cutoff, "used_rows": len(kept), "used_first_source_date": kept[0].source_date,
        "used_last_source_date": kept[-1].source_date,
        "invalid_source_rows": issues,
        "excluded_prefix_rows": eligible_count - len(kept),
        "reseed_after_last_invalid_explicitly_selected": bool(issues and allow_valid_suffix),
        "original_raw_preserved": True, "historical_PIT_authenticated": False,
        "official_calendar_authenticated": False, "corporate_action_coverage_authenticated": False,
    }


def estimate_snapshots(bars, requested_dates, *, security_id, state=None, export_state=False):
    if not isinstance(security_id, str) or not security_id.strip():
        raise ValueError("EXPLICIT_SECURITY_ID_REQUIRED")
    targets = {iso_day(d) for d in requested_dates}
    if not targets:
        raise ValueError("EXPLICIT_SNAPSHOT_DATES_REQUIRED")
    weights, scale, snapshots = {}, Decimal(1), {}
    seed_survival = Decimal(1)
    first_date = previous = None
    rounding, clipped, source_zero_turnover_trades = [], [], []
    prefix_digest = hashlib.sha256()
    saved_date = None
    state_found = False
    processed_rows = 0
    if state is not None:
        if state.get('schema') != 'chip-estimate.state.v1' or state.get('model_version') != VERSION or state.get('security_id') != security_id:
            raise ValueError('MODEL_STATE_IDENTITY_MISMATCH')
        saved_date = iso_day(state['last_date'])
        if saved_date > max(targets): raise ValueError('MODEL_STATE_FROM_FUTURE')
        weights = {int(i): Decimal(v) for i,v in state['weights'].items()}
        scale, seed_survival = Decimal(state['scale']), Decimal(state['seed_survival'])
        if not weights or len(weights)>1000000 or any(i<0 or i>1000000 or not v.is_finite() or v<=0 for i,v in weights.items()) or not scale.is_finite() or scale<=0:
            raise ValueError('MODEL_STATE_INVALID')
        first_date = state['first_date']
        rounding, clipped = list(state['rounding']), list(state['clipped'])
        source_zero_turnover_trades = list(state['source_zero_turnover_trades'])
        snapshots = {d:v for d,v in state['last_snapshots'].items() if d in targets}
    with localcontext() as ctx:
        ctx.prec = PRECISION
        for row_index, bar in enumerate(bars):
            if bar.source_date > max(targets):
                break
            if previous is not None and bar.source_date <= previous:
                raise ValueError("UNIQUE_INCREASING_BAR_DATES_REQUIRED")
            previous = bar.source_date
            processed_rows = row_index + 1
            mode, adjustment = bar.mode()
            if adjustment:
                rounding.append(adjustment)
            prefix_digest.update(json.dumps([bar.source_date, *[str(v) for v in (
                bar.open, bar.high, bar.low, bar.close, bar.volume_shares,
                bar.amount_cny, bar.turnover_pct)]], separators=(",", ":")).encode())
            if saved_date and bar.source_date <= saved_date:
                if bar.source_date == saved_date:
                    if prefix_digest.hexdigest()!=state['normalized_input_prefix_sha256'] or row_index+1!=state['rows_processed']:
                        raise ValueError('MODEL_STATE_PREFIX_CHANGED')
                    state_found=True
                continue
            if saved_date and not state_found: raise ValueError('MODEL_STATE_BOUNDARY_MISSING')
            a_unbounded = COEFFICIENT * bar.turnover_pct / 100
            a = min(Decimal(1), a_unbounded)
            if a_unbounded > 1:
                clipped.append({"source_date": bar.source_date, "effective_replacement_before_clip": str(a_unbounded)})
            if mode is not None and bar.turnover_pct == 0:
                source_zero_turnover_trades.append(bar.source_date)
            if not weights:
                if mode is not None:
                    weights = _triangle(bar, mode)
                    first_date = bar.source_date
            elif a == 1:
                weights, scale = _triangle(bar, mode), Decimal(1)
                seed_survival = Decimal(0)
            elif a > 0:
                # Lazy global scaling: identical recurrence, work proportional to
                # today's occupied price cells rather than all historical cells.
                scale *= 1 - a
                seed_survival *= 1 - a
                new_factor = a / scale
                for index, mass in _triangle(bar, mode).items():
                    weights[index] = weights.get(index, Decimal(0)) + new_factor * mass
            if bar.source_date not in targets:
                continue
            if not weights:
                raise ValueError("NO_OBSERVED_INITIAL_DISTRIBUTION")
            total = sum(weights.values(), Decimal(0))
            maximum = max(weights.values())
            peaks = sorted(i for i, mass in weights.items() if mass == maximum)
            profile = {i: mass / total for i, mass in sorted(weights.items())}
            snapshots[bar.source_date] = {
                "source_date": bar.source_date, "model_version": VERSION, "security_id": security_id,
                "seed_source_date": first_date, "history_rows_processed": row_index + 1,
                "normalized_input_prefix_sha256": prefix_digest.hexdigest(),
                "initial_seed_survival_upper_bound": str(seed_survival),
                "initial_distribution_observed": False,
                "close_cny": str(bar.close), "turnover_percent": str(bar.turnover_pct),
                "effective_replacement": str(a), "coefficient": "2",
                "peak_prices_cny": [price_text(i) for i in peaks],
                "peak_bin_share_percent": str(maximum / total * 100),
                "model_mean_cost_cny": str(sum((Decimal(i) * STEP * mass for i, mass in profile.items()), Decimal(0))),
                "profile": [{"price_cny": price_text(i), "share_percent": str(mass * 100)} for i, mass in profile.items()],
                "rounding_adjustments": list(rounding), "replacement_clips": list(clipped),
                "positive_volume_reported_zero_turnover_dates": list(source_zero_turnover_trades),
                "unchanged_threshold": None, "unchanged_classification": None,
                "historical_PIT_authenticated": False, "vendor_equivalence": "NOT_CALIBRATED",
            }
    if saved_date and not state_found: raise ValueError('MODEL_STATE_BOUNDARY_MISSING')
    missing = sorted(targets - snapshots.keys())
    if missing:
        raise ValueError("REQUESTED_SOURCE_DATES_MISSING:" + ",".join(missing))
    if export_state:
        if saved_date and not state_found: raise ValueError('MODEL_STATE_BOUNDARY_MISSING')
        saved = {'schema':'chip-estimate.state.v1','model_version':VERSION,'security_id':security_id,
            'last_date':previous,'first_date':first_date,'rows_processed':processed_rows,
            'normalized_input_prefix_sha256':prefix_digest.hexdigest(),
            'weights':{str(i):str(v) for i,v in weights.items()},'scale':str(scale),'seed_survival':str(seed_survival),
            'rounding':rounding,'clipped':clipped,'source_zero_turnover_trades':source_zero_turnover_trades,
            'last_snapshots':snapshots}
        return snapshots,saved
    return snapshots


def compare_snapshots(before, after, *, anchors=None):
    if before["source_date"] >= after["source_date"]:
        raise ValueError("ORDERED_COMPARISON_DATES_REQUIRED")
    if before.get("model_version") != VERSION or after.get("model_version") != VERSION:
        raise ValueError("SAME_MODEL_VERSION_REQUIRED")
    if not before.get("security_id") or before.get("security_id") != after.get("security_id"):
        raise ValueError("SAME_SECURITY_REQUIRED")
    if before.get("seed_source_date") != after.get("seed_source_date"):
        raise ValueError("SAME_INITIALIZATION_REQUIRED")
    # Generated old-tail shares can legitimately be far below 1e-20 after years
    # of decay. Keep the strict source-number bounds, but accept finite model
    # tails without clipping them to zero or changing the estimator recurrence.
    left = {number(r["price_cny"]): number(r["share_percent"], minimum_exponent=-100000) for r in before["profile"]}
    right = {number(r["price_cny"]): number(r["share_percent"], minimum_exponent=-100000) for r in after["profile"]}
    selected = before["peak_prices_cny"] if anchors is None else list(anchors)
    if not selected:
        raise ValueError("NONEMPTY_EXPLICIT_ANCHORS_REQUIRED")
    rows = []
    with localcontext() as ctx:
        ctx.prec = PRECISION
        for text in selected:
            price = number(text)
            if price != price.quantize(STEP) or price not in left:
                raise ValueError("ANCHOR_MUST_BE_AN_OBSERVED_PRICE_CELL")
            old = left[price]
            new = right.get(price, Decimal(0))
            rows.append({
                "anchor_price_cny": str(price), "previous_share_percent": str(old),
                "current_same_price_share_percent": str(new),
                "change_percentage_points": str(new - old),
                "same_price_occupancy_ratio_percent": str(new / old * 100) if old else None,
                "original_holder_retention": None,
            })
    return {
        "security_id": before["security_id"], "from_date": before["source_date"], "to_date": after["source_date"],
        "previous_peak_prices_cny": list(before["peak_prices_cny"]),
        "current_peak_prices_cny": list(after["peak_prices_cny"]),
        "anchors": rows,
        "anchor_selection": "ALL_PREVIOUS_MAXIMUM_CELLS" if anchors is None else "EXPLICIT_USER_PRICE_CELLS",
        "same_price_definition": "Current occupancy includes both surviving estimated old mass and newly allocated mass at that price; it is not identified-holder retention.",
        "unchanged_threshold": None, "unchanged_classification": None,
    }
