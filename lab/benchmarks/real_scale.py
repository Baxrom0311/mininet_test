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
import os
from pathlib import Path
import platform
import signal
import threading
import time

from lab.topogen import generate


def process_usage(pids):
    """Read aggregate RSS, CPU ticks, FDs and threads for an explicit PID set."""
    rss = fds = threads = 0
    ticks = {}
    page = os.sysconf("SC_PAGE_SIZE")
    for pid in pids:
        try:
            # comm may contain spaces/parentheses; fields start after the last ')'.
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            ticks[(pid, fields[19])] = int(fields[11]) + int(fields[12])
            rss += int(Path(f"/proc/{pid}/statm").read_text().split()[1]) * page
            threads += int(fields[17])
            fds += len(list(Path(f"/proc/{pid}/fd").iterdir()))
        except (OSError, ValueError, IndexError):
            continue  # process exited between reads
    return {"rss_bytes": rss, "fds": fds, "threads": threads, "processes": len(ticks)}, ticks


class Sampler:
    """Include daemonized FRR by its network namespace, not just its parent PID.

    OVS is shared with the host and excluded from these process totals. Its
    separate RSS is reported so it cannot silently disappear from accounting.
    """
    def __init__(self, driver, interval=0.5):
        self.driver, self.interval = driver, interval
        self.samples, self.last_ticks = [], {}
        self.cpu_seconds = 0.0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def _pids(self):
        namespaces = set()
        net = self.driver.net
        if net:
            for node in list(net.hosts):
                if getattr(node, "inNamespace", False):
                    try:
                        namespaces.add(os.stat(f"/proc/{node.pid}/ns/net").st_ino)
                    except OSError:
                        pass
        owned, ovs = {os.getpid()}, set()
        for p in Path("/proc").iterdir():
            if not p.name.isdigit():
                continue
            try:
                if os.stat(p / "ns/net").st_ino in namespaces:
                    owned.add(int(p.name))
                if (p / "comm").read_text().strip() in ("ovs-vswitchd", "ovsdb-server"):
                    ovs.add(int(p.name))
            except OSError:
                pass
        return owned, ovs

    def sample(self):
        owned, ovs = self._pids()
        usage, ticks = process_usage(owned)
        self.cpu_seconds += sum(max(t - self.last_ticks.get(key, 0 if self.samples else t), 0)
                                for key, t in ticks.items()) / os.sysconf("SC_CLK_TCK")
        self.last_ticks = ticks
        ovs_usage, _ = process_usage(ovs)
        self.samples.append({"monotonic": time.monotonic(), **usage,
                             "cpu_seconds": self.cpu_seconds, "shared_ovs_rss_bytes": ovs_usage["rss_bytes"]})

    def _loop(self):
        while not self.stop.is_set():
            self.sample()
            self.stop.wait(self.interval)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join()

    def summary(self):
        keys = ("rss_bytes", "fds", "threads", "processes", "shared_ovs_rss_bytes")
        return {"peak_" + k: max((s[k] for s in self.samples), default=0) for k in keys} | {
            "cpu_seconds": round(self.cpu_seconds, 3), "samples": len(self.samples),
            "sample_interval_s": self.interval,
            "scope": "benchmark + namespace processes; shared OVS RSS reported separately; sampled, not exact HWM"}


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
            signal.alarm(args.timeout)
            driver.start(p)
            result["startup_s"] = round(time.monotonic() - started, 3)
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
            result["traffic"] = emulation.run(driver, sc)
            summaries = [f["summary"] for f in result["traffic"]["flows"].values()]
            if not summaries or any(s["avg_thr_bps"] <= 0 for s in summaries):
                raise RuntimeError("traffic did not reach all configured destinations")
            core_link = next(lid for lid, l in p["links"].items()
                             if all(p["devices"][l[e]["dev"]]["type"] == "router" for e in ("a", "b")))
            driver.update_link(core_link, up=False)
            failure_time = time.monotonic()
            while " 0% packet loss" not in driver.net.get(hosts[0]).cmd(f"ping -c 1 -W 1 {target}"):
                if time.monotonic() - failure_time > args.settle:
                    raise RuntimeError("OSPF alternate-path recovery timed out")
                time.sleep(0.5)
            result["link_failure_probe_s"] = round(time.monotonic() - failure_time, 3)
            result["failed_link"] = core_link
            driver.update_link(core_link, up=True)
            result["status"] = "passed"
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
        finally:
            signal.alarm(0)
            driver.stop()
    result["resources"] = sampler.summary()
    if result["resources"]["peak_rss_bytes"] > args.rss_budget_mb * 1024 ** 2:
        result.update(status="budget_exceeded", error="sampled process RSS exceeds configured budget")
    result["wall_s"] = round(time.monotonic() - started, 3)
    return result


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
    if (any(n < 3 or n > 200 for n in a.routers) or not 1 <= a.hosts <= 20 or a.flows < 1
            or a.duration < 3 or a.settle < 1 or a.timeout < 1 or a.rss_budget_mb < 1):
        ap.error("routers 3..200; hosts 1..20; flows >= 1; duration >= 3")
    missing = prerequisites()
    report = {"schema_version": 1, "status": "blocked" if missing else "running",
              "platform": platform.platform(), "cpu_count": os.cpu_count(),
              "missing": missing, "runs": [], "maximum_capacity_established": False}
    import resource
    report["limits"] = {"nofile": list(resource.getrlimit(resource.RLIMIT_NOFILE)),
                        "affinity_cpus": len(os.sched_getaffinity(0))}
    for name in ("memory.max", "pids.max", "cpu.max"):
        path = Path("/sys/fs/cgroup") / name
        if path.is_file():
            report["limits"][name] = path.read_text().strip()
    if not missing:
        import fcntl
        with open("/tmp/netlab-real.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            def timeout(_sig, _frame):
                raise TimeoutError("benchmark timeout")
            signal.signal(signal.SIGALRM, timeout)
            for n in a.routers:
                report["runs"].append(measure(n, a))
                if report["runs"][-1]["status"] != "passed":
                    break
            report["status"] = "passed" if all(r["status"] == "passed" for r in report["runs"]) else "failed"
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "missing": missing, "output": str(a.output)}))
    return 2 if missing else int(report["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
