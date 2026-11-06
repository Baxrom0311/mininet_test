import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from lab.drivers.base import EventBus
from lab.drivers.demo_driver import DemoDriver
from lab.engines.run_queue import QueuedRunManager
from lab.engines.run_store import RunStore
from lab.model import empty_project


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = patch("lab.engines.manager.RESULTS_DIR", self.temp.name)
        self.paths.start()
        self.manager = QueuedRunManager(EventBus(), DemoDriver(EventBus()), workers=2)
        self.project = empty_project("queue")
        self.project["scenarios"]["test"] = {"duration": 1, "apps": [], "events": []}

    def tearDown(self):
        self.manager.close()
        self.paths.stop()
        self.temp.cleanup()

    def wait(self, check):
        deadline = time.monotonic() + 5
        while not check():
            if time.monotonic() > deadline:
                self.fail("worker did not reach expected state")
            time.sleep(.01)

    def test_bounded_execution_and_both_cancellations(self):
        lock, release = threading.Lock(), threading.Event()
        running = peak = 0
        def engine(p, sc, progress, log):
            nonlocal running, peak
            with lock:
                running += 1; peak = max(peak, running)
            try:
                while not release.wait(.01):
                    progress(.1)
                return {"flows": {}, "duration": 1}
            finally:
                with lock:
                    running -= 1
        with patch("lab.engines.analytic.run", side_effect=engine):
            ids = [self.manager.start(self.project, "test", "analytic")["run_id"] for _ in range(6)]
            self.wait(lambda: running == 2)
            self.assertEqual(sum(r["status"] == "queued" for r in self.manager.store.list("queue")), 4)
            with self.assertRaises(FileNotFoundError):
                self.manager.cancel("another-project", ids[-1])
            self.manager.cancel("queue", ids[-1])
            self.manager.cancel("queue", ids[0])
            self.wait(lambda: self.manager.get("queue", ids[0])["status"] == "cancelled")
            self.assertEqual(self.manager.get("queue", ids[-1])["status"], "cancelled")
            release.set()
            self.wait(lambda: not self.manager.active)
            self.assertEqual(peak, 2)
            self.assertEqual(len(self.manager.store.list("queue")), 6)

    def test_dead_worker_is_interrupted_after_restart(self):
        database = Path(self.temp.name, "restart.sqlite3")
        code = ("from lab.engines.run_store import RunStore; import sys; "
                "RunStore(sys.argv[1]).save({'run_id':'dead','project':'queue','status':'running'})")
        subprocess.run([sys.executable, "-c", code, str(database)], check=True, timeout=10)
        self.assertEqual(RunStore(database).get("queue", "dead")["status"], "interrupted")

    def test_cancel_silent_ns3_child_is_prompt_and_reaped(self):
        real_popen, spawned, children = subprocess.Popen, threading.Event(), []
        def start(*args, **kwargs):
            if "lab.engines.ns3_runner" not in args[0]:
                return real_popen(*args, **kwargs)
            process = real_popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            children.append(process); spawned.set()
            return process
        with patch("lab.engines.run_queue.subprocess.Popen", side_effect=start), \
                patch("lab.engines.manager._ns3_available", return_value=True):
            rid = self.manager.start(self.project, "test", "ns3")["run_id"]
            self.assertTrue(spawned.wait(3))
            begin = time.monotonic()
            self.manager.cancel("queue", rid)
            self.wait(lambda: not self.manager.active)
            self.assertLess(time.monotonic() - begin, 3)
            self.assertIsNotNone(children[0].poll())
            self.assertEqual(self.manager.get("queue", rid)["status"], "cancelled")

    def test_emulation_cleanup_on_callback_failure(self):
        from lab.engines import emulation
        process = Mock()
        process.poll.return_value = None
        stop_events = []
        def fail(driver, sc, prog, log, workers):
            workers.processes.append(process); stop_events.append(workers.stop)
            raise RuntimeError("cancel during measurement")
        with patch.object(emulation, "_run", side_effect=fail), self.assertRaises(RuntimeError):
            emulation.run(None, {})
        process.terminate.assert_called_once()
        process.wait.assert_called_once()
        self.assertTrue(stop_events[0].is_set())


if __name__ == "__main__":
    unittest.main()
