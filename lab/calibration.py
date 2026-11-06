"""Compare model and real measurements without silently retuning defaults.

CSV columns: protocol,metric,unit,model,real,source,verified,abs_tolerance,rel_tolerance
Tolerances use |model-real| <= abs_tolerance + rel_tolerance*|real|.
Only explicitly verified observations can suggest supported scenario overrides.
Traffic outage duration is never confused with a protocol's own timer.
"""

import argparse
import csv
import json
import math
from pathlib import Path
import statistics
import sys

OVERRIDES = {("stp", "port_forwarding_s"): "stp_convergence_s",
             ("vrrp", "master_down_s"): "vrrp_convergence_s",
             ("rip", "route_recovery_s"): "rip_convergence_s",
             ("ospf", "route_update_s"): "ospf_convergence_s",
             ("bgp", "session_recovery_s"): "bgp_convergence_s"}


def compare(records):
    rows, groups = [], {}
    for i, record in enumerate(records, 1):
        key = tuple(str(record.get(k, "")).strip().lower() for k in ("protocol", "metric", "unit"))
        if not all(key) or not record.get("source"):
            raise ValueError(f"row {i}: protocol, metric, unit and source are required")
        values = {}
        for field in ("model", "real", "abs_tolerance", "rel_tolerance"):
            try:
                value = float(record.get(field, 0 if field.endswith("tolerance") else None))
            except (ValueError, TypeError):
                raise ValueError(f"row {i}: {field} must be numeric") from None
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"row {i}: {field} must be finite and nonnegative")
            values[field] = value
        verified = str(record.get("verified", "false")).strip().lower() == "true"
        delta = abs(values["model"] - values["real"])
        bound = values["abs_tolerance"] + values["rel_tolerance"] * values["real"]
        row = {"protocol": key[0], "metric": key[1], "unit": key[2], **values,
               "source": str(record["source"]), "declared_verified": verified,
               "absolute_error": delta, "relative_error": delta / values["real"] if values["real"] else None,
               "status": ("passed" if delta <= bound else "failed") if verified else "unverified"}
        rows.append(row)
        if verified:
            groups.setdefault(key, []).append(row)
    summary, overrides = [], {}
    for (protocol, metric, unit), samples in sorted(groups.items()):
        real_median = statistics.median(r["real"] for r in samples)
        summary.append({"protocol": protocol, "metric": metric, "unit": unit, "samples": len(samples),
                        "model_median": statistics.median(r["model"] for r in samples), "real_median": real_median,
                        "max_absolute_error": max(r["absolute_error"] for r in samples),
                        "within_bounds": all(r["status"] == "passed" for r in samples)})
        field = OVERRIDES.get((protocol, metric))
        if field and unit == "s":
            overrides[field] = real_median
    return {"schema_version": 1, "observations": rows, "summary": summary,
            "suggested_scenario_overrides": overrides,
            "status": "unverified" if not groups else ("failed" if any(r["status"] == "failed" for r in rows) else "passed"),
            "defaults_changed": False}


def markdown(report):
    lines = ["| Protocol | Metric | Model median | Real median | Unit | Samples | Within bounds |",
             "|---|---|---:|---:|---|---:|---|"]
    for row in report["summary"]:
        lines.append(f"| {row['protocol']} | {row['metric']} | {row['model_median']:.3g} | {row['real_median']:.3g} "
                     f"| {row['unit']} | {row['samples']} | {row['within_bounds']} |")
    if not report["summary"]:
        lines.append("\nNo verified real measurements. Defaults remain unchanged.")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv", type=Path)
    ap.add_argument("--format", choices=("json", "markdown"), default="json")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args(argv)
    try:
        with args.csv.open(newline="") as f:
            report = compare(csv.DictReader(f))
    except (ValueError, OSError) as e:
        ap.error(str(e))
    text = markdown(report) if args.format == "markdown" else json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        sys.stdout.write(text)
    return {"passed": 0, "failed": 1, "unverified": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
