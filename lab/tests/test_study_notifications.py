"""Study completion events must follow admission and durable terminal metadata."""

from concurrent.futures import Future
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from lab.drivers.base import EventBus
from lab.drivers.demo_driver import DemoDriver
from lab.engines.run_queue import QueuedRunManager
from lab.engines.study_status import status
from lab.topogen import generate


class StudyNotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        paths = patch("lab.engines.manager.RESULTS_DIR", self.temp.name)
        paths.start()
        self.addCleanup(paths.stop)
        self.bus = EventBus()
        self.queue = QueuedRunManager(self.bus, DemoDriver(self.bus), workers=2)
        self.addCleanup(self.queue.close)
        self.project = generate("ring", routers=3, hosts=1, flows=1)
        self.project["scenarios"] = {"test": {"duration": 1, "apps": [], "events": []}}
        self.param = {"kind": "link", "link": next(iter(self.project["links"])), "field": "bw"}
        self.events = []
        bus_events = patch.object(self.bus, "publish", side_effect=lambda event: self.events.append(dict(event)))
        bus_events.start()
        self.addCleanup(bus_events.stop)

    def wait(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            self.assertLess(time.monotonic(), deadline, "study state did not settle")
            time.sleep(.005)

    def done_events(self):
        return [event for event in self.events if event.get("type") == "study" and event.get("done")]

    @staticmethod
    def failed_future():
        future = Future()
        future.set_exception(RuntimeError("dispatch already failed before callback registration"))
        return future

    def test_early_dispatch_failure_does_not_finish_a_still_running_study(self):
        release, started = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        submit = self.queue.executor.submit
        calls = 0

        def dispatch(*args, **kwargs):
            nonlocal calls
            calls += 1
            return self.failed_future() if calls == 1 else submit(*args, **kwargs)

        def engine(*args, **kwargs):
            started.set()
            release.wait(5)
            return {"flows": {}, "duration": 1}

        with patch.object(self.queue.executor, "submit", side_effect=dispatch), \
                patch("lab.engines.analytic.run", side_effect=engine):
            study = self.queue.start_study(self.project, "test", "analytic", self.param, [1, 2, 3])
            self.assertTrue(started.wait(3))
            self.assertEqual(self.done_events(), [])
            self.assertTrue(any(row["status"] == "running" for row in
                                self.queue.store.list(self.project["name"], study_id=study["study_id"])))
            release.set()
            self.wait(lambda: len(self.done_events()) == 1)
            self.wait(lambda: not self.queue.futures)
        self.assertEqual(self.done_events()[0]["run_ids"], study["run_ids"])
        rows = self.queue.store.list(self.project["name"], study_id=study["study_id"])
        self.assertEqual(sorted(row["status"] for row in rows), ["done", "done", "error"])
        self.assertFalse(self.queue._study_notices)

    def test_all_immediate_failures_emit_one_done_event_after_all_rows_are_terminal(self):
        with patch.object(self.queue.executor, "submit", side_effect=lambda *a, **k: self.failed_future()), \
                patch("lab.engines.analytic.run") as engine:
            study = self.queue.start_study(self.project, "test", "analytic", self.param, [1, 2, 3])
            engine.assert_not_called()
        self.assertEqual(len(self.done_events()), 1)
        self.assertEqual(self.done_events()[0]["run_ids"], study["run_ids"])
        self.assertEqual(self.done_events()[0]["total"], 3)
        rows = self.queue.store.list(self.project["name"], study_id=study["study_id"])
        self.assertEqual([row["status"] for row in rows], ["error"] * 3)
        self.assertFalse(self.queue._study_notices)
        self.assertFalse(self.queue.futures)
        self.assertFalse(self.queue.payloads)
        self.assertFalse(self.queue.tokens)

    def test_failed_local_worker_keeps_ownership_for_orphan_diagnosis(self):
        with patch.object(self.queue.store, "get", side_effect=sqlite3.OperationalError("metadata unavailable")), \
                patch("lab.engines.analytic.run") as engine:
            plan = self.queue.start_study(self.project, "test", "analytic", self.param, [1, 2])
            self.wait(lambda: not self.queue.futures)
            self.wait(lambda: len(self.done_events()) == 1)
            engine.assert_not_called()
        state = status(self.queue, self.project["name"], plan["study_id"])
        self.assertEqual(set(state["orphaned_run_ids"]), set(plan["run_ids"]))
        self.assertIn(state["status"], ("incomplete", "interrupted"))
        self.assertFalse(state["complete"])
        self.assertTrue(all(row["status"] == "queued" for row in state["runs"]))
        self.queue._maybe_study_done(plan["study_id"])
        self.assertEqual(len(self.done_events()), 1)


if __name__ == "__main__":
    unittest.main()
