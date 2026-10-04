"""Exact (Fraction) quantiles and Tukey outlier fences. No floats anywhere."""

from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction
from math import floor

from .errors import ValidationError

Number = int | Fraction


def quantile(sorted_values: Sequence[Number], p: Fraction) -> Fraction:
    """Linear-interpolation quantile ("type 7") over an already-sorted sequence."""
    n = len(sorted_values)
    if n == 0:
        raise ValidationError("quantile of an empty sample")
    if not (0 <= p <= 1):
        raise ValidationError("quantile p must be within [0, 1]")
    pos = (n - 1) * p
    lo = floor(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    low = Fraction(sorted_values[lo])
    high = Fraction(sorted_values[hi])
    return low + (high - low) * frac


def tukey_bounds(sorted_values: Sequence[Number]) -> tuple[Fraction, Fraction]:
    """Return the (low, high) fences: Q1 - 1.5*IQR and Q3 + 1.5*IQR."""
    q1 = quantile(sorted_values, Fraction(1, 4))
    q3 = quantile(sorted_values, Fraction(3, 4))
    iqr = q3 - q1
    margin = Fraction(3, 2) * iqr
    return q1 - margin, q3 + margin
