"""Synthetic lifecycle/race regressions; local Python children, no Mininet/root."""

from concurrent.futures import ThreadPoolExecutor
import io
import select
import signal
import subprocess
import sys
import threading
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lab.engines import emulation
from lab.engines import manager
from lab.engines.emulation_workers import EmulationStopped, EmulationWorkers
from lab.drivers.base import EventBus
from lab.model import empty_project


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.workers = EmulationWorkers()
        self.addCleanup(self.workers.close)

    def test_scheduled_failure_is_an_error_with_context(self):
        def fail():
            raise OSError("synthetic link update failed")
        worker = self.workers.schedule(0, fail, "event link_down l1")
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        with self.assertRaisesRegex(RuntimeError, "event link_down l1.*synthetic link update failed"):
            self.workers.finish()

    def test_future_launch_is_cancelled_and_close_is_idempotent(self):
        call = Mock()
        self.workers.schedule(3600, call, "future app")
        self.workers.close(timeout=1)
        self.workers.close(timeout=1)
        call.assert_not_called()
        with patch("lab.engines.emulation_workers.subprocess.Popen") as popen:
            with self.assertRaises(EmulationStopped):
                self.workers.spawn(["must-not-start"])
            popen.assert_not_called()

    def test_inflight_callback_cannot_spawn_after_close(self):
        entered, resume = threading.Event(), threading.Event()
        def callback():
            entered.set()
            self.assertTrue(resume.wait(2))
            self.workers.spawn(["must-not-start"])
        worker = self.workers.schedule(0, callback, "delayed launch")
        self.assertTrue(entered.wait(2))
        with ThreadPoolExecutor(max_workers=1) as pool:
            closed = pool.submit(self.workers.close)
            self.assertTrue(self.workers.stop.wait(2))
            with patch("lab.engines.emulation_workers.subprocess.Popen") as popen:
                resume.set()
                closed.result(timeout=3)
                popen.assert_not_called()
        self.assertFalse(worker.is_alive())

    def test_close_cannot_miss_process_between_popen_and_registration(self):
        entered, resume = threading.Event(), threading.Event()
        process = Mock(stdout=io.StringIO(""))
        process.poll.return_value = None
        def popen(*args, **kwargs):
            entered.set()
            if not resume.wait(2):
                raise RuntimeError("fixture rendezvous failed")
            return process
        with ThreadPoolExecutor(max_workers=2) as pool, patch("lab.engines.emulation_workers.subprocess.Popen", side_effect=popen):
            launched = pool.submit(self.workers.spawn, ["synthetic command"])
            self.assertTrue(entered.wait(2))
            closed = pool.submit(self.workers.close)
            resume.set()
            self.assertIs(launched.result(timeout=3), process)
            closed.result(timeout=3)
        process.terminate.assert_called_once()
        process.wait.assert_called_once()
        self.assertTrue(process.stdout.closed)

    def test_reader_drains_before_finish_and_eof_is_processed(self):
        process = self.workers.spawn([sys.executable, "-u", "-c", "print('final metric',flush=True)"])
        values, done = [], threading.Event()
        self.workers.reader(process, values.append, done.set)
        self.assertTrue(done.wait(3))
        self.workers.finish()
        self.assertEqual(values, ["final metric"])
        self.assertTrue(process.stdout.closed)

    def test_reader_parser_error_is_not_silent(self):
        process = self.workers.spawn([sys.executable, "-c", "print('bad fixture metric')"])
        def bad(line):
            raise ValueError("synthetic parser failure")
        thread = self.workers.reader(process, bad)
        thread.join(timeout=3)
        with self.assertRaisesRegex(RuntimeError, "parser.*synthetic parser failure"):
            self.workers.finish()
        self.assertIsNotNone(process.poll())

    @unittest.skipUnless(sys.platform == "linux", "signal semantics required")
    def test_stubborn_child_is_killed_reaped_and_pipe_closed(self):
        script = "import signal,time\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\nprint('ready',flush=True)\nwhile True: time.sleep(1)"
        process = self.workers.spawn([sys.executable, "-u", "-c", script])
        self.assertTrue(select.select([process.stdout], [], [], 3)[0])
        self.assertEqual(process.stdout.readline().strip(), "ready")
        self.workers.close(timeout=.5)
        self.assertEqual(process.returncode, -signal.SIGKILL)
        self.assertTrue(process.stdout.closed)

    def test_unfinished_control_callback_is_reported_as_cleanup_failure(self):
        entered, resume = threading.Event(), threading.Event()
        def callback():
            entered.set()
            resume.wait(3)
        worker = self.workers.schedule(0, callback, "blocked driver callback")
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaisesRegex(RuntimeError, "worker did not stop.*blocked driver"):
                self.workers.close(timeout=.01)
        finally:
            resume.set()
            worker.join(timeout=2)

    def test_primary_cancellation_survives_cleanup_error(self):
        class Cancelled(Exception):
            pass
        with patch.object(emulation, "_run", side_effect=Cancelled("fixture cancellation")), \
                patch.object(EmulationWorkers, "close", side_effect=RuntimeError("fixture cleanup error")):
            with self.assertRaises(Cancelled) as caught:
                emulation.run(None, {})
        self.assertIn("fixture cleanup error", caught.exception.__notes__[0])

    def test_secondary_cleanup_diagnostic_is_saved_in_run_json(self):
        project = empty_project("worker-notes")
        sc = {"duration": 1, "apps": [], "events": []}
        project["scenarios"]["test"] = sc
        error = RuntimeError("synthetic primary error")
        error.add_note("synthetic cleanup failure")
        with tempfile.TemporaryDirectory(prefix="worker-notes-") as td, \
                patch.object(manager, "RESULTS_DIR", td), patch("lab.engines.analytic.run", side_effect=error):
            runs = manager.RunManager(EventBus(), SimpleNamespace(name="demo"))
            runs._worker("worker-note", "worker-notes", project, "test", sc, "analytic")
            result = runs.get("worker-notes", "worker-note")
        self.assertEqual(result["status"], "error")
        self.assertIn("synthetic primary error", result["error"])
        self.assertEqual(result["error_notes"], ["synthetic cleanup failure"])


class EngineTests(unittest.TestCase):
    def driver(self):
        return SimpleNamespace(name="real", running=True, lock=threading.RLock(),
            net=SimpleNamespace(get=lambda name: SimpleNamespace(pid=999999)),
            project={"name": "synthetic-lifecycle", "devices": {
                "h1": {"type": "host", "config": {"interfaces": {"h1-eth1": {"ip": "192.0.2.1/24"}}}},
                "h2": {"type": "host", "config": {"interfaces": {"h2-eth1": {"ip": "192.0.2.2/24"}}}}},
                "links": {"l1": {"a": {"dev": "h1", "port": 1}, "b": {"dev": "h2", "port": 1}, "params": {}}}},
            update_link=Mock())

    def execute(self, driver, events=None):
        sc = {"duration": 1, "apps": [], "events": events if events is not None else [{"t": 0, "action": "link_down", "link": "l1"}]}
        with patch.object(emulation, "_SERVER_STARTUP_S", 0), patch.object(emulation, "_OUTPUT_DRAIN_S", 0), \
                patch("lab.drivers.mininet_driver._read_net_dev", return_value={}), \
                patch("lab.engines.nodestats.read_proc_routes", return_value=set()), \
                patch("lab.engines.nodestats.read_proc_snmp", return_value={}):
            return emulation.run(driver, sc)

    def test_full_engine_event_failure_does_not_return_success(self):
        driver = self.driver()
        driver.update_link.side_effect = OSError("synthetic tc rejected update")
        with self.assertRaisesRegex(RuntimeError, "event t=0 link_down l1.*synthetic tc rejected update"):
            self.execute(driver)
        driver.update_link.assert_called_once_with("l1", up=False)

    def test_successful_event_keeps_requested_and_actual_time(self):
        driver = self.driver()
        result = self.execute(driver)
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["t"], 0)
        self.assertGreaterEqual(result["events"][0]["actual_t"], 0)
        self.assertEqual(result["duration"], 1)

    def test_full_engine_mobility_failure_is_not_silent(self):
        driver = self.driver()
        driver.wifi_refresh = Mock(side_effect=OSError("synthetic radio update failed"))
        with self.assertRaisesRegex(RuntimeError, "movement failed.*synthetic radio update failed"):
            self.execute(driver, events=[{"t": 0, "action": "move", "device": "h1", "x": 1, "y": 1}])
        driver.wifi_refresh.assert_called_once()


if __name__ == "__main__":
    unittest.main()
