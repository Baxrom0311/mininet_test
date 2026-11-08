"""CSV and replay-input ZIP exports; public compatibility lives in lab.report."""

import csv
import io
from itertools import chain
import json
import zipfile

from lab.report_inputs import report_runs
from lab.reproducibility import comparison_key, digest, verify


def replay_inputs(run):
    record = run.get("reproducibility")
    if not verify(record):
        return None
    project, name, engine = record["project"], record.get("scenario_name"), run.get("engine")
    scenarios = project.get("scenarios")
    if (not isinstance(name, str) or not isinstance(scenarios, dict) or not isinstance(scenarios.get(name), dict)
            or engine not in ("analytic", "ns3", "mininet")):
        return None
    try:
        scenario = scenarios[name]
        if (record.get("comparison_key") != comparison_key(project, scenario, engine)
                or "scenario" in run and digest(run["scenario"]) != digest(scenario)):
            return None
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None
    manifest = {k: v for k, v in record.items() if k not in ("project", "effective_scenario")}
    return {"project.json": project, "manifest.json": manifest,
            "effective-input.json": {"project": project, "scenario": record["effective_scenario"]},
            "run-request.json": {"project": project, "scenario": name, "engine": engine}}


def _csv(rows, header):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


HEADERS = {
    "flows.csv": ["run_id", "flow", "t", "thr_bps", "delay_ms", "jitter_ms", "loss_pct", "rtt_ms", "mos"],
    "flow_summary.csv": ["run_id", "flow", "type", "src", "dst", "avg_thr_bps", "avg_delay_ms", "avg_jitter_ms",
                         "loss_pct", "avg_rtt_ms", "hidden"],
    "links.csv": ["run_id", "link", "t", "ab_bps", "ba_bps", "util", "queue_ab", "queue_ba", "drops_ab", "drops_ba"],
    "nodes.csv": ["run_id", "device", "t", "routes", "fwd_pps", "rx_pps", "tx_pps", "noroute", "discards"],
    "apps.csv": ["run_id", "app", "type", "summary_json"],
    "availability.csv": ["run_id", "flow", "availability_pct", "downtime_s", "outages"],
    "convergence.csv": ["run_id", "t", "action", "target", "converged_s", "devices"],
    "route_events.csv": ["run_id", "t", "device", "added", "removed"],
    "events.csv": ["run_id", "t", "action", "target", "source"],
}


def write_csv(archive, name, rows, header):
    """Consume rows once, writing UTF-8 CSV directly into a ZIP64 entry."""
    with archive.open(name, "w", force_zip64=True) as binary:
        with io.TextIOWrapper(binary, encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows(rows)


def _json_chunks(value, indent):
    try:
        yield from json.JSONEncoder(ensure_ascii=False, allow_nan=False, indent=indent).iterencode(value)
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise ValueError("report data must contain finite JSON values") from error


def write_json(archive, name, value, *, indent=None):
    """Write encoder chunks; propagate target I/O failures as storage errors."""
    with archive.open(name, "w", force_zip64=True) as binary:
        with io.TextIOWrapper(binary, encoding="utf-8", newline="") as stream:
            for chunk in _json_chunks(value, indent):
                stream.write(chunk)


def _rows(runs, name):
    for run in runs:
        rid = run.get("run_id") or ""
        if name in ("flows.csv", "flow_summary.csv"):
            for fid, flow in (run.get("flows") or {}).items():
                if name == "flow_summary.csv":
                    summary = flow.get("summary") or {}
                    yield [rid, fid, flow.get("type"), flow.get("src"),
                           json.dumps(flow.get("dst")) if isinstance(flow.get("dst"), list) else flow.get("dst"),
                           summary.get("avg_thr_bps"), summary.get("avg_delay_ms"), summary.get("avg_jitter_ms"),
                           summary.get("loss_pct"), summary.get("avg_rtt_ms"), bool(flow.get("hidden"))]
                else:
                    for point in flow.get("series") or []:
                        yield [rid, fid, *[point.get(key) for key in ("t", "thr_bps", "delay_ms", "jitter_ms", "loss_pct", "rtt_ms", "mos")]]
        elif name in ("links.csv", "nodes.csv"):
            section, fields = (("links", ("t", "ab_bps", "ba_bps", "util", "queue_ab", "queue_ba", "drops_ab", "drops_ba"))
                               if name == "links.csv" else ("nodes", ("t", "routes", "fwd_pps", "rx_pps", "tx_pps", "noroute", "discards")))
            for key, points in (run.get(section) or {}).items():
                for point in points or []:
                    yield [rid, key, *[point.get(field) for field in fields]]
        elif name == "apps.csv":
            for aid, app in (run.get("apps") or {}).items():
                yield [rid, aid, app.get("type"), json.dumps({key: value for key, value in app.items() if key != "type"},
                                                           ensure_ascii=False, default=str)]
        elif name == "availability.csv":
            for fid, item in ((run.get("availability") or {}).get("flows") or {}).items():
                yield [rid, fid, item.get("availability_pct"), item.get("downtime_s"), len(item.get("outages") or [])]
        elif name == "convergence.csv":
            for item in run.get("convergence") or []:
                yield [rid, item.get("t"), item.get("action"), item.get("link"), item.get("converged_s"), " ".join(item.get("devices") or [])]
        elif name == "route_events.csv":
            for item in run.get("route_events") or []:
                yield [rid, item.get("t"), item.get("dev"), " | ".join(item.get("added") or []), " | ".join(item.get("removed") or [])]
        elif name == "events.csv":
            for item in run.get("events") or []:
                yield [rid, item.get("t"), item.get("action"), item.get("link") or item.get("device"), item.get("source") or ""]


def write_results(archive, runs):
    """Append result entries to a caller-owned open ZIP; discard it on failure."""
    runs = report_runs(runs)
    for name, header in HEADERS.items():
        rows = _rows(runs, name)
        first = next(rows, None)
        if first is None and name not in ("flows.csv", "flow_summary.csv"):
            continue
        write_csv(archive, name, chain((first,), rows) if first is not None else (), header)
    write_json(archive, "results.json", runs[0] if len(runs) == 1 else runs)
    for index, run in enumerate(runs):
        prefix = "" if len(runs) == 1 else f"runs/{index + 1:02d}/"
        payloads = replay_inputs(run)
        if payloads is None:
            if run.get("reproducibility") is not None:
                archive.writestr(prefix+"replay-unavailable.txt", "Recorded inputs do not establish a replay request.")
            continue
        for name, payload in payloads.items():
            write_json(archive, prefix+name, payload, indent=2)


def write_csv_bundle(runs, target):
    """Write to a caller-owned binary target without closing it; partial on error."""
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        write_results(archive, runs)


def csv_bundle(runs):
    """Compatibility API returning the final ZIP bytes in memory."""
    buf = io.BytesIO()
    write_csv_bundle(runs, buf)
    return buf.getvalue()
