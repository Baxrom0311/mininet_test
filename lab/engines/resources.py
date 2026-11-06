"""Read effective CPU/RAM capacity, including Linux affinity and cgroup v2 limits."""

from dataclasses import asdict, dataclass
import math
import os
from pathlib import Path
import time

MIB = 1024 ** 2


def positive_integer(value, name, maximum=64):
    if isinstance(value, bool) or not str(value).strip().isdigit() or not 1 <= int(value) <= maximum:
        raise ValueError(f"{name}: 1..{maximum}")
    return int(value)


@dataclass(frozen=True)
class Resources:
    logical_cpus: int
    affinity_cpus: int
    effective_cpus: float
    memory_total_bytes: int | None
    memory_available_bytes: int | None
    cpu_utilization: float | None = None
    cpu_quota: float | None = None
    memory_limit_bytes: int | None = None

    def public(self):
        return asdict(self)


class ResourceMonitor:
    def __init__(self, proc=Path("/proc"), cgroup=Path("/sys/fs/cgroup"), clock=time.monotonic):
        self.proc, self.cgroup, self.clock = Path(proc), Path(cgroup), clock
        self.previous = None

    def _text(self, path):
        try:
            return path.read_text().strip()
        except OSError:
            return ""

    def _groups(self):
        root = self.cgroup.resolve()
        membership = self._text(self.proc / "self/cgroup")
        relative = next((line[3:] for line in membership.splitlines() if line.startswith("0::")), "/")
        current = (root / relative.lstrip("/")).resolve()
        if not current.is_relative_to(root) or not current.is_dir():
            current = root  # A container may mount its own leaf as the cgroup root.
        groups = []
        while current.is_relative_to(root):
            groups.append(current)
            if current == root:
                break
            current = current.parent
        return groups

    def read(self):
        logical = os.cpu_count() or 1
        try:
            affinity = set(os.sched_getaffinity(0))
        except (AttributeError, OSError):
            affinity = set(range(logical))
        count = max(1, len(affinity))
        total = available = None
        for line in self._text(self.proc / "meminfo").splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[0] in ("MemTotal:", "MemAvailable:"):
                try:
                    value = int(fields[1]) * 1024
                except ValueError:
                    continue
                if fields[0] == "MemTotal:":
                    total = value
                else:
                    available = value
        quotas, limits, usages = [], [], {}
        for group in self._groups():
            try:
                quota, period = self._text(group / "cpu.max").split()
                if quota != "max" and int(quota) > 0 and int(period) > 0:
                    fraction = int(quota) / int(period)
                    quotas.append(fraction)
                    stat = dict(line.split() for line in self._text(group / "cpu.stat").splitlines())
                    if "usage_usec" in stat:
                        usages[str(group)] = (int(stat["usage_usec"]), fraction)
            except (ValueError, ZeroDivisionError):
                pass
            try:
                limit = int(self._text(group / "memory.max"))
                if limit > 0:
                    limits.append(limit)
                    try:
                        remaining = max(0, limit - int(self._text(group / "memory.current")))
                        available = remaining if available is None else min(available, remaining)
                    except ValueError:
                        pass
            except ValueError:
                pass
        if limits:
            total = min(limits) if total is None else min(total, *limits)
        if total is not None and available is not None:
            available = max(0, min(available, total))
        cpu_total = cpu_idle = 0
        for line in self._text(self.proc / "stat").splitlines():
            fields = line.split()
            if fields and fields[0].startswith("cpu") and fields[0][3:].isdigit() and int(fields[0][3:]) in affinity:
                try:
                    # guest/guest_nice are already included in user/nice.
                    ticks = [int(v) for v in fields[1:9]]
                    cpu_total += sum(ticks)
                    cpu_idle += sum(ticks[3:5])
                except ValueError:
                    continue
        now = self.clock()
        loads = []
        if self.previous is not None:
            before, old_total, old_idle, old_usages = self.previous
            if cpu_total > old_total:
                loads.append(1 - (cpu_idle - old_idle) / (cpu_total - old_total))
            if now > before:
                for path, (used, fraction) in usages.items():
                    if path in old_usages and used >= old_usages[path][0]:
                        loads.append((used - old_usages[path][0]) / 1e6 / (now - before) / fraction)
        self.previous = (now, cpu_total, cpu_idle, usages)
        utilization = min(1., max(0., max(loads))) if loads else None
        quota = min(quotas) if quotas else None
        return Resources(logical, count, min(count, quota) if quota is not None else float(count),
                         total, available, utilization, quota, min(limits) if limits else None)


def cpu_capacity(resources):
    count = max(1, math.floor(resources.effective_cpus))
    return count - 1 if count >= 4 else count


def memory_reserve(resources):
    if resources.memory_total_bytes is None:
        return 0
    return max(128 * MIB, min(8 * 1024 * MIB, int(resources.memory_total_bytes * .15)))
