"""Dispatch only admitted jobs; queued futures remain cancellable without worker threads."""

from collections import Counter, deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import threading
import time

from lab.engines.resources import MIB, ResourceMonitor, cpu_capacity, memory_reserve, positive_integer


@dataclass
class _Task:
    future: Future
    function: object
    args: tuple
    kwargs: dict
    engine: str
    started: bool = False
    abandoned: bool = False


class AdaptiveExecutor:
    def __init__(self, *, ceiling=64, memory_mb=2048, monitor=None, interval=1.):
        self.ceiling = ceiling = positive_integer(ceiling, "NETLAB_RUN_MAX_WORKERS")
        self.memory_mb = positive_integer(memory_mb, "NETLAB_RUN_MEMORY_MB", 1048576)
        if interval <= 0:
            raise ValueError("resource interval must be positive")
        self.monitor, self.interval = monitor or ResourceMonitor(), interval
        self._condition = threading.Condition()
        self._pending = deque()
        self._active, self._engines = 0, Counter()
        self._reserved = 0
        self._closed, self._refresh_requested = False, False
        self._snapshot, self._sensor_error = None, None
        self._limit, self._credit = 1, None
        self._refresh()
        self._pool = ThreadPoolExecutor(max_workers=ceiling, thread_name_prefix="netlab-run")
        self._dispatcher = threading.Thread(target=self._dispatch, name="netlab-resources", daemon=True)
        self._dispatcher.start()

    def _refresh(self):
        try:
            snapshot = self.monitor.read()
            cap = min(self.ceiling, cpu_capacity(snapshot))
            if self._snapshot is None:
                limit = cap
            elif snapshot.cpu_utilization is not None and snapshot.cpu_utilization >= .95:
                limit = max(1, min(self._limit, cap) - 1)
            elif snapshot.cpu_utilization is None or snapshot.cpu_utilization <= .75:
                limit = min(cap, self._limit + 1)
            else:
                limit = max(1, min(cap, self._limit))
            credit = None if snapshot.memory_available_bytes is None else max(
                0, snapshot.memory_available_bytes - memory_reserve(snapshot))
            if credit == 0:
                limit = 0
            elif credit is None:
                limit = min(limit, 1)  # Unknown RAM must not imply a large machine.
            with self._condition:
                if credit is not None and snapshot.memory_total_bytes is not None:
                    credit = min(credit, max(0, snapshot.memory_total_bytes - memory_reserve(snapshot) - self._reserved))
                    if credit == 0:
                        limit = 0
                self._snapshot, self._limit, self._credit = snapshot, limit, credit
                self._sensor_error = None
        except Exception as error:
            with self._condition:
                self._limit = 1
                self._credit = None
                self._sensor_error = f"{type(error).__name__}: {error}"

    def refresh(self):
        with self._condition:
            self._refresh_requested = True
            self._condition.notify_all()

    def _notify(self, future):
        with self._condition:
            self._condition.notify_all()

    def submit(self, fn, /, *args, resource_engine="ns3", **kwargs):
        future = Future()
        with self._condition:
            if self._closed:
                raise RuntimeError("cannot schedule new futures after shutdown")
            self._pending.append(_Task(future, fn, args, kwargs, resource_engine))
            future.add_done_callback(self._notify)
            self._condition.notify_all()
        return future

    def _cost(self, engine):
        return (256 if engine == "analytic" else self.memory_mb) * MIB

    def _take(self):
        self._pending = deque(task for task in self._pending if not task.future.cancelled())
        if self._active >= self._limit:
            return None
        for task in self._pending:
            # Python flow analysis shares the GIL; live Mininet shares one network.
            if task.engine in ("analytic", "mininet") and self._engines[task.engine] >= 1:
                continue
            cost = self._cost(task.engine)
            if self._credit is not None and self._credit < cost:
                continue
            self._pending.remove(task)
            if not task.future.set_running_or_notify_cancel():
                return None
            self._active += 1
            self._reserved += cost
            self._engines[task.engine] += 1
            if self._credit is not None:
                self._credit -= cost
            return task
        return None

    def _release(self, task):
        with self._condition:
            self._active -= 1
            self._reserved -= self._cost(task.engine)
            self._engines[task.engine] -= 1
            self._condition.notify_all()

    def _execute(self, task):
        with self._condition:
            if task.abandoned:
                return
            task.started = True
        try:
            result = task.function(*task.args, **task.kwargs)
        except BaseException as error:
            self._release(task)
            task.future.set_exception(error)
        else:
            self._release(task)
            task.future.set_result(result)

    def _dispatch(self):
        next_refresh = time.monotonic() + self.interval
        while True:
            with self._condition:
                refresh = time.monotonic() >= next_refresh or self._refresh_requested
                if refresh:
                    self._refresh_requested = False
            if refresh:
                self._refresh()
                next_refresh = time.monotonic() + self.interval
            with self._condition:
                task = self._take()
                if task is None:
                    if self._closed and not self._pending and not self._active:
                        self._pool.shutdown(wait=False)
                        return
                    if not self._refresh_requested:
                        self._condition.wait(timeout=max(0, next_refresh - time.monotonic()))
                    continue
            try:
                self._pool.submit(self._execute, task)
            except BaseException as error:
                with self._condition:
                    if task.started:
                        continue
                    task.abandoned = True
                self._release(task)
                task.future.set_exception(error)  # Never invoke completion callbacks under the dispatch lock.

    def status(self):
        with self._condition:
            return {"mode": "auto", "ceiling": self.ceiling, "limit": self._limit, "active": self._active,
                    "queued": sum(not task.future.cancelled() for task in self._pending),
                    "active_engines": dict(self._engines), "run_memory_mb": self.memory_mb,
                    "memory_credit_bytes": self._credit, "sensor_error": self._sensor_error,
                    "estimated_reserved_bytes": self._reserved,
                    "resources": self._snapshot.public() if self._snapshot is not None else None}

    def shutdown(self, wait=True, *, cancel_futures=False):
        with self._condition:
            self._closed = True
            pending = list(self._pending) if cancel_futures else []
        for task in pending:
            task.future.cancel()
        with self._condition:
            self._condition.notify_all()
        if wait:
            self._dispatcher.join()
            self._pool.shutdown(wait=True)
