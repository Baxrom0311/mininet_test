"""Measure generated FRR/Mininet networks on a dedicated Linux host.

    sudo python3 -m lab.benchmarks.real_scale --routers 10 20 40 --output /tmp/scale.json

Requires the real driver prerequisites. Serializes real-network tests with flock;
Mininet cleanup is global, so never run against someone else's live lab. Exit 2
means blocked prerequisites, exit 1 means a failed check/budget, not a measurement
of the host's maximum supported topology. Capture is off to avoid pcap I/O noise.
"""

import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import signal
import tempfile
import time
import uuid

from lab.real_lock import serial_real_test
from lab.benchmarks.scale_resources import Sampler, process_usage  # public compatibility imports
from lab.topogen import generate


def _error(error):
    return f"{type(error).__name__}: {error}"


def _save(path, report):
    """Replace a complete JSON checkpoint; an interrupted write preserves its predecessor."""
    content = json.dumps(report, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _validate_traffic(traffic, apps):
    flows = traffic.get("flows", {})
    if set(flows) != {app["id"] for app in apps} or not flows:
        raise RuntimeError("traffic report does not contain every configured flow")
    for flow in flows.values():
        rate = flow.get("summary", {}).get("avg_thr_bps")
        if type(rate) not in (int, float) or not math.isfinite(rate) or rate <= 0:
            raise RuntimeError("traffic did not reach all configured destinations with finite positive throughput")
    json.dumps(traffic, allow_nan=False)  # Validate other metrics before retaining them in a checkpoint.


def prerequisites():
    import shutil
    missing = []
    if os.geteuid() != 0:
        missing.append("root required for network namespaces and FRR")
    if importlib.util.find_spec("mininet") is None:
        missing.append("Python mininet package missing")
    for tool in ("mnexec", "ovs-vsctl", "vtysh", "iperf3", "ping"):
        if not shutil.which(tool):
            missing.append(f"{tool} missing")
    for daemon in ("zebra", "ospfd"):
        if not (Path("/usr/lib/frr") / daemon).is_file() and not shutil.which(daemon):
            missing.append(f"FRR {daemon} missing")
    return missing


def measure(routers, args):
    from lab.drivers.base import EventBus
    from lab.drivers.mininet_driver import MininetDriver
    from lab.engines import emulation
    p = generate("ring", routers=routers, hosts=args.hosts, flows=args.flows)
    sc = p["scenarios"]["asosiy"]
    sc["duration"] = args.duration
    for a in sc["apps"]:
        a.update(start=1, stop=args.duration)
    driver = MininetDriver(EventBus())
    driver.capture_on = False
    result = {"routers": routers, "devices": len(p["devices"]), "links": len(p["links"]), "status": "error"}
    started = time.monotonic()
    with Sampler(driver) as sampler:
        try:
            sampler.check()
            signal.alarm(args.timeout)
            startup_started = time.monotonic()
            driver.start(p)
            result["startup_s"] = round(time.monotonic() - startup_started, 3)
            sampler.sample()  # Ensure short runs include a sample while the network is alive.
            sampler.check()
            hosts = [n for n, d in p["devices"].items() if d["type"] == "host"]
            if len(hosts) < 2:
                raise ValueError("at least two hosts are required")
            target = p["devices"][hosts[-1]]["config"]["interfaces"][f"{hosts[-1]}-eth1"]["ip"].split("/")[0]
            deadline = time.monotonic() + args.settle
            while " 0% packet loss" not in driver.net.get(hosts[0]).cmd(f"ping -c 1 -W 1 {target}"):
                if time.monotonic() >= deadline:
                    raise RuntimeError("OSPF end-to-end convergence timed out")
                time.sleep(0.5)
            result["ready_s"] = round(time.monotonic() - started, 3)
            neighbors = {}
            for n, d in p["devices"].items():
                if d["type"] == "router":
                    text = driver.net.get(n).cmd(f"vtysh -N {n} -c 'show ip ospf neighbor'")
                    neighbors[n] = text.count("Full/")
            result["full_neighbors"] = neighbors
            expected = {n: sum(n in (l["a"]["dev"], l["b"]["dev"])
                               and all(p["devices"][l[e]["dev"]]["type"] == "router" for e in ("a", "b"))
                               for l in p["links"].values()) for n in neighbors}
            result["expected_full_neighbors"] = expected
            if any(neighbors[n] < expected[n] for n in neighbors):
                raise RuntimeError("not every configured router-router adjacency is Full")
            traffic = emulation.run(driver, sc)
            _validate_traffic(traffic, sc["apps"])
            result["traffic"] = traffic
            core_link = next(lid for lid, l in p["links"].items()
                             if all(p["devices"][l[e]["dev"]]["type"] == "router" for e in ("a", "b")))
            failure_time = time.monotonic()
            driver.update_link(core_link, up=False)
            while " 0% packet loss" not in driver.net.get(hosts[0]).cmd(f"ping -c 1 -W 1 {target}"):
                if time.monotonic() - failure_time > args.settle:
                    raise RuntimeError("OSPF alternate-path recovery timed out")
                time.sleep(0.5)
            result["link_failure_probe_s"] = round(time.monotonic() - failure_time, 3)
            result["link_failure_probe_method"] = "before_link_command_v2"
            result["failed_link"] = core_link
            driver.update_link(core_link, up=True)
            result["status"] = "passed"
        except Exception as e:
            result["error"] = _error(e)
        except (KeyboardInterrupt, SystemExit) as e:
            result.update(status="interrupted", error=_error(e))
        finally:
            signal.alarm(0)
            sampler.sample()
            try:
                driver.stop()
            except (Exception, KeyboardInterrupt, SystemExit) as e:
                result["cleanup_error"] = _error(e)
                result.setdefault("error", "cleanup failed: " + _error(e))
                result["status"] = "interrupted" if isinstance(e, (KeyboardInterrupt, SystemExit)) or result["status"] == "interrupted" else "error"
    result["resources"] = sampler.summary()
    if not result["resources"]["valid"]:
        result["resource_error"] = "resource evidence incomplete; inspect resources.sampling_errors"
        if result["status"] == "passed":
            result.update(status="error", error=result["resource_error"])
    result["rss_budget_exceeded"] = result["resources"]["peak_rss_bytes"] > args.rss_budget_mb * 1024 ** 2
    if result["rss_budget_exceeded"] and result["status"] == "passed":
        result.update(status="budget_exceeded", error="sampled process RSS exceeds configured budget")
    result["wall_s"] = round(time.monotonic() - started, 3)
    return result


@serial_real_test
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--routers", nargs="+", type=int, default=[10, 20, 40])
    ap.add_argument("--hosts", type=int, default=1)
    ap.add_argument("--flows", type=int, default=4)
    ap.add_argument("--duration", type=int, default=10)
    ap.add_argument("--settle", type=int, default=90)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--rss-budget-mb", type=int, default=2048)
    ap.add_argument("--output", type=Path, required=True)
    a = ap.parse_args(argv)
    if (len(set(a.routers)) != len(a.routers) or any(n < 3 or n > 200 for n in a.routers) or not 1 <= a.hosts <= 20 or a.flows < 1
            or a.duration < 3 or a.settle < 1 or a.timeout < 1 or a.rss_budget_mb < 1):
        ap.error("unique routers 3..200; hosts 1..20; flows >= 1; duration >= 3; settle/timeout/budget >= 1")
    missing = prerequisites()
    report = {"schema_version": 2, "run_id": uuid.uuid4().hex, "status": "blocked" if missing else "running",
              "platform": platform.platform(), "cpu_count": os.cpu_count(),
              "missing": missing, "runs": [], "maximum_capacity_established": False,
              "planned_routers": a.routers, "active_routers": None, "stopped_early": False,
              "settings": {k: v for k, v in vars(a).items() if k != "output"}}
    import resource
    report["limits"] = {"nofile": list(resource.getrlimit(resource.RLIMIT_NOFILE)),
                        "affinity_cpus": len(os.sched_getaffinity(0))}
    for name in ("memory.max", "pids.max", "cpu.max"):
        path = Path("/sys/fs/cgroup") / name
        if path.is_file():
            report["limits"][name] = path.read_text().strip()
    _save(a.output, report)
    if not missing:
        def timeout(_sig, _frame):
            raise TimeoutError("benchmark timeout")
        def interrupted(signum, _frame):
            raise KeyboardInterrupt(f"signal {signum}")
        previous_handler = signal.signal(signal.SIGALRM, timeout)
        previous_term = signal.signal(signal.SIGTERM, interrupted)
        try:
            for n in a.routers:
                report["active_routers"] = n
                _save(a.output, report)
                try:
                    result = measure(n, a)
                except Exception as error:
                    result = {"routers": n, "status": "error", "error": _error(error)}
                except (KeyboardInterrupt, SystemExit) as error:
                    result = {"routers": n, "status": "interrupted", "error": _error(error)}
                report["runs"].append(result)
                report["active_routers"] = None
                if result["status"] != "passed":
                    report["stopped_early"] = True
                    report["status"] = "interrupted" if result["status"] == "interrupted" else "failed"
                _save(a.output, report)
                if report["stopped_early"]:
                    break
            if not report["stopped_early"]:
                report["status"] = "passed"
        except (KeyboardInterrupt, SystemExit) as error:
            report.update(status="interrupted", stopped_early=True, error=_error(error))
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous_handler)
            signal.signal(signal.SIGTERM, previous_term)
    _save(a.output, report)
    print(json.dumps({"status": report["status"], "missing": missing, "output": str(a.output)}))
    return 2 if missing else 130 if report["status"] == "interrupted" else int(report["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
