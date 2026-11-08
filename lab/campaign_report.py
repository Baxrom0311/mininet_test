"""Campaign overview and nested, existing experiment export formats."""

import io
import json
import zipfile

from lab.experiment_comparison_report import comparison_report
from lab.report import _num, e, write_experiment_bundle, kv, page, table
from lab.report_export import write_csv, write_json


def compact_report(report):
    """Opt-in JSON representation with exact pointers to the single stored cohort summaries."""
    positions = {c["variant"]: i for i, c in enumerate(report["experiments"])}
    comparisons = []
    for c in report["comparisons"]:
        result = c["result"]
        if result is not None:
            result = dict(result)
            for side, variant in (("baseline", "baseline"), ("candidate", c["variant"])):
                if variant not in positions:
                    raise ValueError("comparison cohort reference missing")
                result[side] = {k: v for k, v in result[side].items() if k not in ("metrics", "run_windows", "notes")}
                result[side]["summary_ref"] = f"/experiments/{positions[variant]}/summary"
        comparisons.append({**c, "result": result})
    return {**report, "representation": "cohort_references", "comparisons": comparisons}


def campaign_report(report):
    campaign = report["campaign"]
    body = kv([("Campaign", campaign["campaign_id"]), ("Status", campaign["status"]), ("Engine", campaign["engine"]),
               ("Seed", campaign["seed"]), ("Planned runs", campaign["run_count"]), ("Analysis complete", report["analysis_complete"])])
    body += "<p>95% intervals are per comparison and metric; no multiple-comparison adjustment or automatic winner.</p>"
    body += "<h2>Conditions</h2>"
    body += table(["Variant", "Label", "Experiment", "States", "Missing", "Invalid"],
                  [[c["key"], c["label"], c["experiment_id"], json.dumps(c["statuses"], sort_keys=True),
                    len(c["missing_run_ids"]), len(c["invalid_run_ids"])] for c in campaign["conditions"]], num=(4, 5))
    if campaign.get("submission_error"):
        body += "<p>Submission error: " + e(campaign["submission_error"]) + "</p>"
    if campaign.get("orphaned_run_ids"):
        body += "<p>Pending metadata without local work items: " + e(", ".join(campaign["orphaned_run_ids"])) + "</p>"
    for comparison in report["comparisons"]:
        body += "<h2>" + e(comparison["variant"]) + " minus baseline</h2>"
        if comparison["error"]:
            body += "<p>" + e(comparison["error"]) + "</p>"
            continue
        body += table(["Flow", "Metric", "Pairs", "Missing", "Delta", "Unit", "Relative, %", "Delta 95% CI", "CI excludes zero"],
                      [[r["flow"], r["metric"], r["pairs"], r["missing_pairs"], r["delta_mean"], r["delta_unit"],
                        r["relative_change_pct"], " … ".join(_num(v, 6) for v in r["ci95"]) if r["ci95"] is not None else "—",
                        r["ci_excludes_zero"]] for r in comparison["result"]["metrics"]], num=(2, 3, 4, 6))
    errors = [(x["variant"], x["error"]) for x in report["experiments"] if x["error"]]
    if errors:
        body += "<h2>Cohort errors</h2>" + table(["Variant", "Error"], errors)
    body += "<h2>Method</h2><ul>" + "".join("<li>" + e(n) + "</li>" for n in report["notes"]) + "</ul>"
    return page("Experiment campaign", body)


def write_campaign_bundle(plan, report, cohorts, target):
    """Stream nested experiment ZIPs without materializing their complete bytes."""
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        write_json(z, "plan.json", plan, indent=2)
        write_json(z, "campaign.json", report, indent=2)
        z.writestr("report.html", campaign_report(report))
        fields = ("run_id", "variant", "experiment_id", "seed", "rng_run", "status", "progress", "error")
        write_csv(z, "run_status.csv", ([r[k] for k in fields] for r in report["campaign"]["runs"]), fields)
        for c in report["comparisons"]:
            if c["error"]:
                continue
            z.writestr(f"comparisons/{c['variant']}.html", comparison_report(c["result"]))
        write_csv(z, "effects.csv", (
            [c["variant"], r["flow"], r["metric"], r["pairs"], r["missing_pairs"], r["delta_mean"],
             r["delta_unit"], r["relative_change_pct"], *(r["ci95"] or [None, None]), r["ci_excludes_zero"]]
            for c in report["comparisons"] if not c["error"] for r in c["result"]["metrics"]),
            ["variant", "flow", "metric", "pairs", "missing_pairs", "delta_mean", "unit",
             "relative_change_pct", "ci95_low", "ci95_high", "ci_excludes_zero"])
        write_csv(z, "comparison_errors.csv", ([c["variant"], c["error"]] for c in report["comparisons"] if c["error"]), ["variant", "error"])
        for c in report["experiments"]:
            records = cohorts[c["variant"]]
            if not records:
                z.writestr(f"experiments/{c['variant']}-unavailable.txt", c["error"] or "No surviving run records")
                continue
            with z.open(f"experiments/{c['variant']}.zip", "w", force_zip64=True) as nested_target:
                if c["summary"] is not None:
                    write_experiment_bundle(records, c["summary"], nested_target)
                else:
                    with zipfile.ZipFile(nested_target, "w", zipfile.ZIP_DEFLATED) as nested:
                        write_json(nested, "results.json", records)
                        nested.writestr("error.txt", c["error"] or "Cohort validation failed")


def campaign_bundle(plan, report, cohorts):
    buf = io.BytesIO()
    write_campaign_bundle(plan, report, cohorts, buf)
    return buf.getvalue()
