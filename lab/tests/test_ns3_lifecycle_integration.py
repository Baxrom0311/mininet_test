"""Exercise manager entry points using actual local children instead of ns-3."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from lab.drivers.base import EventBus
from lab.drivers.demo_driver import DemoDriver
from lab.engines import ns3_process
from lab.engines.manager import RunManager
from lab.engines.run_queue import QueuedRunManager
from lab.model import empty_project

REAL_POPEN = subprocess.Popen


class ManagerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        paths = patch("lab.engines.manager.RESULTS_DIR", self.temp.name)
        paths.start()
        self.addCleanup(paths.stop)
        timeout = patch.object(ns3_process, "TERM_TIMEOUT", .1)
        timeout.start()
        self.addCleanup(timeout.stop)
        self.children = []
        self.addCleanup(self.reap)

    def reap(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)
            child.stdout.close()

    def spawn(self, command, **kwargs):
        script = 'import time; print(\'{"progress":0.25}\', flush=True); time.sleep(30)'
        child = REAL_POPEN([sys.executable, "-u", "-c", script], **kwargs)
        self.children.append(child)
        return child

    def test_legacy_manager_callback_failure_reaps_subprocess(self):
        bus = EventBus()
        manager = RunManager(bus, DemoDriver(bus))
        def fail(value):
            raise RuntimeError("callback failed in legacy runner")
        with patch.object(ns3_process.subprocess, "Popen", side_effect=self.spawn), \
                self.assertRaisesRegex(RuntimeError, "callback failed in legacy runner"):
            manager._run_ns3({}, {}, fail, lambda message: None)
        self.assertIsNotNone(self.children[0].poll())
        self.assertTrue(self.children[0].stdout.closed)

    def test_queue_persists_primary_error_and_cleanup_notes(self):
        bus = EventBus()
        manager = QueuedRunManager(bus, DemoDriver(bus), workers=1)
        self.addCleanup(manager.close)
        project = empty_project("ns3lifecycle")
        project["scenarios"]["test"] = {"duration": 1, "events": [], "apps": []}
        real_killpg = os.killpg
        def signal_group(pid, signum):
            if signum == signal.SIGTERM:
                raise PermissionError("injected group TERM failure")
            return real_killpg(pid, signum)
        def bad_progress(command, **kwargs):
            child = REAL_POPEN([sys.executable, "-u", "-c",
                               'import time; print(\'{"progress":2}\', flush=True); time.sleep(30)'], **kwargs)
            self.children.append(child)
            return child
        with patch.object(ns3_process.subprocess, "Popen", side_effect=bad_progress), \
                patch.object(ns3_process.os, "killpg", side_effect=signal_group), \
                patch("lab.engines.manager._ns3_available", return_value=True):
            rid = manager.start(project, "test", "ns3")["run_id"]
            deadline = time.monotonic() + 5
            while manager.active:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.01)
        result = manager.get("ns3lifecycle", rid)
        self.assertEqual(result["status"], "error")
        self.assertIn("progress", result["error"])
        self.assertTrue(any("group TERM failure" in note for note in result["error_notes"]))
        self.assertTrue(Path(self.temp.name, "ns3lifecycle", rid + ".json").is_file())
        self.assertFalse(manager.payloads)
        self.assertIsNotNone(self.children[0].poll())
        self.assertTrue(self.children[0].stdout.closed)


if __name__ == "__main__":
    unittest.main()
