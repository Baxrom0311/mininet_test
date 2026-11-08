"""Bounded display envelopes; simulation samples and statistics stay untouched."""

from dataclasses import dataclass
from itertools import chain, repeat

from lab.analysis_windows import finite

MAX_SERIES = 128
MAX_SERIES_POINTS = 1200
MAX_CHART_POINTS = 12000


@dataclass
class Envelope:
    # Each retained point is (source index, x, y, contiguous segment).
    points: list
    observed: int
    valid: int
    segments: int
    xmin: object
    xmax: object
    ymin: object
    ymax: object


def _merge(left, right):
    if left is None:
        return right
    if right is None:
        return left
    return (left[0], right[1], left[2] if left[2][2] <= right[2][2] else right[2],
            left[3] if left[3][2] >= right[3][2] else right[3])


def _coarsen(buckets):
    return [_merge(buckets[i], buckets[i+1] if i+1 < len(buckets) else None)
            for i in range(0, len(buckets), 2)]


def envelope(points, limit=MAX_SERIES_POINTS):
    """One pass: retain bin first/last/min/max with explicit segment identities."""
    if type(limit) is not int or not 4 <= limit <= MAX_SERIES_POINTS:
        raise ValueError("display point limit: integer 4..1200")
    raw, buckets, width = [], None, 1
    capacity = limit//4
    observed, valid, segment, previous_valid = 0, 0, -1, False
    xmin = xmax = ymin = ymax = None
    for index, (x, y) in enumerate(points):
        observed += 1
        point = None
        if finite(x) and finite(y):
            if not previous_valid:
                segment += 1
            previous_valid = True
            point = (index, x, y, segment)
            valid += 1
            xmin = x if xmin is None else min(xmin, x)
            xmax = x if xmax is None else max(xmax, x)
            ymin = y if ymin is None else min(ymin, y)
            ymax = y if ymax is None else max(ymax, y)
        else:
            previous_valid = False
            continue
        if buckets is None:
            raw.append(point)
            if len(raw) > limit:
                buckets = [(p, p, p, p) for p in raw]
                raw = None
                while len(buckets) > capacity:
                    buckets = _coarsen(buckets)
                    width *= 2
            continue
        key = (valid-1)//width
        if key == len(buckets):
            buckets.append(None)
        if len(buckets) > capacity:
            buckets = _coarsen(buckets)
            width *= 2
            key = (valid-1)//width
        buckets[key] = _merge(buckets[key], (point, point, point, point))
    if buckets is None:
        selected = raw
    else:
        selected = []
        for bucket in buckets:
            if bucket is not None:
                selected.extend(sorted({point[0]: point for point in bucket}.values()))
    return Envelope(selected, observed, valid, segment+1, xmin, xmax, ymin, ymax)


def _nonempty(points):
    iterator = iter(points)
    skipped = 0
    for x, y in iterator:
        if finite(x) and finite(y):
            # Replay leading invalid slots without retaining their original values.
            return chain(repeat((None, None), skipped), ((x, y),), iterator)
        skipped += 1
    return None


def sample_series(series):
    """Select the first data-bearing lines and share a fixed chart point budget."""
    selected, omitted = [], 0
    for name, points in series:
        points = _nonempty(points)
        if points is None:
            continue
        if len(selected) < MAX_SERIES:
            selected.append((name, points))
        else:
            omitted += 1
    limit = min(MAX_SERIES_POINTS, MAX_CHART_POINTS//len(selected)) if selected else MAX_SERIES_POINTS
    return [(name, envelope(points, limit)) for name, points in selected], omitted
