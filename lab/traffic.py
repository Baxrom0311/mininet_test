"""Bounded CSV and gravity traffic matrices -> validated scenario applications."""

import csv
from decimal import Decimal, InvalidOperation
import io
import math

from lab.model import parse_rate, validate_project, validate_scenario
from lab.project_shape import project_for_execution
from lab.validation_numbers import number as finite_number

MAX_FLOWS = 10000
MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_CSV_FIELD_CHARS = 128 * 1024
MIN_RATE_BPS = 1
MAX_RATE_BPS = 1e10
FIELDS = {"id", "src", "dst", "type", "rate", "size", "start", "stop", "dscp"}


def rate_text(value):
    value = number(value, "rate bps", MIN_RATE_BPS, MAX_RATE_BPS)
    # parse_rate/ns-3 expect decimal units, not Python's scientific notation.
    return f"{value:.9f}".rstrip("0").rstrip(".") + "bps"


def number(value, name, low, high):
    return finite_number(value, name, minimum=low, maximum=high)


def _csv_integer(value, name, low, high):
    """Keep integral decimal CSV spellings without float rounding/truncation."""
    try:
        parsed = Decimal(value)
        if not parsed.is_finite() or not low <= parsed <= high or parsed != parsed.to_integral_value():
            raise ValueError
        return int(parsed)
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        raise ValueError(f"{name}: integer {low}..{high}") from None


def _field_lengths(values, where):
    if any(len(value) > MAX_CSV_FIELD_CHARS for value in values if value is not None):
        raise ValueError(f"{where}: field exceeds {MAX_CSV_FIELD_CHARS} characters")


def csv_apps(text, duration):
    duration = number(duration, "duration", 1, 3600)
    # Every Unicode code point needs at least one UTF-8 byte. Reject oversized
    # character counts before allocating the encoded representation.
    if not isinstance(text, str) or len(text) > MAX_CSV_BYTES:
        raise ValueError("CSV must be text, at most 2 MiB")
    try:
        if len(text.encode("utf-8")) > MAX_CSV_BYTES:
            raise ValueError("CSV must be text, at most 2 MiB")
    except UnicodeEncodeError:
        raise ValueError("CSV must be valid UTF-8 text") from None
    # Do not change csv.field_size_limit: it is process-global and shared by
    # concurrent imports. A lower ambient parser limit may reject earlier; our
    # own field cap still applies if another component raised that global limit.
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff"), newline=""), strict=True)
    apps, ids = [], set()
    try:
        names = reader.fieldnames or []
        _field_lengths(names, "CSV header")
        if not {"src", "dst"} <= set(names) or len(names) != len(set(names)) or set(names) - FIELDS:
            raise ValueError("CSV headers: src,dst required; optional id,type,rate,size,start,stop,dscp")
        for i, row in enumerate(reader, 1):
            if i > MAX_FLOWS:
                raise ValueError(f"at most {MAX_FLOWS} flows")
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"CSV row {i}: column count mismatch")
            _field_lengths(row.values(), f"CSV row {i}")
            row = {k: v.strip() for k, v in row.items()}
            try:
                typ = row.get("type") or "udp"
                if typ not in ("tcp", "udp", "ping"):
                    raise ValueError("type tcp | udp | ping")
                if not row["src"] or not row["dst"]:
                    raise ValueError("src,dst must be nonempty device names")
                start = number(row.get("start") or 0, "start", 0, duration)
                stop = number(row.get("stop") or duration, "stop", 0, duration)
                if start >= stop:
                    raise ValueError("start < stop required")
                app = {"id": row.get("id") or f"matrix{i}", "src": row["src"], "dst": row["dst"],
                       "type": typ, "start": start, "stop": stop}
                if app["id"] in ids:
                    raise ValueError(f"duplicate id: {app['id']}")
                ids.add(app["id"])
                if typ == "udp" or row.get("rate"):
                    rate = number(parse_rate(row.get("rate") or ""), "rate bps", MIN_RATE_BPS, MAX_RATE_BPS)
                    app["rate"] = rate_text(rate)
                for field, low, high in (("size", 1, 65507), ("dscp", 0, 63)):
                    if row.get(field):
                        app[field] = _csv_integer(row[field], field, low, high)
            except ValueError as exc:
                raise ValueError(f"CSV row {i}: {exc}") from None
            apps.append(app)
    except csv.Error as exc:
        raise ValueError(f"CSV row {len(apps) + 1} (line {max(reader.reader.line_num, 1)}): {exc}") from None
    if not apps:
        raise ValueError("empty traffic matrix")
    return apps


def _prepared_project(project):
    if not isinstance(project, dict):
        raise ValueError("project must be an object")
    errors = validate_project(project)
    if errors:
        raise ValueError("; ".join(errors[:5]))
    return project_for_execution(project)


def _gravity_apps(project, duration, total_mbps, weights):
    devices = project["devices"]
    if weights is None:
        endpoints = [name for name, device in devices.items() if device["type"] in ("host", "server")]
        if not 2 <= len(endpoints) <= 100:
            raise ValueError("gravity weights: select 2..100 host/server endpoints")
        weights = {name: 1 for name in endpoints}
    if not isinstance(weights, dict) or not 2 <= len(weights) <= 100:
        raise ValueError("gravity weights: select 2..100 host/server endpoints")
    for name in weights:
        device = devices.get(name) if isinstance(name, str) else None
        if device is None or device["type"] not in ("host", "server"):
            raise ValueError("gravity weights: select 2..100 host/server endpoints")
    weights = {name: number(value, f"weight {name}", 1e-9, 1e9) for name, value in weights.items()}
    total = number(total_mbps, "total_mbps", 1e-9, 10000) * 1e6
    pairs = [(a, b, weights[a] * weights[b]) for a in sorted(weights) for b in sorted(weights) if a != b]
    denominator = math.fsum(weight for _, _, weight in pairs)
    return [{"id": f"gravity{i}", "src": a, "dst": b, "type": "udp", "rate": rate_text(total * weight / denominator),
             "start": 0, "stop": duration} for i, (a, b, weight) in enumerate(pairs, 1)]


def gravity_apps(project, duration, total_mbps, weights=None):
    """Ordered demand proportional to w_i*w_j, without self traffic or rounding floors.

    Output is deterministic; any share below one native bit/s rejects the entire
    matrix. Decimal rate formatting preserves total demand within its precision.
    """
    duration = number(duration, "duration", 1, 3600)
    return _gravity_apps(_prepared_project(project), duration, total_mbps, weights)


def import_matrix(project, options):
    if not isinstance(options, dict):
        raise ValueError("options must be an object")
    project = _prepared_project(project)
    duration = number(options.get("duration", 30), "duration", 1, 3600)
    mode = options.get("mode", "csv")
    if mode == "csv":
        apps = csv_apps(options.get("csv", ""), duration)
    elif mode == "gravity":
        apps = _gravity_apps(project, duration, options.get("total_mbps", 100), options.get("weights"))
    else:
        raise ValueError("mode: csv | gravity")
    scenario = {"duration": duration, "ip_version": options.get("ip_version", 4), "apps": apps, "events": []}
    errors = validate_scenario(project, scenario)
    if errors:
        raise ValueError("; ".join(errors[:5]))
    return {"scenario": scenario, "count": len(apps),
            "offered_bps": math.fsum(parse_rate(a["rate"]) for a in apps if a.get("rate"))}
