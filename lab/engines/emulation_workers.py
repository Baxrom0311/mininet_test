"""Per-run ownership of real emulation subprocesses and scheduled/read workers."""

import subprocess
import threading
import time


class EmulationStopped(RuntimeError):
    pass


class EmulationWorkers:
    def __init__(self):
        self.processes = []
        self.stop = threading.Event()
        self._threads = []
        self._errors = []
        self._lock = threading.RLock()
        self._closed = False

    def error(self, label, error):
        with self._lock:
            if len(self._errors) < 16:
                self._errors.append(f"{label}: {type(error).__name__}: {error}")

    def check(self):
        with self._lock:
            errors = list(self._errors)
        if errors:
            raise RuntimeError("emulation workers failed: " + "; ".join(errors[:5]))

    def spawn(self, argv):
        # Close cannot snapshot children between Popen and registration.
        with self._lock:
            if self._closed:
                raise EmulationStopped("emulation is stopping")
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, bufsize=1)
            self.processes.append(process)
            return process

    def worker(self, label, fn):
        def run():
            try:
                fn()
            except EmulationStopped as error:
                if not self.stop.is_set():
                    self.error(label, error)
            except Exception as error:
                self.error(label, error)
        with self._lock:
            if self._closed:
                return None
            thread = threading.Thread(target=run, name="netlab-emulation-" + label, daemon=True)
            self._threads.append((label, thread))
            thread.start()
            return thread

    def schedule(self, delay, fn, label):
        def run():
            if not self.stop.wait(max(delay, 0)):
                fn()
        return self.worker(label, run)

    def reader(self, process, fn, on_eof=None):
        def run():
            for line in process.stdout:
                try:
                    fn(line.rstrip())
                except Exception as error:
                    self.error("parser", error)
            if on_eof:
                process.wait(timeout=5)
                on_eof()
        return self.worker("reader", run)

    def close(self, timeout=3):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self.stop.set()
            processes, threads = list(self.processes), list(self._threads)
        problems = []
        for process in processes:
            try:
                if process.poll() is None:
                    process.terminate()
            except ProcessLookupError:
                pass
            except Exception as error:
                problems.append(f"terminate: {error}")
        deadline = time.monotonic() + timeout
        killed = []
        for process in processes:
            try:
                process.wait(timeout=max(deadline - time.monotonic(), 0))
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    killed.append(process)
                except Exception as error:
                    problems.append(f"kill: {error}")
            except Exception as error:
                problems.append(f"wait: {error}")
        deadline = time.monotonic() + timeout
        for process in killed:
            try:
                process.wait(timeout=max(deadline - time.monotonic(), 0))
            except Exception as error:
                problems.append(f"wait after kill: {error}")
        deadline = time.monotonic() + timeout
        for label, thread in threads:
            thread.join(timeout=max(deadline - time.monotonic(), 0))
            if thread.is_alive():
                problems.append(f"worker did not stop: {label}")
        # Do not close a TextIOWrapper while a stuck reader holds its read lock.
        if not any(thread.is_alive() for _, thread in threads):
            for process in processes:
                if process.stdout is not None:
                    try:
                        process.stdout.close()
                    except Exception as error:
                        problems.append(f"close stdout: {error}")
        if problems:
            raise RuntimeError("emulation cleanup failed: " + "; ".join(problems[:5]))

    def finish(self):
        self.close()
        self.check()
