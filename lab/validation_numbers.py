"""Numeric input checks shared by scenario and application validators.

These helpers do not coerce the user's document in place. Integer spellings must
also be consumable by int(), which the simulation adapters use after validation.
"""

import math

MAX_UINT64 = (1 << 64) - 1
# float(MAX_UINT64) rounds UP to 2**64, outside ns-3's DataRate range.
MAX_RATE_BPS = math.nextafter(float(1 << 64), 0)
MIN_TIMER_S = 1e-9
MAX_TIMER_S = 9223372036  # Whole seconds within signed 64-bit nanoseconds.


def number(value, name, minimum=None, maximum=None):
    """Return a finite float, rejecting booleans and numeric overflow."""
    try:
        if isinstance(value, bool):
            raise ValueError
        result = float(value)
        if not math.isfinite(result):
            raise ValueError
        if minimum is not None and result < minimum:
            raise ValueError
        if maximum is not None and result > maximum:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        bounds = (f" >= {minimum}" if minimum is not None else "")
        bounds += (f" <= {maximum}" if maximum is not None else "")
        raise ValueError(f"{name}: finite number{bounds}") from None
    return result


def integer(value, name, minimum=None, maximum=None):
    """Return an exact integer; never silently truncate fractional JSON values."""
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError
        result = int(value)
        if isinstance(value, float) and value != result:
            raise ValueError
        if minimum is not None and result < minimum:
            raise ValueError
        if maximum is not None and result > maximum:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        bounds = (f" >= {minimum}" if minimum is not None else "")
        bounds += (f" <= {maximum}" if maximum is not None else "")
        raise ValueError(f"{name}: integer{bounds}") from None
    return result


def packet_rate(rate_bps, size=1000):
    """Check the paced source's serialization timer fits nanosecond resolution."""
    size = integer(size, "size", minimum=1, maximum=65507)
    return number(rate_bps, "rate (bit/s; packet timer >= 1 ns)",
                  minimum=1, maximum=size * 8 * 1e9)
