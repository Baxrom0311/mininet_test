"""One admission path for normal runs, replications and bounded campaigns."""

from dataclasses import dataclass
from functools import partial
import threading
import time


@dataclass
class PreparedRun:
    run_id: str
    project_name: str
    project: dict
    scenario_name: str
    scenario: dict
    engine: str
    extra: dict | None = None
    resource_group: str | None = None
    resource_parallelism: int | None = None

    def args(self):
        return (self.run_id, self.project_name, self.project, self.scenario_name, self.scenario, self.engine, self.extra)

    def metadata(self, created):
        return {"run_id": self.run_id, "project": self.project_name, "scenario_name": self.scenario_name,
                "engine": self.engine, "duration": self.scenario.get("duration"), "created": created,
                "status": "queued", **(self.extra or {})}


def submit(queue, jobs, campaign=None):
    """Persist every planned row before any worker can start; cancel the batch on scheduling failure."""
    futures, metadata = {}, {}
    admitted = False
    with queue.queue_lock:
        try:
            ids = [job.run_id for job in jobs]
            if (not jobs or len(set(ids)) != len(ids) or any(rid in queue.futures for rid in ids)):
                raise ValueError("invalid or duplicate batch run identities")
            if queue.closed or len(queue.futures) + len(jobs) > 256:
                raise ValueError("run queue closed or full (256 pending/running)")
            with queue._lock:
                for job in jobs:
                    created = queue.active.get(job.run_id, {}).get("created", time.time())
                    metadata[job.run_id] = job.metadata(created)
            with queue.store.connect() as db:
                if campaign is not None:
                    campaign["submission"] = "admitted"
                    queue.campaigns.store.put(db, campaign)
                for data in metadata.values():
                    queue.store._write(db, data)
            admitted = True
            with queue._lock:
                for job in jobs:
                    data = metadata[job.run_id]
                    queue.active[job.run_id] = {**data, "progress": 0.0}
                    queue.tokens[job.run_id] = threading.Event()
                    queue.payloads[job.run_id] = job
            for job in jobs:
                future = queue.executor.submit(queue._execute_prepared, job.run_id, resource_engine=job.engine,
                                               resource_group=job.resource_group, resource_parallelism=job.resource_parallelism)
                futures[job.run_id] = queue.futures[job.run_id] = future
                future.add_done_callback(partial(queue._dispatch_done, job.project_name, job.run_id))
            if campaign is not None:
                campaign["submission"] = "submitted"
                with queue.store.connect() as db:
                    queue.campaigns.store.put(db, campaign)
            return futures
        except Exception as exc:
            for job in jobs:
                token = queue.tokens.get(job.run_id)
                if job.run_id in futures and token:
                    token.set()
            terminal = []
            with queue._lock:
                for job in jobs:
                    rid, future = job.run_id, futures.get(job.run_id)
                    if rid in queue.futures and rid not in futures:
                        continue  # Never remove a pre-existing job on an identity conflict.
                    if future is None or future.cancel():
                        queue.active.pop(rid, None)
                        queue.tokens.pop(rid, None)
                        queue.futures.pop(rid, None)
                        queue.payloads.pop(rid, None)
                        status = "error" if future is None else "cancelled"
                    else:
                        status = "cancelling"  # Worker waits on queue_lock and will observe its token.
                    if admitted:
                        terminal.append({**metadata[rid], "status": status, "error": f"Batch scheduling failed: {exc}"})
            if admitted:
                try:
                    with queue.store.connect() as db:
                        for data in terminal:
                            queue.store._write(db, data)
                        if campaign is not None:
                            campaign.update(submission="error", submission_error=f"{type(exc).__name__}: {exc}")
                            queue.campaigns.store.put(db, campaign)
                except Exception:
                    pass  # Preserve the original error if storage itself failed; never replay these jobs.
            raise
