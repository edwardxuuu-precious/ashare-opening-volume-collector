"""Unchanged data types used by the frozen V3 evaluator."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum


class Status(str, Enum):
    PASS = 'PASS'
    FAIL = 'FAIL'
    UNKNOWN = 'UNKNOWN'


@dataclass(frozen=True)
class DailyBar:
    day: date
    open: Decimal
    close: Decimal
    high: Decimal
    low: Decimal
    volume: Decimal
    complete: bool


@dataclass(frozen=True)
class DailySeries:
    security_id: str
    bars: tuple[DailyBar, ...]
    source_sha256: str
    raw_sha256: str
    provider: str
    volume_unit: str
    ingested_at: str
    historical_known_at_verified: bool
    session_coverage_verified: bool = False


@dataclass(frozen=True)
class FeatureResult:
    status: Status
    metrics: tuple
    windows: tuple
    reasons: tuple[str, ...] = ()
