"""Synthetic analytical TCP correctness benchmark; never a host/network capacity measurement."""

import argparse
import json
import math
from pathlib import Path
import time

from lab.engines import analytic
from lab.topogen import generate


def measure(flows):
    report = {"schema_version": 1, "benchmark": "analytic_tcp", "evidence_kind": "synthetic_fluid_model",
              "real_network_measurement": False, "flows_per_case": flows, "duration_s": 1, "cases": []}
    for name in ("finite_files", "rate_cap_levels"):
        project = generate("ring", routers=3, hosts=1, flows=0)
        bandwidth = max(10000, .008 * flows * (flows + 1)) if name == "finite_files" else flows * (flows + 1) / 2 + 1
        for link in project["links"].values():
            link["params"]["bw"] = bandwidth
        apps = [{"id": f"f{i}", "type": "tcp", "src": "h0x0", "dst": "h2x0", "start": 0, "stop": 1,
                 **({"bytes": 1000 * i} if name == "finite_files" else {"rate": f"{i}Mbps"})}
                for i in range(1, flows + 1)]
        case = {"case": name, "status": "failed", "expected_flows": flows, "link_capacity_mbps": bandwidth}
        started = time.monotonic()
        try:
            result = analytic.run(project, {"duration": 1, "apps": apps, "events": []})
            records = result["flows"]
            if set(records) != {app["id"] for app in apps}:
                raise ValueError("result must contain every requested flow exactly once")
            if name == "finite_files":
                delivered, matched, complete = [], 0, 0
                for app in apps:
                    flow = records[app["id"]]
                    total = math.fsum(p["thr_bps"] * (p["t_end"] - p["t_start"]) / 8 for p in flow["series"])
                    if not math.isfinite(total):
                        raise ValueError("nonfinite delivered bytes")
                    delivered.append(total)
                    matched += abs(total - app["bytes"]) <= .125
                    done = flow.get("done_t")
                    complete += type(done) in (int, float) and math.isfinite(done) and 0 <= done <= 1
                case.update(requested_bytes=sum(app["bytes"] for app in apps), delivered_bytes=math.fsum(delivered),
                            completed_flows=complete, byte_matched_flows=matched, tolerance_bytes_per_flow=.125)
                passed = complete == matched == flows
            else:
                measured, matched = [], 0
                for i, app in enumerate(apps, 1):
                    value = records[app["id"]]["summary"]["avg_thr_bps"]
                    if type(value) not in (int, float) or not math.isfinite(value):
                        raise ValueError("nonfinite measured rate")
                    measured.append(value)
                    matched += abs(value - i * 1000000) <= .5
                case.update(expected_bps=flows * (flows + 1) // 2 * 1000000,
                            measured_bps=math.fsum(measured), rate_matched_flows=matched, tolerance_bps_per_flow=.5)
                passed = matched == flows
            case["status"] = "passed" if passed else "failed"
        except Exception as error:
            case["error"] = f"{type(error).__name__}: {error}"
        case["wall_s"] = round(time.monotonic() - started, 6)
        report["cases"].append(case)
    report["status"] = "passed" if all(case["status"] == "passed" for case in report["cases"]) else "failed"
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flows", type=int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not 1 <= args.flows <= 2000:
        parser.error("flows must be 1..2000")
    report = measure(args.flows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "evidence_kind": report["evidence_kind"], "output": str(args.output)}))
    return int(report["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
