"""Bounded execution and cooperative cancellation on top of existing engines."""

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading

from lab.engines import manager
from lab.engines.adaptive_executor import AdaptiveExecutor
from lab.engines.resources import ResourceMonitor, positive_integer
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

    def __init__(self, bus, driver, workers=None, database=None, *, resource_monitor=None):
        super().__init__(bus, driver)
        setting = os.environ.get("NETLAB_RUN_WORKERS", "auto") if workers is None else workers
        self.auto_workers = str(setting).strip().lower() == "auto"
        workers = positive_integer(os.environ.get("NETLAB_RUN_MAX_WORKERS", "64") if self.auto_workers else setting,
                                   "NETLAB_RUN_MAX_WORKERS" if self.auto_workers else "NETLAB_RUN_WORKERS")
        memory_mb = positive_integer(os.environ.get("NETLAB_RUN_MEMORY_MB", "2048"), "NETLAB_RUN_MEMORY_MB", 1048576)
        self.resource_monitor = resource_monitor or ResourceMonitor()
        self.store = RunStore(database or Path(manager.RESULTS_DIR, "runs.sqlite3"))
        self.result_index = ResultIndex(self.store, manager.RESULTS_DIR)
        self.futures, self.tokens = {}, {}
        self.payloads = {}
        self.queue_lock = threading.RLock()
        self.closed = False
        self._study_notices = {}
        self.workers = workers
        self.study_parallelism("ns3")  # Reject invalid per-study limits before starting threads.
        self.local = threading.local()
        self.real_lock = threading.Lock()
        from lab.campaigns import CampaignManager
        self.campaigns = CampaignManager(self)
        self.executor = AdaptiveExecutor(ceiling=workers, memory_mb=memory_mb, monitor=self.resource_monitor,
                                         adaptive=self.auto_workers)

    def start_study(self, project, scenario_name, engine, param, values, param2=None, values2=None):
        from lab.engines.studies import prepare_study
        with self.queue_lock:
            if self.closed:
                raise ValueError("run queue closed")
        plan = prepare_study(self, project, scenario_name, engine, param, values, param2, values2)
        with self.queue_lock:
            self._study_notices[plan.study_id] = {"type": "study", "study_id": plan.study_id,
                "project": plan.jobs[0].project_name, "total": len(plan.jobs),
                "run_ids": [job.run_id for job in plan.jobs], "submitted": False}
            try:
                submit(self, plan.jobs)
            except BaseException:
                self._study_notices.pop(plan.study_id, None)
                raise
            self._study_notices[plan.study_id]["submitted"] = True
            self._maybe_study_done(plan.study_id)
        return plan.response()

    def _maybe_study_done(self, study_id):
        # This notification follows saved cancellation/results, not Future.cancel's
        # synchronous callbacks. Never capture projects in long-lived callbacks.
        try:
            with self.queue_lock:
                notice = self._study_notices.get(study_id)
                if not notice or not notice["submitted"] or any(rid in self.futures for rid in notice["run_ids"]):
                    return
                rows = self.store.list(notice["project"], study_id=study_id)
                states = [row["status"] for row in rows]
                pending_metadata = any(state not in TERMINAL for state in states)
                state = ("incomplete" if len(rows) != notice["total"] or any(s not in TERMINAL for s in states)
                         else "done" if all(s == "done" for s in states)
                         else "failed" if "error" in states else "interrupted" if "interrupted" in states else "cancelled")
                notified = notice.get("notified", False)
                if pending_metadata:
                    notice["notified"] = True  # Keep ownership evidence for read-only orphan diagnosis.
                else:
                    self._study_notices.pop(study_id, None)
                if notified:
                    return
            self.bus.publish({**{key: value for key, value in notice.items() if key not in ("submitted", "notified")},
                              "done": True, "status": state})
        except Exception:
            pass  # A best-effort event must not mask a saved result or storage failure.

    def study_parallelism(self, engine):
        if engine != "ns3":
            return 1
        setting = os.environ.get("NETLAB_NS3_PARALLEL", "").strip()
        # Per-study admission bound; the global dispatcher also enforces CPU/RAM limits.
        return min(self.workers, positive_integer(setting, "NETLAB_NS3_PARALLEL")) if setting else self.workers

    def resource_status(self):
        return self.executor.status()

    def _dispatch_done(self, project, rid, future):
        if future.cancelled() or future.exception() is None:
            return
        with self.queue_lock:
            if rid not in self.futures:
                return  # Normal execution has already saved its result and released ownership.
            data = {}
            try:
                data = self.store.get(project, rid)
                if data["status"] not in TERMINAL:
                    error = future.exception()
                    data.update(status="error", error=f"{type(error).__name__}: {error}")
                    self.store.save(data)
            finally:
                with self._lock:
                    self.active.pop(rid, None)
                self.futures.pop(rid, None)
                self.tokens.pop(rid, None)
                self.payloads.pop(rid, None)
                self._maybe_study_done(data.get("study_id"))

    def _enqueue(self, rid, pname, project, name, sc, engine, extra=None):
        job = PreparedRun(rid, pname, project, name, sc, engine, extra)
        return submit(self, [job])[rid]

    def _launch_worker(self, *args):
        self._enqueue(*args)

    def _execute_prepared(self, rid, resource_engine=None):
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
                if extra and extra.get("study_id"):
                    self.bus.publish({"type": "study", "study_id": extra["study_id"], "project": pname,
                        "index": extra["study_position"], "total": extra["study_total"],
                        "value": extra["label"], "run_id": rid})
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
            if extra and extra.get("study_id"):
                self._maybe_study_done(extra["study_id"])

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
                    self._maybe_study_done(data.get("study_id"))
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
