"""Bounded CSV and gravity traffic matrices -> validated scenario applications."""

import csv
import io
import math

from lab.model import parse_rate, validate_project, validate_scenario

MAX_FLOWS = 10000
MAX_CSV_BYTES = 2 * 1024 * 1024
FIELDS = {"id", "src", "dst", "type", "rate", "size", "start", "stop", "dscp"}


def rate_text(value):
    value = number(value, "rate bps", .001, 1e10)
    # parse_rate/ns-3 expect decimal units, not Python's scientific notation.
    return f"{value:.9f}".rstrip("0").rstrip(".") + "bps"


def number(value, name, low, high):
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{name}: number required") from None
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"{name}: {low}..{high}")
    return result


def csv_apps(text, duration):
    if not isinstance(text, str) or len(text.encode()) > MAX_CSV_BYTES:
        raise ValueError("CSV must be text, at most 2 MiB")
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    names = reader.fieldnames or []
    if not {"src", "dst"} <= set(names) or len(names) != len(set(names)) or set(names) - FIELDS:
        raise ValueError("CSV headers: src,dst required; optional id,type,rate,size,start,stop,dscp")
    apps = []
    for i, row in enumerate(reader, 1):
        if i > MAX_FLOWS:
            raise ValueError(f"at most {MAX_FLOWS} flows")
        if None in row or any(v is None for v in row.values()):
            raise ValueError(f"CSV row {i}: column count mismatch")
        row = {k: v.strip() for k, v in row.items()}
        typ = row.get("type") or "udp"
        if typ not in ("tcp", "udp", "ping"):
            raise ValueError(f"row {i}: type tcp | udp | ping")
        start = number(row.get("start") or 0, "start", 0, duration)
        stop = number(row.get("stop") or duration, "stop", 0, duration)
        if start >= stop:
            raise ValueError(f"row {i}: start < stop required")
        app = {"id": row.get("id") or f"matrix{i}", "src": row["src"], "dst": row["dst"],
               "type": typ, "start": start, "stop": stop}
        if typ == "udp" or row.get("rate"):
            rate = number(parse_rate(row.get("rate") or ""), "rate bps", .001, 1e10)
            app["rate"] = rate_text(rate)
        for field, low, high in (("size", 8, 65000), ("dscp", 0, 63)):
            if row.get(field):
                value = number(row[field], field, low, high)
                if not value.is_integer():
                    raise ValueError(f"row {i}: {field} must be integer")
                app[field] = int(value)
        apps.append(app)
    if not apps:
        raise ValueError("empty traffic matrix")
    return apps


def gravity_apps(project, duration, total_mbps, weights=None):
    """Ordered demand T_ij proportional to w_i*w_j, with no self traffic.

    Weights can select a subset of endpoints. Normalization preserves the total
    offered rate. No RNG or topology mutation; repeat imports are identical.
    """
    endpoints = {n for n, d in project["devices"].items() if d["type"] in ("host", "server")}
    if weights is None:
        weights = {name: 1 for name in endpoints}
    if not isinstance(weights, dict) or not 2 <= len(weights) <= 100 or set(weights) - endpoints:
        raise ValueError("gravity weights: select 2..100 host/server endpoints")
    weights = {n: number(w, f"weight {n}", 1e-9, 1e9) for n, w in weights.items()}
    total = number(total_mbps, "total_mbps", 1e-9, 10000) * 1e6
    pairs = [(a, b, weights[a] * weights[b]) for a in sorted(weights) for b in sorted(weights) if a != b]
    denominator = math.fsum(w for _, _, w in pairs)
    return [{"id": f"gravity{i}", "src": a, "dst": b, "type": "udp", "rate": rate_text(total * w / denominator),
             "start": 0, "stop": duration} for i, (a, b, w) in enumerate(pairs, 1)]


def import_matrix(project, options):
    if not isinstance(project, dict):
        raise ValueError("project must be an object")
    errors = validate_project(project)
    if errors:
        raise ValueError("; ".join(errors[:5]))
    duration = number(options.get("duration", 30), "duration", 1, 3600)
    mode = options.get("mode", "csv")
    if mode == "csv":
        apps = csv_apps(options.get("csv", ""), duration)
    elif mode == "gravity":
        apps = gravity_apps(project, duration, options.get("total_mbps", 100), options.get("weights"))
    else:
        raise ValueError("mode: csv | gravity")
    scenario = {"duration": duration, "ip_version": options.get("ip_version", 4), "apps": apps, "events": []}
    errors = validate_scenario(project, scenario)
    if errors:
        raise ValueError("; ".join(errors[:5]))
    return {"scenario": scenario, "count": len(apps),
            "offered_bps": math.fsum(parse_rate(a["rate"]) for a in apps if a.get("rate"))}
