"""CSV and replay-input ZIP exports; public compatibility lives in lab.report."""

import csv
import io
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


def csv_bundle(runs):
    """runs: bitta natija yoki ro'yxat -> zip bayt (flows/links/nodes/apps/availability/convergence/... .csv)."""
    runs = report_runs(runs)
    try:
        raw = json.dumps(runs[0] if len(runs) == 1 else runs, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise ValueError("report data must contain finite JSON values") from error
    files = {"flows.csv": [], "flow_summary.csv": [], "links.csv": [], "nodes.csv": [], "apps.csv": [],
             "availability.csv": [], "convergence.csv": [], "route_events.csv": [], "events.csv": []}
    for r in runs:
        rid = r.get("run_id") or ""
        for fid, f in (r.get("flows") or {}).items():
            s = f.get("summary") or {}
            files["flow_summary.csv"].append([rid, fid, f.get("type"), f.get("src"),
                                              json.dumps(f.get("dst")) if isinstance(f.get("dst"), list) else f.get("dst"),
                                              s.get("avg_thr_bps"), s.get("avg_delay_ms"), s.get("avg_jitter_ms"),
                                              s.get("loss_pct"), s.get("avg_rtt_ms"), bool(f.get("hidden"))])
            for p in f.get("series") or []:
                files["flows.csv"].append([rid, fid, p.get("t"), p.get("thr_bps"), p.get("delay_ms"), p.get("jitter_ms"),
                                           p.get("loss_pct"), p.get("rtt_ms"), p.get("mos")])
        for lid, pts in (r.get("links") or {}).items():
            for p in pts or []:
                files["links.csv"].append([rid, lid, p.get("t"), p.get("ab_bps"), p.get("ba_bps"), p.get("util"),
                                           p.get("queue_ab"), p.get("queue_ba"), p.get("drops_ab"), p.get("drops_ba")])
        for dv, pts in (r.get("nodes") or {}).items():
            for p in pts or []:
                files["nodes.csv"].append([rid, dv, p.get("t"), p.get("routes"), p.get("fwd_pps"), p.get("rx_pps"),
                                           p.get("tx_pps"), p.get("noroute"), p.get("discards")])
        for aid, a in (r.get("apps") or {}).items():
            files["apps.csv"].append([rid, aid, a.get("type"), json.dumps({k: v for k, v in a.items() if k != "type"},
                                                                          ensure_ascii=False, default=str)])
        for fid, a in ((r.get("availability") or {}).get("flows") or {}).items():
            files["availability.csv"].append([rid, fid, a.get("availability_pct"), a.get("downtime_s"),
                                              len(a.get("outages") or [])])
        for c in r.get("convergence") or []:
            files["convergence.csv"].append([rid, c.get("t"), c.get("action"), c.get("link"), c.get("converged_s"),
                                             " ".join(c.get("devices") or [])])
        for ev in r.get("route_events") or []:
            files["route_events.csv"].append([rid, ev.get("t"), ev.get("dev"), " | ".join(ev.get("added") or []),
                                              " | ".join(ev.get("removed") or [])])
        for ev in r.get("events") or []:
            files["events.csv"].append([rid, ev.get("t"), ev.get("action"), ev.get("link") or ev.get("device"),
                                        ev.get("source") or ""])
    headers = {
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
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, rows in files.items():
            if rows or name in ("flows.csv", "flow_summary.csv"):
                z.writestr(name, _csv(rows, headers[name]))
        z.writestr("results.json", raw)
        for index, run in enumerate(runs):
            prefix = "" if len(runs) == 1 else f"runs/{index + 1:02d}/"
            payloads = replay_inputs(run)
            if payloads is None:
                if run.get("reproducibility") is not None:
                    z.writestr(prefix+"replay-unavailable.txt", "Recorded inputs do not establish a replay request.")
                continue
            for name, payload in payloads.items():
                z.writestr(prefix + name, json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
    return buf.getvalue()
