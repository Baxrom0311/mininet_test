"""Synthetic export allocation benchmark; excludes the already loaded input."""

import argparse
from datetime import datetime, timezone
import gc
import hashlib
import io
import json
from pathlib import Path
import platform
import tempfile
import time
import tracemalloc
import zipfile

from lab.report_export import csv_bundle, write_csv_bundle


def fixture(flows=20, points=2000):
    if (type(flows) is not int or type(points) is not int or not 1 <= flows <= 1000
            or not 1 <= points <= 100000 or flows * points > 1000000):
        raise ValueError("flows: 1..1000; points: 1..100000; total samples <= 1000000")
    return {"run_id": "synthetic", "engine": "analytic", "status": "done", "duration": points,
            "notes": ["Synthetic export allocation fixture; not simulator traffic"],
            "links": {"edge": [{"t": 1, "util": .2, "ab_bps": 1000000}]},
            "nodes": {"r1": [{"t": 1, "routes": 2**64-1}]},
            "apps": {"web": {"type": "http", "label": "O‘zbekcha, quote\"\ntext", "avg_page_s": .5}},
            "availability": {"flows": {"f0": {"availability_pct": 99, "downtime_s": 1, "outages": [{}]}}},
            "convergence": [{"t": 1, "devices": ["r1"], "converged_s": .5}],
            "route_events": [{"t": 1, "dev": "r1", "added": ["::/0"], "removed": []}],
            "events": [{"t": 1, "action": "link_down", "link": "edge"}],
            "flows": {f"f{index}": {"type": "udp", "src": "h1", "dst": "h2", "summary": {"avg_thr_bps": 1000000},
                "series": [{"t": t+1, "thr_bps": 1000000+index*100+t, "delay_ms": 1.5, "loss_pct": 0} for t in range(points)]}
                for index in range(flows)}}


def fingerprints(source):
    """Compare uncompressed entry content, independent of ZIP timestamps/headers."""
    if isinstance(source, bytes):
        source = io.BytesIO(source)
    else:
        source.seek(0)
    entries = {}
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            digest = hashlib.sha256()
            with archive.open(info) as stream:
                while block := stream.read(65536):
                    digest.update(block)
            entries[info.filename] = {"bytes": info.file_size, "sha256": digest.hexdigest()}
    return entries


def benchmark(flows=20, points=2000, mode="bytes", *, exporter=csv_bundle):
    if mode not in ("bytes", "file"):
        raise ValueError("mode: bytes | file")
    run = fixture(flows, points)

    def produce():
        if mode == "bytes":
            return exporter(run)
        target = tempfile.TemporaryFile()
        try:
            write_csv_bundle(run, target)
            return target
        except BaseException:
            target.close()
            raise

    def close(result):
        if not isinstance(result, bytes):
            result.close()

    gc.collect()
    start = time.perf_counter()
    result = produce()
    elapsed = time.perf_counter()-start
    try:
        size = len(result) if isinstance(result, bytes) else result.tell()
        entries = fingerprints(result)
    finally:
        close(result)
        del result
    gc.collect()
    if tracemalloc.is_tracing():
        raise ValueError("run allocation benchmark without an existing tracemalloc session")
    tracemalloc.start()
    try:
        result = produce()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    close(result)
    return {"kind": "synthetic ZIP export; not Mininet/ns-3 throughput", "measured_at": datetime.now(timezone.utc).isoformat(),
            "python": platform.python_version(), "flows": flows, "points_per_flow": points, "samples": flows*points,
            "target": mode, "zip_bytes": size, "wall_s": elapsed, "traced_peak_bytes": peak, "entries": entries,
            "notes": ["Input fixture allocation and archive inspection are excluded from the measured peak.",
                      "Wall time is measured in a separate untraced export; allocation measurement uses a second export.",
                      "tracemalloc is Python allocation tracing, not total process RSS or complete native memory accounting.",
                      "The bytes target includes final ZIP bytes; the file target writes directly to a temporary file."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flows", type=int, default=20)
    parser.add_argument("--points", type=int, default=2000)
    parser.add_argument("--mode", choices=("bytes", "file"), default="bytes")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        measured = benchmark(args.flows, args.points, args.mode)
    except ValueError as error:
        parser.error(str(error))
    text = json.dumps(measured, indent=2)
    if args.output:
        args.output.write_text(text+"\n")
    print(text)


if __name__ == "__main__":
    main()
