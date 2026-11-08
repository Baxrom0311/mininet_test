"""Synthetic HTML display benchmark; no simulator or root privileges needed."""

import argparse
from datetime import datetime, timezone
import gc
import hashlib
import json
from pathlib import Path
import time
import tracemalloc

from lab.report import run_report


def fixture(points=100000):
    if type(points) is not int or not 10 <= points <= 1000000:
        raise ValueError("points: integer 10..1000000")
    series = [{"t": i+1, "thr_bps": 1000000, "delay_ms": 2, "loss_pct": 0} for i in range(points)]
    series[points//3]["thr_bps"] = 9000000
    series[points//2].update(thr_bps=0, delay_ms=20, loss_pct=100)
    series[2*points//3].update(thr_bps=None, delay_ms=None, loss_pct=None)
    return {"run_id": "display", "engine": "analytic", "status": "done", "duration": points,
            "scenario_name": "Synthetic spike, outage and missing sample", "created": 1,
            "flows": {"signal": {"type": "udp", "src": "h1", "dst": "h2",
                "summary": {"avg_thr_bps": 1000000, "avg_delay_ms": 2, "loss_pct": 0}, "series": series}}}


def _digest(run):
    return hashlib.sha256(json.dumps(run, sort_keys=True, allow_nan=False).encode()).hexdigest()


def benchmark(points=100000, *, renderer=run_report):
    run = fixture(points)
    before = _digest(run)
    gc.collect()
    start = time.perf_counter()
    html = renderer(run)
    elapsed = time.perf_counter()-start
    size = len(html.encode("utf-8"))
    del html
    gc.collect()
    if tracemalloc.is_tracing():
        raise ValueError("run benchmark without an existing tracemalloc session")
    tracemalloc.start()
    try:
        html = renderer(run)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    unchanged = before == _digest(run)
    if not unchanged:
        raise ValueError("report renderer mutated its input")
    return {"kind": "synthetic HTML display; not simulator throughput", "measured_at": datetime.now(timezone.utc).isoformat(),
            "points_per_metric": points, "metrics": 3, "html_bytes": size, "wall_s": elapsed,
            "traced_peak_bytes": peak, "input_unchanged": unchanged,
            "figures": html.count('<figure'), "polylines": html.count('<polyline'),
            "notes": ["Input construction and content hashing are excluded from allocation tracing.",
                      "Timing is a separate untraced render; peak is from a second render.",
                      "tracemalloc is Python allocation tracing, not total process RSS.",
                      "Reduction applies only to displayed SVG samples; raw results and statistics are unchanged."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--points", type=int, default=100000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        measured = benchmark(args.points)
    except ValueError as error:
        parser.error(str(error))
    text = json.dumps(measured, indent=2)
    if args.output:
        args.output.write_text(text+"\n")
    print(text)


if __name__ == "__main__":
    main()
