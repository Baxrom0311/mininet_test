"""Sample benchmark/namespace processes and expose incomplete resource evidence."""

import os
from pathlib import Path
import threading
import time


def process_usage(pids, *, proc=Path("/proc"), on_error=None):
    """Return complete process reads only; ordinary process exits are expected races."""
    rss = fds = threads = 0
    ticks = {}
    page = os.sysconf("SC_PAGE_SIZE")
    for pid in pids:
        path = proc / str(pid)
        try:
            # comm can contain spaces and ')'; fields start after its last ')'.
            fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
            cpu, count, identity = int(fields[11]) + int(fields[12]), int(fields[17]), fields[19]
            resident = int((path / "statm").read_text().split()[1]) * page
            descriptors = len(list((path / "fd").iterdir()))
            if min(cpu, count, resident, int(identity)) < 0:
                raise ValueError("negative process counters")
            after = (path / "stat").read_text().rsplit(")", 1)[1].split()
            if after[19] != identity:
                continue  # PID was reused while reading it.
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (OSError, ValueError, IndexError) as error:
            if on_error is not None:
                on_error(f"process {pid}", error)
            continue
        ticks[(pid, identity)] = cpu
        rss += resident
        fds += descriptors
        threads += count
    return {"rss_bytes": rss, "fds": fds, "threads": threads, "processes": len(ticks)}, ticks


class Sampler:
    """Include daemonized FRR by network namespace; account for shared OVS separately."""

    def __init__(self, driver, interval=.5, *, proc=Path("/proc")):
        self.driver, self.interval = driver, interval
        self.proc = Path(proc)
        self.samples, self.last_ticks = [], {}
        self.cpu_seconds = 0.
        self.errors, self.error_count = [], 0
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.thread = threading.Thread(target=self._loop, name="netlab-scale-resources", daemon=True)

    def _error(self, where, error):
        self.error_count += 1
        if len(self.errors) < 20:
            self.errors.append(f"{where}: {type(error).__name__}: {error}")

    def _pids(self):
        namespaces = set()
        net = self.driver.net
        if net:
            for node in list(net.hosts):
                if getattr(node, "inNamespace", False):
                    try:
                        namespaces.add(os.stat(self.proc / str(node.pid) / "ns/net").st_ino)
                    except (FileNotFoundError, ProcessLookupError):
                        continue
                    except OSError as error:
                        self._error(f"namespace {node.pid}", error)
        owned, ovs = {os.getpid()}, set()
        for path in self.proc.iterdir():
            if not path.name.isdigit():
                continue
            try:
                if namespaces and os.stat(path / "ns/net").st_ino in namespaces:
                    owned.add(int(path.name))
                if (path / "comm").read_text().strip() in ("ovs-vswitchd", "ovsdb-server"):
                    ovs.add(int(path.name))
            except (FileNotFoundError, ProcessLookupError):
                continue
            except OSError as error:
                self._error(f"discovery {path.name}", error)
        # Shared OVS must never be included twice, even with an unusual namespace configuration.
        return owned - ovs, ovs

    def sample(self):
        with self.lock:
            try:
                owned, ovs = self._pids()
                usage, ticks = process_usage(owned, proc=self.proc, on_error=self._error)
                self.cpu_seconds += sum(max(t - self.last_ticks.get(key, 0 if self.samples else t), 0)
                                        for key, t in ticks.items()) / os.sysconf("SC_CLK_TCK")
                # Retain identities across a temporarily missed sample; do not count their CPU twice.
                self.last_ticks.update(ticks)
                ovs_usage, _ = process_usage(ovs, proc=self.proc, on_error=self._error)
                self.samples.append({"monotonic": time.monotonic(), **usage,
                                     "cpu_seconds": self.cpu_seconds,
                                     "shared_ovs_rss_bytes": ovs_usage["rss_bytes"]})
            except Exception as error:
                self._error("sample", error)

    def check(self):
        with self.lock:
            if self.error_count or not self.samples:
                raise RuntimeError("resource evidence incomplete: " + (self.errors[0] if self.errors else "no samples"))

    def _loop(self):
        while not self.stop.wait(self.interval):
            self.sample()

    def __enter__(self):
        self.sample()
        try:
            self.thread.start()
        except Exception as error:
            self._error("sampler startup", error)
        return self

    def __exit__(self, *args):
        self.stop.set()
        if self.thread.ident is not None:
            self.thread.join()
        self.sample()

    def summary(self):
        with self.lock:
            keys = ("rss_bytes", "fds", "threads", "processes", "shared_ovs_rss_bytes")
            return {"peak_" + k: max((s[k] for s in self.samples), default=0) for k in keys} | {
                "cpu_seconds": round(self.cpu_seconds, 3), "samples": len(self.samples),
                "sample_interval_s": self.interval, "valid": bool(self.samples) and not self.error_count,
                "sampling_error_count": self.error_count, "sampling_errors": list(self.errors),
                "scope": "benchmark + namespace processes; shared OVS RSS reported separately; sampled, not exact HWM"}
