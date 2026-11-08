"""Standalone paired-comparison HTML and auditable CSV/raw-input ZIP exports."""

import io
import json
import zipfile

from lab.report import _csv, _num, csv_bundle, e, kv, page, table
from lab.report_export import replay_inputs


def comparison_report(result):
    body = kv([("Baseline", result["baseline"]["experiment_id"]), ("Candidate", result["candidate"]["experiment_id"]),
               ("Expected pairs", result["expected_pairs"]), ("Completed pairs", result["completed_pairs"]),
               ("Complete", result["complete"]), ("Confidence", "95%")])
    if result.get("window"):
        w = result["window"]
        body += kv([("Analysis start, s", w["start_s"]), ("Analysis end, s", w["end_s"]),
                    ("Weighting", w["weighting"]), ("Selection", w["selection"])])
    body += "<h2>Candidate minus baseline</h2>"
    body += table(["Flow", "Metric", "Pairs", "Missing", "Incomplete", "Baseline mean", "Candidate mean", "Delta mean",
                   "Delta unit", "Change, %", "Delta 95% CI", "CI excludes zero", "Preferred direction", "Window mismatch"],
                  [[r["flow"], r["metric"], r["pairs"], r["missing_pairs"], r["incomplete_pairs"], r["baseline_mean"],
                    r["candidate_mean"], r["delta_mean"], r["delta_unit"], r["relative_change_pct"],
                    " … ".join(_num(v, 6) for v in r["ci95"]) if r["ci95"] is not None else "—",
                    r["ci_excludes_zero"], r["preferred_direction"], r["window_mismatch"]] for r in result["metrics"]],
                  num=(2, 3, 4, 5, 6, 7, 9))
    body += "<h2>Changed conditions</h2>"
    diff = result["design_diff"]
    body += table(["JSON pointer", "Operation", "Baseline", "Candidate"],
                  [[d["path"], d["operation"], json.dumps(d["before"], ensure_ascii=False) if "before" in d else "—",
                    json.dumps(d["after"], ensure_ascii=False) if "after" in d else "—"] for d in diff["changes"]])
    if not diff["available"]:
        body += '<p class="mut">Configuration diff awaits a completed run in each experiment.</p>'
    if diff["truncated"]:
        body += '<p class="mut">Diff truncated; inspect the exported full inputs.</p>'
    body += "<h2>Pair identities</h2>"
    body += table(["Seed", "RNG run", "Baseline run", "Candidate run", "Baseline status", "Candidate status"],
                  [[p[k] for k in ("seed", "rng_run", "baseline_run_id", "candidate_run_id", "baseline_status", "candidate_status")]
                   for p in result["pairs"]], num=(0, 1))
    if result["unmatched_baseline"] or result["unmatched_candidate"]:
        body += "<p>Unmatched baseline: " + e(", ".join(result["unmatched_baseline"])) + "</p>"
        body += "<p>Unmatched candidate: " + e(", ".join(result["unmatched_candidate"])) + "</p>"
    body += "<h2>Method</h2><p>" + e(result["method"]) + "</p><ul>"
    body += "".join("<li>" + e(note) + "</li>" for note in result["notes"]) + "</ul>"
    return page("Paired experiment comparison", body)


def comparison_bundle(baseline, candidate, result):
    buf = io.BytesIO(csv_bundle(baseline + candidate))
    with zipfile.ZipFile(buf, "a", zipfile.ZIP_DEFLATED) as z:
        z.writestr("comparison.json", json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        z.writestr("report.html", comparison_report(result))
        columns = ("flow", "metric", "pairs", "missing_pairs", "incomplete_pairs", "window_mismatch", "baseline_mean",
                   "candidate_mean", "delta_mean", "delta_stddev", "delta_unit", "relative_change_pct", "ci_excludes_zero", "preferred_direction")
        z.writestr("paired_statistics.csv", _csv([[*[r[k] for k in columns], *(r["ci95"] or [None, None])]
                                               for r in result["metrics"]], [*columns, "ci95_low", "ci95_high"]))
        columns = ("flow", "metric", "seed", "rng_run", "baseline_run_id", "candidate_run_id", "baseline", "candidate", "delta", "complete", "reason")
        z.writestr("paired_samples.csv", _csv([[s[k] for k in columns] for s in result["samples"]], columns))
        mapping = []
        for index, (role, run) in enumerate([*(('baseline', r) for r in baseline), *(('candidate', r) for r in candidate)], 1):
            available = replay_inputs(run) is not None
            mapping.append([role, run["run_id"], run["seed"], run["rng_run"], f"runs/{index:02d}/" if available else None, available])
        z.writestr("run_map.csv", _csv(mapping, ["role", "run_id", "seed", "rng_run", "input_directory", "replay_inputs_available"]))
    return buf.getvalue()
