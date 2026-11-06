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

from lab.engines import manager
from lab.engines.result_index import ResultIndex
from lab.engines.run_store import RunStore, TERMINAL
from lab.engines.run_submission import PreparedRun, submit


class RunCancelled(Exception):
    pass


class QueuedRunManager(manager.RunManager):
    def start_experiment(self, project, scenario_name, engine, repetitions=10, seed=1):
        from lab.campaigns import prepare_experiment
        condition, jobs = prepare_experiment(self, project, scenario_name, engine, repetitions, seed)
        submit(self, jobs)
        return {"experiment_id": condition["experiment_id"], "run_ids": condition["run_ids"], "repetitions": repetitions}

    def __init__(self, bus, driver, workers=None, database=None):
        super().__init__(bus, driver)
        workers = int(workers or os.environ.get("NETLAB_RUN_WORKERS", "2"))
        if not 1 <= workers <= 16:
            raise ValueError("NETLAB_RUN_WORKERS: 1..16")
        self.store = RunStore(database or Path(manager.RESULTS_DIR, "runs.sqlite3"))
        self.result_index = ResultIndex(self.store, manager.RESULTS_DIR)
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="netlab-run")
        self.futures, self.tokens = {}, {}
        self.payloads = {}
        self.queue_lock = threading.RLock()
        self.closed = False
        self.workers = workers
        self.local = threading.local()
        self.real_lock = threading.Lock()
        from lab.campaigns import CampaignManager
        self.campaigns = CampaignManager(self)

    def _enqueue(self, rid, pname, project, name, sc, engine, extra=None):
        job = PreparedRun(rid, pname, project, name, sc, engine, extra)
        return submit(self, [job])[rid]

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

    def _execute_prepared(self, rid):
        with self.queue_lock:
            job = self.payloads.pop(rid)
        return self._execute(*job.args())

    def _execute(self, rid, pname, project, name, sc, engine, extra):
        self.local.run_id = rid
        try:
            with self.queue_lock:
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
        except Exception as exc:
            # Failures before the base worker's result/finally block must not leave a live-looking job.
            # A completed JSON is authoritative; never replace committed measurements with a callback/index error.
            if not (self.result_index.root / pname / (rid + ".json")).exists():
                try:
                    data = self.store.get(pname, rid)
                    if data["status"] not in TERMINAL:
                        data.update(status="error", error=f"{type(exc).__name__}: {exc}")
                        self.store.save(data)
                except Exception:
                    pass  # Keep the original exception when storage is unavailable.
            raise
        finally:
            with self._lock:
                self.active.pop(rid, None)
            with self.queue_lock:
                self.futures.pop(rid, None)
                self.tokens.pop(rid, None)
                self.payloads.pop(rid, None)
            self.local.run_id = None

    def _pub(self, rid, **kw):
        token = self.tokens.get(rid)
        if "progress" in kw and token and token.is_set():
            raise RunCancelled("run cancelled")
        return super()._pub(rid, **kw)

    def _save(self, pname, result):
        with self.result_index.lock:
            with self._lock:
                active = self.active.get(result["run_id"], {})
                if active.get("project") == pname and "_label_override" in active:
                    result["label"] = active["_label_override"]
            token = self.tokens.get(result["run_id"])
            if token and token.is_set():
                result.update(status="cancelled", error=None)
            super()._save(pname, result)
            self.result_index.record(pname, result)

    def update_label(self, project, rid, label):
        with self.queue_lock, self.result_index.lock:
            data = self.get(project, rid)
            data["project"], data["label"] = project, str(label)[:80]
            with self._lock:
                active = self.active.get(rid)
                if active and active["project"] == project:
                    active["_label_override"] = data["label"]
            if data["status"] not in TERMINAL and active and active["project"] == project:
                self.store.save(data)  # Pending label edits must not create partial result JSON files.
            else:
                self._save(project, data)
            return data

    def get(self, project, rid):
        try:
            return super().get(project, rid)
        except FileNotFoundError:
            return self.store.get(project, rid)

    def _overlay_active(self, rows):
        with self._lock:
            active = {rid: dict(data) for rid, data in self.active.items()}
        for row in rows:
            data = active.get(row["run_id"])
            if data and data["project"] == row["project"] and row["status"] not in TERMINAL:
                # Keep legacy status=running while exposing the actual queue phase.
                row["queue_state"] = row["status"]
                row["status"] = "running"
                row["progress"] = data.get("progress", 0)
        return rows

    def list(self, project, **filters):
        self.result_index.reconcile(project)
        return self._overlay_active(self.store.list(project, **filters))

    def page(self, project, limit=100, offset=0, **filters):
        self.result_index.reconcile(project)
        page = self.store.page(project, limit, offset, **filters)
        self._overlay_active(page["runs"])
        return page

    def reindex(self, project):
        return self.result_index.reconcile(project, force=True)

    def cancel(self, project, rid):
        with self.queue_lock:
            data = self.store.get(project, rid)
            if data["status"] in TERMINAL:
                return data
            token, future = self.tokens.get(rid), self.futures.get(rid)
            if not token or not future:
                raise ValueError("run has no cancellable local worker")
            token.set()
            if future.cancel():
                try:
                    self._save(project, {**data, "status": "cancelled", "flows": {}})
                finally:
                    with self._lock:
                        self.active.pop(rid, None)
                    self.tokens.pop(rid, None)
                    self.futures.pop(rid, None)
                    self.payloads.pop(rid, None)
            else:
                data["status"] = "cancelling"
                self.store.save(data)
            return self.store.get(project, rid)

    def delete(self, project, rid):
        with self.queue_lock:
            if rid in self.futures and self.store.get(project, rid)["status"] not in TERMINAL:
                raise ValueError("cancel an active run before deleting it")
            with self.result_index.lock:
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
