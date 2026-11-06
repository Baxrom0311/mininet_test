"""Synthetic result-history I/O benchmark; no network simulator or root needed."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import statistics
import tempfile
import time
from unittest.mock import patch

from lab.drivers.base import EventBus
from lab.drivers.demo_driver import DemoDriver
from lab.engines import manager
from lab.engines.run_queue import QueuedRunManager


def measure(action, repetitions=3):
    elapsed, reads = [], []
    real_load = json.load
    for _ in range(repetitions):
        with patch("json.load", wraps=real_load) as reader:
            start = time.perf_counter()
            action()
            elapsed.append(time.perf_counter() - start)
            reads.append(reader.call_count)
    return {"median_s": statistics.median(elapsed), "samples_s": elapsed, "full_json_reads": reads}


def benchmark(runs=100, points=10000):
    if not 1 <= runs <= 500 or not 1 <= points <= 20000 or runs * points > 2000000:
        raise ValueError("runs: 1..500; points: 1..20000; total points <= 2000000")
    series = [{"t": i, "thr_bps": 1234567, "delay_ms": 1.5, "loss_pct": 0} for i in range(points)]
    with tempfile.TemporaryDirectory(prefix="netlab-result-index-") as td, patch.object(manager, "RESULTS_DIR", td):
        project_dir = Path(td, "history")
        project_dir.mkdir()
        size = 0
        for i in range(runs):
            data = {"run_id": f"r{i:04d}", "project": "history", "status": "done", "created": i,
                    "engine": "analytic", "scenario_name": "synthetic", "flows": {"flow": {"series": series}}}
            path = project_dir / (data["run_id"] + ".json")
            path.write_text(json.dumps(data))
            size += path.stat().st_size
        bus = EventBus()
        queue = QueuedRunManager(bus, DemoDriver(bus))
        try:
            cold = measure(lambda: queue.list("history"), 1)
            warm = measure(lambda: queue.list("history"))
            paged = measure(lambda: queue.page("history", 20))
            # Same fixture and process; old list method reads every complete JSON.
            legacy = measure(lambda: manager.RunManager.list(queue, "history"))
        finally:
            queue.close()
        return {"kind": "synthetic result metadata I/O; not Mininet/ns-3 throughput",
                "measured_at": datetime.now(timezone.utc).isoformat(),
                "runtime": {"python": platform.python_version(), "system": platform.system(), "machine": platform.machine()},
                "runs": runs,
                "points_per_run": points, "json_bytes": size, "cold_index": cold,
                "warm_list": warm, "warm_page_20": paged, "legacy_list": legacy,
                "warm_speedup": legacy["median_s"] / warm["median_s"],
                "notes": ["OS page cache and filesystem affect timings; this is a local measurement.",
                          "Warm requests still stat every result file; directory work is O(number of results).",
                          "Cold indexing reads each legacy result once and writes metadata to SQLite."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--points", type=int, default=10000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = benchmark(args.runs, args.points)
    output = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(output + "\n")
    print(output)


if __name__ == "__main__":
    main()
