from decimal import Decimal
from fractions import Fraction

import pytest

from property_estimates.errors import ValidationError
from property_estimates.money import minor_to_str, parse_decimal, round_for_proposal, to_minor
from property_estimates.ratelimit import SlidingWindowLimiter
from property_estimates.stats import quantile, tukey_bounds


class TestMoney:
    @pytest.mark.parametrize("ok", ["0", "1", "412500.00", "-3.5", 7, Decimal("1.25")])
    def test_accepts(self, ok):
        assert isinstance(parse_decimal(ok), Decimal)

    @pytest.mark.parametrize(
        "bad", [1.5, True, "1e3", "NaN", "Infinity", "", " ", "1,000", None, "1."]
    )
    def test_rejects(self, bad):
        with pytest.raises(ValidationError):
            parse_decimal(bad)

    def test_float_has_specific_code(self):
        with pytest.raises(ValidationError) as ei:
            parse_decimal(0.1)
        assert ei.value.code == "float_money"

    def test_to_minor_is_half_even(self):
        assert to_minor(Decimal("0.005")) == 0  # 0.5 minor -> 0
        assert to_minor(Decimal("0.015")) == 2  # 1.5 -> 2
        assert to_minor(Decimal("0.025")) == 2  # 2.5 -> 2
        assert to_minor(Fraction(1, 2) / 100) == 0
        assert to_minor(232000) == 23_200_000

    def test_minor_to_str(self):
        assert minor_to_str(41_250_000) == "412500.00"
        assert minor_to_str(5) == "0.05"
        assert minor_to_str(-105) == "-1.05"
        with pytest.raises(ValidationError):
            minor_to_str(1.5)  # type: ignore[arg-type]

    def test_round_for_proposal(self):
        assert round_for_proposal(41_237_500) == 41_200_000  # 412,375.00 -> 412,000.00
        assert round_for_proposal(41_250_000) == 41_200_000  # 412.5 -> 412 (half-even)
        assert round_for_proposal(41_350_000) == 41_400_000  # 413.5 -> 414
        assert round_for_proposal(5_549_900) == 5_550_000  # below 100k -> nearest 100


class TestStats:
    def test_quantile_known_values(self):
        v = list(range(10, 101, 10))  # 10..100
        assert quantile(v, Fraction(1, 2)) == 55
        assert quantile(v, Fraction(1, 4)) == Fraction(325, 10)
        assert quantile(v, Fraction(0)) == 10 and quantile(v, Fraction(1)) == 100
        assert quantile([7], Fraction(9, 10)) == 7

    def test_quantile_errors(self):
        with pytest.raises(ValidationError):
            quantile([], Fraction(1, 2))
        with pytest.raises(ValidationError):
            quantile([1], Fraction(3, 2))

    def test_tukey(self):
        lo, hi = tukey_bounds(list(range(10, 101, 10)))
        assert lo < 10 and hi > 100


class TestLimiter:
    def make(self, n, w):
        t = [0.0]
        sleeps = []

        def sleep(s):
            sleeps.append(s)
            t[0] += s

        return SlidingWindowLimiter(n, w, clock=lambda: t[0], sleep=sleep), t, sleeps

    def test_under_limit_does_not_wait(self):
        lim, _, sleeps = self.make(4, 10.0)
        for _ in range(4):
            assert lim.acquire() == 0
        assert sleeps == []

    def test_fifth_call_waits_for_window(self):
        lim, t, sleeps = self.make(4, 10.0)
        for _ in range(4):
            lim.acquire()
        waited = lim.acquire()
        assert waited == pytest.approx(10.0)
        assert t[0] == pytest.approx(10.0)

    def test_never_exceeds_rate_over_many_calls(self):
        lim, t, _ = self.make(4, 10.0)
        stamps = []
        for _ in range(40):
            lim.acquire()
            stamps.append(t[0])
        for i in range(len(stamps) - 4):
            assert stamps[i + 4] - stamps[i] >= 10.0 - 1e-9  # <=4 calls in any 10 s

    def test_slots_free_as_time_passes(self):
        lim, t, sleeps = self.make(2, 1.0)
        lim.acquire()
        lim.acquire()
        t[0] += 5
        assert lim.acquire() == 0 and sleeps == []

    @pytest.mark.parametrize("n,w", [(0, 1), (1, 0), (-1, 1)])
    def test_invalid(self, n, w):
        with pytest.raises(ValidationError):
            self.make(n, w)
