"""Independent seeded replications; Student t intervals over per-run summaries."""

import copy
import math
import statistics

from lab.analysis_windows import finite
from lab.experiment_inputs import completed_inputs, validate_cohort
from lab.reproducibility import comparison_key, digest

# Two-sided 95% Student t quantiles, df=1..24. Replications are bounded at 25.
T95 = (12.706204736, 4.302652730, 3.182446305, 2.776445105, 2.570581836,
       2.446911851, 2.364624252, 2.306004135, 2.262157163, 2.228138852,
       2.200985160, 2.178812830, 2.160368656, 2.144786688, 2.131449546,
       2.119905299, 2.109815578, 2.100922040, 2.093024054, 2.085963447,
       2.079613845, 2.073873068, 2.068657610, 2.063898562)
METRICS = ("avg_thr_bps", "avg_delay_ms", "avg_jitter_ms", "loss_pct", "avg_rtt_ms")


def variants(project, scenario_name, repetitions, seed):
    if type(repetitions) is not int or not 2 <= repetitions <= 25:
        raise ValueError("repetitions: integer 2..25")
    if type(seed) is not int or not 1 <= seed <= 2147483647 - repetitions + 1:
        raise ValueError("seed range must fit 1..2147483647")
    out = []
    for i in range(repetitions):
        p = copy.deepcopy(project)
        sc = p["scenarios"][scenario_name]
        # ns-3 recommends a fixed seed with distinct run substreams.
        sc["seed"] = seed
        sc["rng_run"] = i + 1
        if sc.get("failures"):
            sc["failures"]["seed"] = seed + i
        out.append(p)
    return out


def interval(values, eligible=True):
    if len(values) > 25:
        raise ValueError("at most 25 independent samples")
    if any(not finite(v) for v in values):
        raise ValueError("finite numeric samples required")
    n = len(values)
    mean = statistics.mean(values) if n else None
    sd = statistics.stdev(values) if n >= 2 else None
    half = T95[n - 2] * sd / math.sqrt(n) if n >= 2 and eligible else None
    computed = [v for v in (mean, sd, half) if v is not None]
    if half is not None:
        computed.extend((mean - half, mean + half))
    if any(not math.isfinite(v) for v in computed):
        raise ValueError("sample magnitudes exceed finite statistics")
    return {"n": n, "mean": mean, "sample_stddev": sd,
            "ci95": [mean - half, mean + half] if half is not None else None}


def summarize(runs, *, start_s=None, end_s=None):
    expected, experiment_id = validate_cohort(runs)
    done = [r for r in runs if r.get("status") == "done"]
    keys, runtime_keys = set(), set()
    for r in done:
        record, sc = completed_inputs(r)
        key = comparison_key(record["project"], sc, r.get("engine"))
        if (key != record.get("comparison_key") or sc.get("seed") != r.get("seed")
                or sc.get("rng_run") != r.get("rng_run")):
            raise ValueError("run input/seed mismatch")
        if sc.get("failures") and sc["failures"].get("seed") != r["seed"] + r["replication_index"]:
            raise ValueError("failure seed does not match replication")
        keys.add(key)
        # Different code, package versions or engine switches cannot share an interval.
        runtime_keys.add(digest(record["runtime"]))
    if len(keys) > 1 or len(runtime_keys) > 1:
        raise ValueError("replications must use the same topology, scenario, engine and runtime")
    complete = len(done) == expected and len(runs) == expected
    windowed = start_s is not None or end_s is not None
    analyses, window = {}, None
    if windowed:
        from lab.analysis_windows import analyze, specification
        window = specification(runs[0].get("duration", (runs[0].get("scenario") or {}).get("duration")), start_s, end_s)
        analyses = {r["run_id"]: analyze(r, start_s, end_s) for r in done}
    rows = []
    flow_ids = sorted({fid for r in done for fid, f in r.get("flows", {}).items() if not f.get("hidden")})
    for fid in flow_ids:
        selected_windows = set()
        if windowed:
            for analysis in analyses.values():
                flow = analysis["flows"].get(fid)
                if flow:
                    selected_windows.add((flow["selected_start_s"], flow["selected_end_s"], flow["expected_bins"]))
        for metric in METRICS:
            coverage = []
            if windowed:
                from lab.analysis_windows import METRICS as fields
                if not any(metric in r.get("flows", {}).get(fid, {}).get("summary", {}) or
                           any(fields[metric] in p for p in r.get("flows", {}).get(fid, {}).get("series", [])) for r in done):
                    continue
                coverage = [analyses[r["run_id"]]["flows"].get(fid, {}).get("metrics", {}).get(metric, {}) for r in done]
                values = [c.get("value") for c in coverage]
            else:
                values = [r.get("flows", {}).get(fid, {}).get("summary", {}).get(metric) for r in done]
            valid = [v for v in values if finite(v)]
            if not valid and not windowed:
                continue
            eligible = complete and len(valid) == expected
            extra = {}
            if windowed:
                extra["incomplete"] = sum(not c.get("complete", False) for c in coverage)
                extra["window_mismatch"] = len(selected_windows) > 1
                eligible = eligible and extra["incomplete"] == 0 and not extra["window_mismatch"]
            rows.append({"flow": fid, "metric": metric, "missing": expected - len(valid), **extra,
                         **interval(valid, eligible=eligible)})
    statuses = {}
    for r in runs:
        status = r.get("status", "unknown")
        statuses[status] = statuses.get(status, 0) + 1
    result = {"experiment_id": experiment_id, "expected": expected, "observed": len(runs),
            "complete": complete, "statuses": statuses, "confidence": .95, "method": "Student t, per-run summary",
            "metrics": rows,
            "notes": ["Intervals require independent replications and an approximately normal sample mean.",
                      "Identical deterministic results give zero width; this does not measure model accuracy.",
                      "Incomplete, failed or missing-metric replications suppress the corresponding interval."]}
    if windowed:
        result.update(window=window, run_windows=list(analyses.values()),
                      method="Student t, per-run duration-weighted full-bin means")
        result["notes"] += ["Only full bins within each flow's active time are selected; boundary bins are trimmed, never split.",
                            "Delay, jitter, RTT and loss are time-weighted bin means, not packet-weighted estimates.",
                            "Missing/invalid bins or differing per-flow windows suppress the interval; available means remain descriptive."]
    return result
