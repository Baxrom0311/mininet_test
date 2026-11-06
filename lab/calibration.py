"""Compare model and real measurements without silently retuning defaults.

CSV columns: protocol,metric,unit,model,real,source,verified,abs_tolerance,rel_tolerance
Tolerances use |model-real| <= abs_tolerance + rel_tolerance*|real|.
Only explicitly verified observations can suggest supported scenario overrides.
Traffic outage duration is never confused with a protocol's own timer.
Use --min-samples and --require-metric to demand a complete measurement set.
Repeated verified source IDs do not count twice or bias the median.
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


def _metric_key(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3 or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError("required metric must be (protocol, metric, unit)")
    return tuple(v.strip().lower() for v in value)


def compare(records, *, min_samples=1, required_metrics=()):
    """Count distinct verified source IDs, retaining duplicate rows for auditing.

    Distinct provenance is necessary for counting trials, not proof of independence
    or matching experimental conditions. A source cannot describe two different
    observations for the same protocol/metric/unit in one report.
    """
    if type(min_samples) is not int or not 1 <= min_samples <= 10000:
        raise ValueError("min_samples must be an integer 1..10000")
    required = {_metric_key(value) for value in required_metrics}
    rows, groups, seen, input_counts = [], {}, {}, {}
    for i, record in enumerate(records, 1):
        key = tuple(str(record.get(k, "")).strip().lower() for k in ("protocol", "metric", "unit"))
        source = str(record.get("source") or "").strip()
        if not all(key) or not source:
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
               "source": source, "declared_verified": verified, "duplicate_of": None,
               "absolute_error": delta, "relative_error": delta / values["real"] if values["real"] else None,
               "status": ("passed" if delta <= bound else "failed") if verified else "unverified"}
        if verified:
            input_counts[key] = input_counts.get(key, 0) + 1
            identity = (key, source)
            previous = seen.get(identity)
            if previous:
                first_row, first_values = previous
                if values != first_values:
                    raise ValueError(f"row {i}: source {source!r} conflicts with row {first_row} for {':'.join(key)}")
                row["duplicate_of"] = first_row
            else:
                seen[identity] = (i, values)
                groups.setdefault(key, []).append(row)
        rows.append(row)
    summary, overrides = [], {}
    for protocol, metric, unit in sorted(set(groups) | required):
        samples = groups.get((protocol, metric, unit), [])
        n = len(samples)
        real_median = statistics.median(r["real"] for r in samples) if n else None
        model_values = sorted({r["model"] for r in samples})
        enough = n >= min_samples
        field = OVERRIDES.get((protocol, metric))
        eligible = bool(field and unit == "s" and enough and len(model_values) == 1)
        summary.append({"protocol": protocol, "metric": metric, "unit": unit, "samples": len(samples),
                        "input_samples": input_counts.get((protocol, metric, unit), 0),
                        "duplicate_samples": input_counts.get((protocol, metric, unit), 0) - n,
                        "min_samples": min_samples, "sufficient_samples": enough,
                        "model_values": model_values, "mixed_model_values": len(model_values) > 1,
                        "override_eligible": eligible,
                        "model_median": statistics.median(r["model"] for r in samples) if n else None,
                        "real_median": real_median,
                        "max_absolute_error": max((r["absolute_error"] for r in samples), default=None),
                        "within_bounds": all(r["status"] == "passed" for r in samples) if n else None})
        if eligible:
            overrides[field] = real_median
    if not summary:
        status = "unverified"
    elif any(not row["sufficient_samples"] for row in summary):
        status = "insufficient"
    else:
        status = "failed" if any(row["within_bounds"] is False for row in summary) else "passed"
    return {"schema_version": 1, "observations": rows, "summary": summary,
            "min_samples": min_samples,
            "required_metrics": [{"protocol": p, "metric": m, "unit": u} for p, m, u in sorted(required)],
            "duplicate_rows": sum(row["duplicate_of"] is not None for row in rows),
            "suggested_scenario_overrides": overrides,
            "status": status,
            "defaults_changed": False}


def markdown(report):
    def number(value):
        return "—" if value is None else f"{value:.3g}"
    def cell(value):
        return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    lines = ["| Protocol | Metric | Model median | Real median | Unit | Sources/min | Duplicates | Within bounds |",
             "|---|---|---:|---:|---|---:|---:|---|"]
    for row in report["summary"]:
        lines.append(f"| {cell(row['protocol'])} | {cell(row['metric'])} | {number(row['model_median'])} | {number(row['real_median'])} "
                     f"| {cell(row['unit'])} | {row['samples']}/{row.get('min_samples', 1)} "
                     f"| {row.get('duplicate_samples', 0)} | {row['within_bounds']} |")
    if not any(row["samples"] for row in report["summary"]):
        lines.append("\nNo verified real measurements. Defaults remain unchanged.")
    lines.append(f"\nStatus: {report['status']}; minimum distinct verified sources per metric: {report.get('min_samples', 1)}.")
    if any(row.get("mixed_model_values") for row in report["summary"]):
        lines.append("Mixed model values: no pooled scenario override is suggested for those metrics.")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv", type=Path)
    ap.add_argument("--format", choices=("json", "markdown"), default="json")
    ap.add_argument("--output", type=Path)
    ap.add_argument("--min-samples", type=int, default=1, help="minimum distinct verified sources per metric (1..10000)")
    def metric_arg(value):
        try:
            return _metric_key(value.split(":"))
        except ValueError as error:
            raise argparse.ArgumentTypeError(str(error)) from None
    ap.add_argument("--require-metric", type=metric_arg, action="append", default=[], metavar="PROTOCOL:METRIC:UNIT",
                    help="require this metric even if missing from the CSV (repeatable)")
    args = ap.parse_args(argv)
    try:
        with args.csv.open(newline="") as f:
            report = compare(csv.DictReader(f), min_samples=args.min_samples, required_metrics=args.require_metric)
    except (ValueError, OSError) as e:
        ap.error(str(e))
    text = markdown(report) if args.format == "markdown" else json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        sys.stdout.write(text)
    return {"passed": 0, "failed": 1, "unverified": 2, "insufficient": 3}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
