"""Bounded execution and cooperative cancellation on top of existing engines."""

from concurrent.futures import CancelledError, ThreadPoolExecutor
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time

from lab.engines import manager
from lab.engines.run_store import RunStore, TERMINAL


class RunCancelled(Exception):
    pass


class QueuedRunManager(manager.RunManager):
    def __init__(self, bus, driver, workers=None, database=None):
        super().__init__(bus, driver)
        workers = int(workers or os.environ.get("NETLAB_RUN_WORKERS", "2"))
        if not 1 <= workers <= 16:
            raise ValueError("NETLAB_RUN_WORKERS: 1..16")
        self.store = RunStore(database or Path(manager.RESULTS_DIR, "runs.sqlite3"))
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="netlab-run")
        self.futures, self.tokens = {}, {}
        self.queue_lock = threading.RLock()
        self.closed = False
        self.workers = workers
        self.local = threading.local()
        self.real_lock = threading.Lock()

    def _enqueue(self, rid, pname, project, name, sc, engine, extra=None):
        with self.queue_lock:
            if self.closed or len(self.futures) >= 256:
                with self._lock:
                    self.active.pop(rid, None)
                raise ValueError("run queue closed or full (256 pending/running)")
            token = self.tokens[rid] = threading.Event()
            self.store.save({"run_id": rid, "project": pname, "scenario_name": name, "engine": engine,
                             "duration": sc.get("duration"), "created": time.time(), "status": "queued", **(extra or {})})
            future = self.executor.submit(self._execute, rid, pname, project, name, sc, engine, extra)
            self.futures[rid] = future
            return future

    def _launch_worker(self, *args):
        self._enqueue(*args)

    def _study_worker(self, *args, **kwargs):
        if self.closed:
            with self._lock:
                self.active.pop(args[0], None)
            return
        try:
            self._enqueue(*args, **kwargs).result()
        except CancelledError:
            pass

    def _execute(self, rid, pname, project, name, sc, engine, extra):
        self.local.run_id = rid
        try:
            data = self.store.get(pname, rid)
            data["status"] = "running"
            self.store.save(data)
            if self.tokens[rid].is_set():
                self._save(pname, {**data, "status": "cancelled", "flows": {}})
                with self._lock:
                    self.active.pop(rid, None)
            else:
                if engine == "mininet":
                    with self.real_lock:
                        super()._worker(rid, pname, project, name, sc, engine, extra)
                else:
                    super()._worker(rid, pname, project, name, sc, engine, extra)
        finally:
            with self.queue_lock:
                self.futures.pop(rid, None)
                self.tokens.pop(rid, None)
            self.local.run_id = None

    def _pub(self, rid, **kw):
        token = self.tokens.get(rid)
        if "progress" in kw and token and token.is_set():
            raise RunCancelled("run cancelled")
        return super()._pub(rid, **kw)

    def _save(self, pname, result):
        token = self.tokens.get(result["run_id"])
        if token and token.is_set():
            result.update(status="cancelled", error=None)
        super()._save(pname, result)
        self.store.save(result)

    def get(self, project, rid):
        try:
            return super().get(project, rid)
        except FileNotFoundError:
            return self.store.get(project, rid)

    def list(self, project):
        rows = {r["run_id"]: r for r in super().list(project)}
        for data in self.store.list(project):
            rid = data["run_id"]
            if rid in rows and data["status"] not in TERMINAL:
                # Legacy clients already poll status=running; expose actual phase separately.
                rows[rid]["queue_state"] = data["status"]
            elif rid not in rows:
                rows[rid] = data
        return sorted(rows.values(), key=lambda r: r.get("created") or 0, reverse=True)

    def cancel(self, project, rid):
        with self.queue_lock:
            data = self.store.get(project, rid)
            if data["status"] in TERMINAL:
                return data
            token, future = self.tokens.get(rid), self.futures.get(rid)
            if not token or not future:
                raise ValueError("run belongs to another live worker")
            token.set()
            if future.cancel():
                self._save(project, {**data, "status": "cancelled", "flows": {}})
                with self._lock:
                    self.active.pop(rid, None)
                self.tokens.pop(rid, None)
                self.futures.pop(rid, None)
            else:
                data["status"] = "cancelling"
                self.store.save(data)
            return self.store.get(project, rid)

    def delete(self, project, rid):
        with self.queue_lock:
            if rid in self.futures and self.store.get(project, rid)["status"] not in TERMINAL:
                raise ValueError("cancel an active run before deleting it")
            super().delete(project, rid)
            self.store.delete(project, rid)

    def close(self):
        with self.queue_lock:
            self.closed = True
            active = [(rid, a["project"]) for rid, a in list(self.active.items())]
        for rid, project in active:
            try:
                self.cancel(project, rid)
            except (ValueError, FileNotFoundError):
                pass
        self.executor.shutdown(wait=True, cancel_futures=True)

    def _run_ns3(self, project, sc, prog, log):
        """Read logs asynchronously so cancellation also works during silent startup."""
        token = self.tokens[self.local.run_id]
        with tempfile.TemporaryDirectory(prefix="netlab-ns3-") as td:
            inp, out = Path(td, "in.json"), Path(td, "out.json")
            inp.write_text(json.dumps({"project": project, "scenario": sc}))
            env = dict(os.environ, PYTHONPATH=os.path.dirname(manager.LAB_DIR) + os.pathsep + os.environ.get("PYTHONPATH", ""))
            process = subprocess.Popen([sys.executable, "-m", "lab.engines.ns3_runner", str(inp), str(out)],
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
            messages = queue.Queue()
            def read():
                for line in process.stdout:
                    messages.put(line)
                messages.put(None)
            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            tail = []
            try:
                while True:
                    if token.is_set():
                        raise RunCancelled("run cancelled")
                    try:
                        line = messages.get(timeout=.1)
                    except queue.Empty:
                        continue
                    if line is None:
                        break
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        tail = (tail + [line.strip()])[-10:]
                        continue
                    if "progress" in msg:
                        prog(msg["progress"])
                    if msg.get("log"):
                        log(msg["log"])
                process.wait(timeout=30)
                if process.returncode or not out.exists():
                    raise RuntimeError("ns-3 failed: " + " | ".join(tail))
                return json.loads(out.read_text())
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()
                reader.join(timeout=2)
                process.stdout.close()
