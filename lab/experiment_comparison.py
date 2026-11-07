"""Paired experiment effects: candidate minus baseline, with explicit matching."""

import json
import statistics

from lab.analysis_windows import finite
from lab.experiments import interval, summarize
from lab.reproducibility import comparison_inputs, digest
from lab.sampling import sample_interval


def _scenario(run):
    record = run["reproducibility"]
    return record["project"]["scenarios"][record["scenario_name"]]


def _conditions(run):
    record, sc = run["reproducibility"], _scenario(run)
    runtime = record.get("runtime")
    if not isinstance(runtime, dict) or not runtime.get("lab_python_sha256"):
        raise ValueError("paired comparison requires a recorded runtime/source identity")
    if "scenario" in run and digest(run["scenario"]) != digest(sc):
        raise ValueError("scenario metadata does not match recorded inputs")
    if run.get("duration", sc["duration"]) != sc["duration"]:
        raise ValueError("duration does not match recorded inputs")
    step = sample_interval({**run, "scenario": sc, "duration": sc["duration"]})
    if run["engine"] == "analytic" and "sample_s" in run:
        requested = sc.get("sample", 1)
        if type(requested) not in (int, float) or step != requested:
            raise ValueError("analytic sample_s does not match recorded inputs")
    return {"engine": run["engine"], "project": record["project"].get("name"),
            "ip_version": sc.get("ip_version", 4), "duration": float(sc["duration"]),
            "sample_s": step,
            "runtime": digest(runtime)}


def _flow_identity(run, flow):
    start, stop = flow.get("start", 0), flow.get("stop", _scenario(run)["duration"])
    if not finite(start) or not finite(stop) or not 0 <= start <= stop:
        raise ValueError("invalid flow active time")
    return digest({"type": flow.get("type"), "src": flow.get("src"), "dst": flow.get("dst"),
                   "start": float(start), "stop": float(stop)})


def _preview(value):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False)
    return value if len(raw) <= 400 else {"preview": raw[:200], "sha256": digest(value), "truncated": True}


def design_diff(before, after, limit=100):
    """Bounded JSON pointer diff; full inputs remain available in exported runs."""
    changes = []
    def emit(path, operation, left=None, right=None):
        item = {"path": path, "operation": operation}
        if operation != "add":
            item["before"] = _preview(left)
        if operation != "remove":
            item["after"] = _preview(right)
        changes.append(item)
    def walk(left, right, path):
        if len(changes) > limit:
            return
        if type(left) is type(right) and left == right:
            return
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(left.keys() | right.keys()):
                if len(changes) > limit:
                    break
                pointer = path + "/" + key.replace("~", "~0").replace("/", "~1")
                if key not in left:
                    emit(pointer, "add", right=right[key])
                elif key not in right:
                    emit(pointer, "remove", left=left[key])
                else:
                    walk(left[key], right[key], pointer)
        elif isinstance(left, list) and isinstance(right, list):
            for index in range(max(len(left), len(right))):
                if len(changes) > limit:
                    break
                pointer = path + f"/{index}"
                if index >= len(left):
                    emit(pointer, "add", right=right[index])
                elif index >= len(right):
                    emit(pointer, "remove", left=left[index])
                else:
                    walk(left[index], right[index], pointer)
        else:
            emit(path or "/", "replace", left, right)
    walk(before, after, "")
    return {"available": True, "changes": changes[:limit], "truncated": len(changes) > limit}


def _metric(run, analysis, fid, metric):
    if analysis is None:
        value = run.get("flows", {}).get(fid, {}).get("summary", {}).get(metric)
        return value, finite(value), None
    flow = analysis["flows"].get(fid, {})
    data = flow.get("metrics", {}).get(metric, {})
    window = (flow.get("selected_start_s"), flow.get("selected_end_s"), flow.get("expected_bins"))
    return data.get("value"), data.get("complete", False), window


class _ComparisonInput:
    """Read-only request-owned records; never retained on a manager or shared between requests."""

    def __init__(self, runs, *, start_s=None, end_s=None):
        self.runs = runs
        self.summary = summarize(runs, start_s=start_s, end_s=end_s)
        self.windows = {a["run_id"]: a for a in self.summary.get("run_windows", [])}
        self._memo = {}

    def _cached(self, key, compute):
        if key not in self._memo:
            try:
                self._memo[key] = (compute(), None)
            except (ValueError, KeyError, TypeError) as exc:
                # Cache the failure without retaining a traceback and its record/series frames.
                self._memo[key] = (None, (type(exc), exc.args))
        value, error = self._memo[key]
        if error:
            cls, args = error
            raise cls(*args)
        return value

    def conditions(self):
        return self._cached("conditions", lambda: {digest(_conditions(r)) for r in self.runs if r["status"] == "done"})

    def identities(self):
        def build():
            identities = {}
            for run in self.runs:
                if run["status"] != "done":
                    continue
                for fid, flow in run.get("flows", {}).items():
                    if flow.get("hidden"):
                        continue
                    identity = _flow_identity(run, flow)
                    if fid in identities and identities[fid] != identity:
                        raise ValueError(f"flow {fid}: type, endpoints and active time must match")
                    identities[fid] = identity
            return identities
        return self._cached("identities", build)

    def design(self):
        def build():
            run = next((r for r in self.runs if r["status"] == "done"), None)
            return comparison_inputs(run["reproducibility"]["project"], _scenario(run), run["engine"]) if run else None
        return self._cached("design", build)


def compare(baseline, candidate, *, start_s=None, end_s=None):
    return _compare_prepared(_ComparisonInput(baseline, start_s=start_s, end_s=end_s),
                             _ComparisonInput(candidate, start_s=start_s, end_s=end_s))


def _compare_prepared(baseline_input, candidate_input):
    baseline, candidate = baseline_input.runs, candidate_input.runs
    left, right = baseline_input.summary, candidate_input.summary
    if left["experiment_id"] == right["experiment_id"]:
        raise ValueError("choose two distinct experiments")
    if left["expected"] != right["expected"]:
        raise ValueError("paired experiments require equal replication counts")
    runs = baseline + candidate
    if len({r["run_id"] for r in runs}) != len(runs):
        raise ValueError("experiments must not reuse run identities")
    if len({r["seed"] for r in runs}) != 1:
        raise ValueError("paired experiments require the same fixed seed")
    if len({r["engine"] for r in runs}) != 1 or runs[0]["engine"] not in ("analytic", "ns3"):
        raise ValueError("paired experiments require the same analytic/ns3 engine")
    if len(baseline_input.conditions() | candidate_input.conditions()) > 1:
        raise ValueError("engine, project, IP family, duration, sampling and runtime must match")
    identities = {}
    for prepared in (baseline_input, candidate_input):
        for fid, identity in prepared.identities().items():
            if fid in identities and identities[fid] != identity:
                raise ValueError(f"flow {fid}: type, endpoints and active time must match")
            identities[fid] = identity
    lmap = {(r["seed"], r["rng_run"]): r for r in baseline}
    rmap = {(r["seed"], r["rng_run"]): r for r in candidate}
    expected = left["expected"]
    if len(lmap) == len(rmap) == expected and lmap.keys() != rmap.keys():
        raise ValueError("paired experiments require matching seed/substream schedules")
    paired = sorted(lmap.keys() & rmap.keys())
    complete = left["complete"] and right["complete"] and lmap.keys() == rmap.keys()
    windows = (baseline_input.windows, candidate_input.windows)
    overview, done_pairs = [], []
    for seed, rng_run in paired:
        a, b = lmap[seed, rng_run], rmap[seed, rng_run]
        overview.append({"seed": seed, "rng_run": rng_run, "baseline_run_id": a["run_id"], "candidate_run_id": b["run_id"],
                         "baseline_status": a["status"], "candidate_status": b["status"]})
        if a["status"] == b["status"] == "done":
            af, bf = _scenario(a).get("failures"), _scenario(b).get("failures")
            if af and bf and af.get("seed") != bf.get("seed"):
                raise ValueError("paired failure generator seeds must match")
            done_pairs.append((a, b))
    lrows = {(r["flow"], r["metric"]): r for r in left["metrics"]}
    rrows = {(r["flow"], r["metric"]): r for r in right["metrics"]}
    rows, samples = [], []
    for fid, metric in sorted(lrows.keys() | rrows.keys()):
        group = []
        for a, b in done_pairs:
            av, ac, aw = _metric(a, windows[0].get(a["run_id"]), fid, metric)
            bv, bc, bw = _metric(b, windows[1].get(b["run_id"]), fid, metric)
            delta = bv - av if finite(av) and finite(bv) else None
            if not finite(delta):
                delta = None
            reason = "missing/nonfinite metric" if delta is None else "incomplete coverage" if not (ac and bc) else "window mismatch" if aw != bw else None
            sample = {"flow": fid, "metric": metric, "seed": a["seed"], "rng_run": a["rng_run"],
                      "baseline_run_id": a["run_id"], "candidate_run_id": b["run_id"],
                      "baseline": av if finite(av) else None, "candidate": bv if finite(bv) else None,
                      "delta": delta, "complete": reason is None, "reason": reason}
            samples.append(sample)
            group.append(sample)
        valid = [s for s in group if s["delta"] is not None]
        mismatch = any(s["reason"] == "window mismatch" for s in group) or any(
            table.get((fid, metric), {}).get("window_mismatch", False) for table in (lrows, rrows))
        eligible = complete and len(valid) == expected and all(s["complete"] for s in group) and not mismatch
        result = interval([s["delta"] for s in valid], eligible)
        amean = statistics.mean(s["baseline"] for s in valid) if valid else None
        bmean = statistics.mean(s["candidate"] for s in valid) if valid else None
        relative = result["mean"] / amean * 100 if amean else None
        ci = result["ci95"]
        rows.append({"flow": fid, "metric": metric, "pairs": result["n"], "missing_pairs": expected - len(valid),
                     "incomplete_pairs": sum(not s["complete"] for s in group), "window_mismatch": mismatch,
                     "baseline_mean": amean, "candidate_mean": bmean, "delta_mean": result["mean"],
                     "delta_stddev": result["sample_stddev"], "ci95": ci,
                     "relative_change_pct": relative if finite(relative) else None,
                     "ci_excludes_zero": (ci[0] > 0 or ci[1] < 0) if ci else None,
                     "delta_unit": "bps" if metric == "avg_thr_bps" else "percentage_points" if metric == "loss_pct" else "ms",
                     "preferred_direction": "higher" if metric == "avg_thr_bps" else "lower"})
    a, b = baseline_input.design(), candidate_input.design()
    changes = {"available": False, "changes": [], "truncated": False}
    if a is not None and b is not None:
        changes = design_diff(a, b)
    return {"schema": 1, "method": "paired Student t, candidate minus baseline", "confidence": .95,
            "baseline": left, "candidate": right, "expected_pairs": expected, "observed_pairs": len(paired),
            "completed_pairs": len(done_pairs), "complete": complete, "window": left.get("window"),
            "pairs": overview, "unmatched_baseline": [lmap[k]["run_id"] for k in sorted(lmap.keys() - rmap.keys())],
            "unmatched_candidate": [rmap[k]["run_id"] for k in sorted(rmap.keys() - lmap.keys())],
            "metrics": rows, "samples": samples, "design_diff": changes,
            "notes": ["Pairs match explicit seed and RNG run, never list order. No unpaired test is substituted.",
                      "All expected pairs must finish with valid metrics/coverage before an interval is shown.",
                      "Relative change is a point estimate, undefined for a zero baseline; no ratio interval is computed.",
                      "Intervals are per metric, without multiple-comparison correction; zero width does not prove model accuracy.",
                      "Equal seed metadata does not guarantee identical packet trajectories after topology or model changes.",
                      "The configuration diff describes changed conditions; it does not prove a causal effect."]}


def main():
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path, help="baseline experiment's results.json array")
    parser.add_argument("candidate", type=Path, help="candidate experiment's results.json array")
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--format", choices=("json", "html", "zip"), default="json")
    parser.add_argument("--start-s", type=float)
    parser.add_argument("--end-s", type=float)
    args = parser.parse_args()
    try:
        cohorts = []
        for path in (args.baseline, args.candidate):
            with path.open() as stream:
                data = json.load(stream)
            if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
                raise ValueError("each input must be an experiment results.json array")
            cohorts.append(data)
        result = compare(*cohorts, start_s=args.start_s, end_s=args.end_s)
        if args.format == "json":
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        else:
            from lab.experiment_comparison_report import comparison_bundle, comparison_report
            if args.format == "html":
                args.output.write_text(comparison_report(result))
            else:
                args.output.write_bytes(comparison_bundle(*cohorts, result))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"comparison failed: {exc}\n")


if __name__ == "__main__":
    main()
