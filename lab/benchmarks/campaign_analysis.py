"""Compare independent per-pair analysis with request-local cohort preparation."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import statistics
import tempfile
import time
from unittest.mock import patch

from lab import analysis_windows
from lab import experiment_comparison as comparison
from lab.campaign_report import compact_report
from lab.drivers.base import EventBus
from lab.drivers.demo_driver import DemoDriver
from lab.engines import manager
from lab.engines.run_queue import QueuedRunManager
from lab.reproducibility import digest
from lab.topogen import generate


def independent_reference(campaigns, project, cid):
    """Pre-optimization orchestration for completed fixtures, using the public validated pair API."""
    _, status, known = campaigns._snapshot(project, cid)
    plan, _ = campaigns._read(project, cid, full=True)
    if not status["complete"]:
        raise ValueError("reference benchmark requires a completed campaign")
    options = {k: plan["window"][k] for k in ("start_s", "end_s")} if plan["window"] else {}
    cohorts, experiments, comparisons = {}, [], []
    for c in plan["conditions"]:
        records = [campaigns.queue.get(project, rid) for rid in c["run_ids"] if rid in known]
        cohorts[c["key"]] = records
        experiments.append({"variant": c["key"], "summary": comparison.summarize(records, **options), "error": None})
    for c in plan["conditions"][1:]:
        result = comparison.compare(cohorts["baseline"], cohorts[c["key"]], **options)
        comparisons.append({"variant": c["key"], "result": result, "error": None})
    return {"experiments": experiments, "comparisons": comparisons}


def measure(action, samples=3):
    summarize, analyze, conditions = comparison.summarize, analysis_windows.analyze, comparison._conditions
    times, counts, result = [], [], None
    for _ in range(samples):
        with patch.object(comparison, "summarize", wraps=summarize) as summary_calls, \
                patch.object(analysis_windows, "analyze", wraps=analyze) as window_calls, \
                patch.object(comparison, "_conditions", wraps=conditions) as guard_calls:
            start = time.perf_counter()
            result = action()
            times.append(time.perf_counter() - start)
            counts.append({"cohort_summaries": summary_calls.call_count, "window_analyses": window_calls.call_count,
                           "runtime_guards": guard_calls.call_count})
    return {"median_s": statistics.median(times), "samples_s": times, "calls": counts}, result


def json_bytes(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())


def benchmark(flows=60, duration=60, variants=4, repetitions=3, samples=3):
    runs = (variants + 1) * repetitions
    if (type(flows) is not int or not 1 <= flows <= 300 or not 4 <= duration <= 180 or not 1 <= variants <= 8
            or not 2 <= repetitions <= 10 or runs > 100 or flows * duration * runs > 1000000 or not 1 <= samples <= 5):
        raise ValueError("fixture limits: flows 1..300, duration 4..180, variants 1..8, reps 2..10, samples 1..5, <=1M points")
    project = generate("ring", routers=3, hosts=1, flows=1)
    project["name"] = "analysis_benchmark"
    project["scenarios"] = {"test": {"duration": duration, "events": [], "apps": [
        {"id": f"f{i}", "type": "udp", "src": "h0x0", "dst": "h2x0", "rate": "1Mbps", "start": 0, "stop": duration}
        for i in range(flows)]}}
    link = next(k for k, l in project["links"].items() if "h0x0" in (l["a"]["dev"], l["b"]["dev"]))
    project["links"][link]["params"]["bw"] = 20
    with tempfile.TemporaryDirectory(prefix="netlab-analysis-") as td, patch.object(manager, "RESULTS_DIR", td):
        bus = EventBus()
        queue = QueuedRunManager(bus, DemoDriver(bus))
        try:
            plan = queue.campaigns.start({"project": project, "scenario": "test", "param": {"kind": "link", "link": link, "field": "bw"},
                "values": [40 + 20 * i for i in range(variants)], "repetitions": repetitions, "seed": 7,
                "start_s": duration / 4, "end_s": duration})
            deadline = time.monotonic() + 120
            while queue.futures:
                if time.monotonic() > deadline:
                    raise RuntimeError("fixture generation timed out")
                time.sleep(.02)
            cid, name = plan["campaign_id"], project["name"]
            old, reference = measure(lambda: independent_reference(queue.campaigns, name, cid), samples)
            new, report = measure(lambda: queue.campaigns.analysis(name, cid)[1], samples)
            actual = {k: report[k] for k in ("experiments", "comparisons")}
            if digest(actual) != digest(reference):
                raise AssertionError("optimized results differ from independent pairwise analysis")
            full_size, compact_size = json_bytes(report), json_bytes(compact_report(report))
            size = sum(p.stat().st_size for p in Path(td, name).glob("*.json"))
            return {"kind": "analytic campaign report processing; not emulation or packet throughput",
                    "measured_at": datetime.now(timezone.utc).isoformat(), "runtime": {"python": platform.python_version(),
                    "system": platform.system(), "machine": platform.machine()},
                    "fixture": {"flows": flows, "duration": duration, "variants": variants, "repetitions": repetitions,
                                "runs": runs, "result_json_bytes": size}, "independent_reference": old, "request_local": new,
                    "speedup": old["median_s"] / new["median_s"], "identical_report_values": True,
                    "full_json_bytes": full_size, "compact_json_bytes": compact_size,
                    "compact_reduction_pct": 100 * (full_size - compact_size) / full_size,
                    "notes": ["Generation is excluded; both paths read the same completed files in the same process.",
                              "Reference repeats cohort summaries and public pair comparisons, matching earlier orchestration.",
                              "Only one request reuses preparation; later requests reread/revalidate inputs.",
                              "Sizes are UTF-8 JSON before compression; timings depend on OS cache, CPU and filesystem."]}
        finally:
            queue.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name, default in (("flows", 60), ("duration", 60), ("variants", 4), ("repetitions", 3), ("samples", 3)):
        parser.add_argument("--" + name, type=int, default=default)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = benchmark(args.flows, args.duration, args.variants, args.repetitions, args.samples)
    raw = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(raw + "\n")
    print(raw)


if __name__ == "__main__":
    main()
