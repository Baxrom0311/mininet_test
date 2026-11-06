"""Persist every real calibration attempt; never turn partial success into exit 0."""

import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import tempfile
import time
import uuid

FIELDS = ["protocol", "metric", "unit", "model", "real", "source", "verified", "abs_tolerance", "rel_tolerance"]


def _atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as file:
            file.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _save(output, report_path, report, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    content = stream.getvalue()
    report.update(rows=len(rows), csv_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest())
    _atomic(output, content)
    _atomic(report_path, json.dumps(report, indent=2, allow_nan=False) + "\n")


def collect(driver, measures, expected, cases, reps, output, report_path, host, log=print):
    """Measurements are supplied by the real driver; tests use explicit synthetic callables."""
    cases = list(cases)
    if (type(reps) is not int or not 1 <= reps <= 1000 or not cases or len(set(cases)) != len(cases)
            or any(name not in measures or not expected.get(name) for name in cases)):
        raise ValueError("nonempty unique known cases and reps 1..1000 are required")
    output, report_path = Path(output), Path(report_path)
    if output.resolve() == report_path.resolve():
        raise ValueError("CSV and collection report paths must differ")
    run = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex
    report = {"schema_version": 1, "run_id": run, "host": host, "status": "running",
              "measurement_method": "event_before_command_v2", "probe_interval_s": .1,
              "cases": list(cases), "replications": reps, "planned_attempts": len(cases) * reps,
              "attempts": [], "stopped_early": False, "defaults_changed": False}
    rows = []
    _save(output, report_path, report, rows)
    for name in cases:
        for rep in range(1, reps + 1):
            attempt = {"case": name, "rep": rep, "status": "failed", "metrics": []}
            started = time.monotonic()
            try:
                log(f"== {name} #{rep}")
                caps = driver.capabilities()
                result = list(measures[name](driver))
                keys = [(r[0], r[1]) for r in result]
                if len(set(keys)) != len(keys) or set(keys) != set(expected[name]):
                    raise ValueError("collector returned missing, duplicate or unexpected metric keys")
                staged, missing = [], False
                for proto, metric, model, real, abs_t, rel_t in result:
                    values = (model, abs_t, rel_t) if real is None else (model, real, abs_t, rel_t)
                    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values):
                        raise ValueError(f"invalid numeric measurement: {proto}:{metric}")
                    attempt["metrics"].append({"protocol": proto, "metric": metric, "unit": "s", "value": real,
                                               "status": "missing" if real is None else "measured"})
                    if real is None:
                        missing = True
                        continue
                    staged.append({"protocol": proto, "metric": metric, "unit": "s", "model": round(model, 4),
                                   "real": round(real, 4), "verified": "true",
                                   "source": f"host={host} datapath={caps.get('ovs_datapath')} netem={caps.get('netem')} "
                                             f"run={run} method=event_before_command_v2 test={name} rep={rep}",
                                   "abs_tolerance": abs_t, "rel_tolerance": rel_t})
                rows.extend(staged)
                attempt["status"] = "incomplete" if missing else "passed"
            except Exception as error:
                attempt["error"] = f"{type(error).__name__}: {error}"
                log(f"   XATO: {attempt['error']}")
            except (KeyboardInterrupt, SystemExit):
                attempt["status"] = report["status"] = "interrupted"
                report["stopped_early"] = True
                raise
            finally:
                try:
                    driver.stop()
                except Exception as error:
                    attempt.update(status="failed", cleanup_error=f"{type(error).__name__}: {error}")
                    report["stopped_early"] = True
                attempt["wall_s"] = round(time.monotonic() - started, 4)
                report["attempts"].append(attempt)
                _save(output, report_path, report, rows)
            if report["stopped_early"]:
                break
        if report["stopped_early"]:
            break
    complete = len(report["attempts"]) == report["planned_attempts"] and all(a["status"] == "passed" for a in report["attempts"])
    report["status"] = "passed" if complete else "failed"
    _save(output, report_path, report, rows)
    log(f"{len(rows)} o'lchov -> {output}; collection={report['status']} -> {report_path}")
    return report
