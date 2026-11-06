"""Ping observations and bounded process cleanup for real calibration collectors."""

import re
import subprocess
import threading
import time

_PING = re.compile(r"^\[(\d+\.\d+)\]\s+\d+ bytes from .*icmp_seq=(\d+)")


def poll(fn, timeout, step=0.1, *, started_at=None):
    """Elapsed monotonic time, optionally including the preceding link command."""
    start = time.monotonic() if started_at is None else started_at
    while time.monotonic() - start < timeout:
        if fn():
            return time.monotonic() - start
        time.sleep(step)
    return None


class PingTrace:
    def __init__(self):
        self._replies = {}
        self._lock = threading.Lock()
        self.invalid_clock = False

    def record(self, line):
        match = _PING.match(line)
        if not match:
            return
        stamp, sequence = float(match[1]), int(match[2])
        with self._lock:
            if sequence in self._replies:
                return
            if self._replies and stamp < next(reversed(self._replies.values())):
                self.invalid_clock = True
            self._replies[sequence] = stamp

    @property
    def times(self):
        with self._lock:
            return list(self._replies.values())

    def ready_before(self, event):
        before = [t for t in self.times if t <= event]
        return not self.invalid_clock and len(before) >= 2 and 0 <= event - before[-1] <= 1

    def gap_after(self, event, ended_at):
        if not self.ready_before(event):
            return None
        times = [t for t in self.times if t <= ended_at]
        before, after = [t for t in times if t <= event], [t for t in times if t > event]
        # An unresolved trailing outage must never be reported as a zero/short gap.
        if not after or ended_at - after[-1] > 1:
            return None
        selected = [before[-1]] + after
        return max(0.0, max(b - a for a, b in zip(selected, selected[1:])) - .1)

    def first_after(self, event, ended_at=None):
        if self.invalid_clock:
            return None
        return next((t - event for t in self.times if t > event and (ended_at is None or t <= ended_at)), None)


class Pinger:
    def __init__(self, drv, host, ip):
        pid = drv.net.get(host).pid
        self.trace = PingTrace()
        self._wall_start, self._mono_start = time.time(), time.monotonic()
        self._ended_at = None
        self._reader_error = None
        self._started = threading.Event()
        self.p = subprocess.Popen(["mnexec", "-a", str(pid), "ping", "-D", "-n", "-i", "0.1", "-W", "1", ip],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            for line in self.p.stdout:
                self._started.set()
                self.trace.record(line)
        except Exception as error:
            self._reader_error = str(error)

    @property
    def times(self):
        return self.trace.times

    def ready_before(self, event):
        return self.trace.ready_before(event)

    def wait_ready(self, timeout=10):
        def ready():
            if self.p.poll() is not None:
                raise RuntimeError("ping probe exited before baseline was ready")
            return self.ready_before(time.time())
        if poll(ready, timeout, step=.05) is None:
            raise RuntimeError("no fresh pre-event ping baseline (two replies required)")

    def mark_event(self, *, require_ready=True):
        if not require_ready and not self._started.wait(timeout=3):
            raise RuntimeError("ping probe produced no startup output")
        wall, mono = time.time(), time.monotonic()
        if self.p.poll() is not None:
            raise RuntimeError("ping probe exited before event")
        if require_ready and not self.ready_before(wall):
            raise RuntimeError("pre-event ping baseline is missing or stale")
        return wall, mono

    def stop(self, timeout=3):
        if self._ended_at is not None:
            return
        ended_at, mono_end = time.time(), time.monotonic()
        if self.p.poll() is None:
            self.p.terminate()
        try:
            self.p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait(timeout=timeout)
        self.reader.join(timeout=timeout)
        if self.reader.is_alive():
            raise RuntimeError("ping stdout reader did not finish")
        self.p.stdout.close()
        self._ended_at = ended_at
        if abs((ended_at - self._wall_start) - (mono_end - self._mono_start)) > .5:
            self.trace.invalid_clock = True
        if self._reader_error or self.p.returncode == 2:
            raise RuntimeError(f"ping probe failed: {self._reader_error or 'exit 2'}")

    def gap_after(self, event):
        if self._ended_at is None:
            raise RuntimeError("stop the ping probe before reading final metrics")
        return self.trace.gap_after(event, self._ended_at)

    def first_after(self, event):
        if self._ended_at is None:
            raise RuntimeError("stop the ping probe before reading final metrics")
        return self.trace.first_after(event, self._ended_at)

    def __enter__(self):
        return self

    def __exit__(self, kind, error, traceback):
        try:
            self.stop()
        except Exception as cleanup_error:
            if error is None:
                raise
            error.add_note(f"ping cleanup also failed: {cleanup_error}")
