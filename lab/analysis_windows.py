"""Duration-weighted flow statistics over complete simulation sample intervals."""

import math

from lab.sampling import sample_interval

METRICS = {"avg_thr_bps": "thr_bps", "avg_delay_ms": "delay_ms", "avg_jitter_ms": "jitter_ms",
           "loss_pct": "loss_pct", "avg_rtt_ms": "rtt_ms"}
EPS = 1e-8


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def bounds(duration, start_s=None, end_s=None):
    start, end = (0 if start_s is None else start_s), (duration if end_s is None else end_s)
    if not all(finite(v) for v in (duration, start, end)) or not 0 <= start < end <= duration:
        raise ValueError("analysis window: finite 0 <= start_s < end_s <= duration")
    return float(start), float(end)


def specification(duration, start_s=None, end_s=None):
    start, end = bounds(duration, start_s, end_s)
    return {"schema": 1, "start_s": start, "end_s": end,
            "selection": "complete bins within flow active time", "weighting": "duration"}


def _grid(lo, hi, duration, step):
    count = math.ceil(duration / step - EPS)
    first = max(0, math.ceil(lo / step - EPS))
    last = count if abs(hi - duration) < EPS else math.floor(hi / step + EPS)
    if hi <= lo or first >= last:
        return first, first, None, None
    return first, last, first * step, duration if last == count else last * step


def _interval(point, step, duration):
    if not isinstance(point, dict):
        raise ValueError("sample point must be an object")
    if "t_start" in point or "t_end" in point:
        start, end = point.get("t_start"), point.get("t_end")
        if not finite(start) or not finite(end) or not 0 <= start < end <= duration + EPS:
            raise ValueError("invalid explicit sample interval")
        return float(start), float(end), False
    # Older ns-3 output rounded t to milliseconds. Infer only an unambiguous grid endpoint.
    t = point.get("t")
    if not finite(t) or not 0 <= t <= duration + .000500001:
        raise ValueError("invalid sample timestamp")
    candidates = set()
    count = math.ceil(duration / step - EPS)
    for k in {math.floor(t / step), math.ceil(t / step), count}:
        if k < 1 or k > count:
            continue
        a, b = (k - 1) * step, duration if k == count else k * step
        if a < b and abs(b - t) <= .000500001:
            candidates.add((a, b))
    if len(candidates) != 1:
        raise ValueError("legacy timestamp does not identify one sample interval")
    return *next(iter(candidates)), True


def analyze(run, start_s=None, end_s=None):
    if run.get("status") != "done" or run.get("engine") not in ("analytic", "ns3"):
        raise ValueError("window analysis requires a completed analytic/ns3 run")
    scenario = run.get("scenario") or {}
    duration = run.get("duration", scenario.get("duration"))
    spec = specification(duration, start_s, end_s)
    start, end = spec["start_s"], spec["end_s"]
    step = sample_interval(run)
    flows = {}
    for fid, flow in (run.get("flows") or {}).items():
        if flow.get("hidden"):
            continue
        flow_start, flow_stop = flow.get("start", 0), flow.get("stop", duration)
        if not finite(flow_start) or not finite(flow_stop) or not 0 <= flow_start <= flow_stop:
            raise ValueError("invalid flow active time")
        lo, hi = max(start, min(flow_start, end)), min(end, max(flow_stop, start))
        first, last, selected_start, selected_end = _grid(lo, hi, duration, step)
        expected_s = selected_end - selected_start if first < last else 0
        selected, inferred, error = {}, False, None
        try:
            for point in flow.get("series") or []:
                a, b, legacy = _interval(point, step, duration)
                inferred |= legacy
                k = round(a / step)
                bin_end = duration if k == math.ceil(duration / step - EPS) - 1 else (k + 1) * step
                if abs(a - k * step) > EPS or abs(b - bin_end) > EPS:
                    raise ValueError("sample interval is not on the engine grid")
                if not first <= k < last:
                    continue
                if k in selected:
                    raise ValueError("duplicate sample interval")
                selected[k] = (b - a, point)
        except (ValueError, TypeError) as exc:
            error = str(exc)
            selected = {}
        metrics = {}
        for metric, field in METRICS.items():
            values = [(dt, point[field]) for dt, point in selected.values() if finite(point.get(field))]
            covered_s = math.fsum(dt for dt, _ in values)
            value = math.fsum(v * (dt / covered_s) for dt, v in values) if covered_s else None
            if value is not None and not math.isfinite(value):
                value = None
            metrics[metric] = {"value": value, "covered_s": covered_s, "samples": len(values),
                               "complete": bool(expected_s > 0 and len(values) == last - first and value is not None
                                                and math.isclose(covered_s, expected_s, rel_tol=1e-8, abs_tol=EPS))}
        flows[fid] = {"active_start_s": lo, "active_end_s": hi,
                      "selected_start_s": selected_start, "selected_end_s": selected_end,
                      "expected_s": expected_s, "expected_bins": last - first, "observed_bins": len(selected),
                      "trimmed_s": max(0, hi - lo - expected_s),
                      "legacy_intervals": inferred, "error": error, "metrics": metrics}
    return {"run_id": run.get("run_id"), "engine": run["engine"], "sample_s": step, "duration": duration,
            "window": spec, "flows": flows}
