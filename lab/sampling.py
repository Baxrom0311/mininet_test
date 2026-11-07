"""Actual result sampling, preserving the historical one-second analytic grid."""

import math


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def sample_interval(run):
    """Read output's analytic marker, or the ns-3 scenario's configured interval.

    Older analytic runs ignored scenario.sample, so absent sample_s always means
    one second. Never reinterpret an archive just because its input requested a
    finer grid. ns-3 continues to use the scenario value, including old archives.
    """
    scenario = run.get("scenario") or {}
    engine = run.get("engine")
    if engine == "analytic":
        step = run.get("sample_s", 1)
    elif engine == "ns3":
        step = scenario.get("sample", 1)
    else:
        raise ValueError("sampling interval requires an analytic/ns3 result")
    duration = run.get("duration", scenario.get("duration"))
    if not _finite(step) or not .001 <= step <= 3600:
        raise ValueError("unsupported sampling interval")
    if not _finite(duration) or duration <= 0:
        raise ValueError("invalid sampling duration")
    if duration / step > 100000:
        raise ValueError("unsupported sampling interval: at most 100000 intervals")
    return float(step)
