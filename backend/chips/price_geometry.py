"""Log-price calculations with deterministic 28 significant decimal digits."""
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from fractions import Fraction

GEOMETRY_VERSION = 'log-price.v1'


def _decimal(value):
    if isinstance(value, Fraction):
        return Decimal(value.numerator) / Decimal(value.denominator)
    return Decimal(value)


def geometric_level(low, high, ratio):
    with localcontext() as ctx:
        ctx.prec = 28
        ctx.rounding = ROUND_HALF_EVEN
        low, high, ratio = map(_decimal, (low, high, ratio))
        if not low.is_finite() or not high.is_finite() or low <= 0 or high <= 0 or not ratio.is_finite():
            raise ValueError('INVALID_PRICE_GEOMETRY')
        if ratio == 1:
            return high
        if ratio in (Decimal('0.5'), Decimal('0.25'), Decimal('0.125')):
            factor = (high / low).sqrt()
            if ratio < Decimal('0.5'):
                factor = factor.sqrt()
            if ratio < Decimal('0.25'):
                factor = factor.sqrt()
            return low * factor
        return low * ((high / low).ln() * ratio).exp()


def reflected_price(origin, middle):
    with localcontext() as ctx:
        ctx.prec = 28
        ctx.rounding = ROUND_HALF_EVEN
        origin, middle = map(_decimal, (origin, middle))
        if not origin.is_finite() or not middle.is_finite() or origin <= 0 or middle <= 0:
            raise ValueError('INVALID_PRICE_GEOMETRY')
        return middle * middle / origin
