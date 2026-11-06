"""Own one ns-3 subprocess through output handling, cancellation and cleanup.

Linux pipe readiness avoids a reader thread and an unbounded cross-thread queue.
Only diagnostic output is bounded; the result JSON is read in full.
"""

from collections import deque
import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time

TERM_TIMEOUT = 5.
EXIT_TIMEOUT = 30.
PIPE_TIMEOUT = 2.
POLL_INTERVAL = .1
MAX_LINE_BYTES = 65536
READ_BYTES = 16384
DIAGNOSTIC_BYTES = 2048


class _Output:
    def __init__(self, progress, log, check_cancel):
        self.progress, self.log, self.check_cancel = progress, log, check_cancel
        self.buffer, self.discarding = bytearray(), False
        self.tail = deque(maxlen=6)

    def diagnostic(self, line):
        line = line.strip()
        if line:
            self.tail.append(line[:DIAGNOSTIC_BYTES])

    def line(self, data):
        self.check_cancel()
        line = data.decode("utf-8", errors="replace").strip()
        if not line:
            return
        try:
            message = json.loads(line)
        except ValueError:
            self.diagnostic(line)
            return
        if not isinstance(message, dict):
            self.diagnostic(line)
            return
        if "progress" in message:
            value = message["progress"]
            if type(value) not in (int, float) or not 0 <= value <= 1 or not math.isfinite(value):
                raise ValueError("ns-3 progress must be a finite number in 0..1")
            self.progress(value)
        if message.get("log") is not None:
            if not isinstance(message["log"], str):
                raise ValueError("ns-3 log must be a string")
            if message["log"]:
                self.log(message["log"])

    def feed(self, data):
        parts = data.split(b"\n")
        for index, part in enumerate(parts):
            self.check_cancel()
            complete = index < len(parts) - 1
            if not self.discarding:
                if len(self.buffer) + len(part) > MAX_LINE_BYTES:
                    prefix = (bytes(self.buffer) + part)[:DIAGNOSTIC_BYTES]
                    self.diagnostic("[truncated ns-3 output] " + prefix.decode("utf-8", errors="replace"))
                    self.buffer.clear()
                    self.discarding = True
                else:
                    self.buffer.extend(part)
            if complete:
                if not self.discarding:
                    self.line(bytes(self.buffer))
                self.buffer.clear()
                self.discarding = False

    def finish(self):
        if self.buffer and not self.discarding:
            self.line(bytes(self.buffer))
        self.buffer.clear()


def _signal_group(process, signum, errors):
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass
    except BaseException as error:
        errors.append(error)


def _cleanup(process, selector):
    errors = []
    _signal_group(process, signal.SIGTERM, errors)
    try:
        process.wait(timeout=TERM_TIMEOUT)
    except subprocess.TimeoutExpired:
        pass
    except BaseException as error:
        errors.append(error)
    alive = True
    try:
        alive = process.poll() is None
    except BaseException as error:
        errors.append(error)
    if alive:
        _signal_group(process, signal.SIGKILL, errors)
        try:
            process.kill()  # Also attempt direct cleanup if group signalling failed.
        except ProcessLookupError:
            pass
        except BaseException as error:
            errors.append(error)
        try:
            process.wait(timeout=TERM_TIMEOUT)
        except BaseException as error:
            errors.append(error)
    # The parent may have exited while a descendant still holds its stdout pipe.
    _signal_group(process, signal.SIGKILL, errors)
    for resource in (selector, process.stdout):
        if resource is not None:
            try:
                resource.close()
            except BaseException as error:
                errors.append(error)
    return errors


def _read(process, selector, output, check_cancel):
    selector.register(process.stdout, selectors.EVENT_READ)
    eof_at = idle_since = None
    while True:
        check_cancel()
        now = time.monotonic()
        if process.poll() is not None:
            if eof_at is not None:
                return
        elif eof_at is not None and now - eof_at >= EXIT_TIMEOUT:
            raise TimeoutError("ns-3 closed stdout but did not exit")
        if eof_at is not None:
            time.sleep(POLL_INTERVAL)
            continue
        events = selector.select(POLL_INTERVAL)
        if not events and process.poll() is not None:
            if idle_since is None:
                idle_since = time.monotonic()
            if time.monotonic() - idle_since >= PIPE_TIMEOUT:
                raise RuntimeError("ns-3 stdout did not close after child exit")
        for key, _ in events:
            data = os.read(key.fd, READ_BYTES)
            if data:
                idle_since = None  # Callback time and drainable backlog do not imply a held-open pipe.
                output.feed(data)
            else:
                output.finish()
                selector.unregister(process.stdout)
                eof_at = time.monotonic()


def _invalid_constant(value):
    raise ValueError(f"nonfinite ns-3 result constant: {value}")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        _invalid_constant(value)
    return number


def run_ns3(project, scenario, progress, log, *, check_cancel=None):
    """Run the packet engine; always reap the child and close its pipe on return/error."""
    check_cancel = check_cancel or (lambda: None)
    check_cancel()
    with tempfile.TemporaryDirectory(prefix="netlab-ns3-") as directory:
        inp, out = Path(directory, "in.json"), Path(directory, "out.json")
        inp.write_text(json.dumps({"project": project, "scenario": scenario}), encoding="utf-8")
        root = str(Path(__file__).resolve().parents[2])
        env = dict(os.environ, PYTHONPATH=root + os.pathsep + os.environ.get("PYTHONPATH", ""))
        process = subprocess.Popen([sys.executable, "-m", "lab.engines.ns3_runner", str(inp), str(out)],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                                   start_new_session=True, bufsize=0)
        selector, primary = None, None
        try:
            selector = selectors.DefaultSelector()
            output = _Output(progress, log, check_cancel)
            _read(process, selector, output, check_cancel)
            process.wait(timeout=0)
            if process.returncode or not out.is_file():
                raise RuntimeError(f"ns-3 failed (code {process.returncode}): " + " | ".join(output.tail))
            check_cancel()
            result = json.loads(out.read_text(encoding="utf-8"), parse_constant=_invalid_constant, parse_float=_finite_float)
            if not isinstance(result, dict):
                raise ValueError("ns-3 result must be an object")
            return result
        except BaseException as error:
            primary = error
            raise
        finally:
            errors = _cleanup(process, selector)
            if errors:
                detail = "ns-3 cleanup failed: " + "; ".join(f"{type(e).__name__}: {e}" for e in errors)
                if primary is not None:
                    primary.add_note(detail)
                else:
                    raise RuntimeError(detail)
