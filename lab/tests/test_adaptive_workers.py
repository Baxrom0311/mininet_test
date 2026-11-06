"""Deterministic resource/dispatch tests; no dependence on the test host's CPU count."""

from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from lab.drivers.base import EventBus
from lab.drivers.demo_driver import DemoDriver
from lab.engines.adaptive_executor import AdaptiveExecutor
from lab.engines.resources import MIB, ResourceMonitor, Resources
from lab.engines.run_queue import QueuedRunManager
from lab.engines.run_submission import PreparedRun, submit
from lab.model import empty_project
from lab.server import create_app

GIB = 1024 * MIB


class Monitor:
    def __init__(self, **changes):
        self.snapshot = replace(Resources(32, 32, 32., 64 * GIB, 56 * GIB), **changes)
        self.calls = 0

    def read(self):
        self.calls += 1
        return self.snapshot


class WaitTests(unittest.TestCase):
    def until(self, check):
        deadline = time.monotonic() + 5
        while not check():
            self.assertLess(time.monotonic(), deadline, "dispatch state did not settle")
            time.sleep(.005)


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proc, self.cgroup = self.root / "proc", self.root / "cgroup"
        self.put(self.proc / "self/cgroup", "0::/parent/leaf")
        self.put(self.proc / "meminfo", f"MemTotal: {64 * GIB // 1024} kB\nMemAvailable: {50 * GIB // 1024} kB\n")
        self.put(self.proc / "stat", "cpu0 10 0 0 90 0 0 0 0 100 0\ncpu1 10 0 0 90 0 0 0 0\n")
        (self.cgroup / "parent/leaf").mkdir(parents=True)
        self.clock = 10.
        self.monitor = ResourceMonitor(self.proc, self.cgroup, clock=lambda: self.clock)
        self.affinity = patch("os.sched_getaffinity", return_value={0, 1})
        self.affinity.start()
        self.addCleanup(self.affinity.stop)

    @staticmethod
    def put(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def test_parent_and_leaf_limits_and_actual_cpu_deltas(self):
        self.put(self.cgroup / "cpu.max", "400000 100000")
        self.put(self.cgroup / "parent/cpu.max", "150000 100000")
        self.put(self.cgroup / "parent/cpu.stat", "usage_usec 1000000\n")
        self.put(self.cgroup / "parent/memory.max", str(16 * GIB))
        self.put(self.cgroup / "parent/memory.current", str(14 * GIB))
        self.put(self.cgroup / "parent/leaf/memory.max", str(8 * GIB))
        self.put(self.cgroup / "parent/leaf/memory.current", str(GIB))
        first = self.monitor.read()
        self.assertEqual((first.affinity_cpus, first.effective_cpus, first.cpu_quota), (2, 1.5, 1.5))
        self.assertEqual((first.memory_total_bytes, first.memory_available_bytes), (8 * GIB, 2 * GIB))
        self.assertIsNone(first.cpu_utilization)
        self.clock += 2
        self.put(self.cgroup / "parent/cpu.stat", "usage_usec 4000000\n")
        self.assertEqual(self.monitor.read().cpu_utilization, 1.)

    def test_affinity_load_excludes_guest_double_counting_and_unrelated_cpus(self):
        self.monitor.read()
        self.clock += 1
        self.put(self.proc / "stat", "cpu0 20 0 0 180 0 0 0 0 10000 0\n"
                 "cpu1 20 0 0 180 0 0 0 0\ncpu99 9999999 0 0 0\n")
        self.assertAlmostEqual(self.monitor.read().cpu_utilization, .1)

    def test_container_leaf_mount_and_missing_files(self):
        self.put(self.proc / "self/cgroup", "0::/unavailable-host-path")
        self.put(self.cgroup / "cpu.max", "50000 100000")
        self.assertEqual(self.monitor.read().effective_cpus, .5)
        (self.proc / "meminfo").unlink()
        snapshot = self.monitor.read()
        self.assertIsNone(snapshot.memory_available_bytes)
        self.assertIsNone(snapshot.memory_total_bytes)


class ExecutorTests(WaitTests):
    def executor(self, monitor=None, **kwargs):
        executor = AdaptiveExecutor(monitor=monitor or Monitor(), interval=30, **kwargs)
        self.addCleanup(lambda: executor.shutdown(cancel_futures=True))
        return executor

    def gate(self):
        gate = threading.Event()
        self.addCleanup(gate.set)  # Release running calls before executor cleanup.
        return gate

    def test_large_machine_starts_more_than_two_and_pressure_only_restricts_new_work(self):
        monitor = Monitor(effective_cpus=16)
        executor = self.executor(monitor)
        gate = self.gate()
        jobs = [executor.submit(gate.wait) for _ in range(8)]
        self.until(lambda: executor.status()["active"] == 8)
        monitor.snapshot = replace(monitor.snapshot, effective_cpus=3, cpu_utilization=.99)
        executor.refresh()
        self.until(lambda: executor.status()["limit"] == 2)
        waiting = executor.submit(lambda: "next")
        self.assertEqual(executor.status()["active"], 8)
        self.assertFalse(waiting.running())
        gate.set()
        self.assertEqual(waiting.result(5), "next")
        self.assertTrue(all(job.result(5) for job in jobs))

    def test_limit_grows_with_available_cpu_capacity(self):
        monitor = Monitor(effective_cpus=2)
        executor = self.executor(monitor)
        self.assertEqual(executor.status()["limit"], 2)
        monitor.snapshot = replace(monitor.snapshot, effective_cpus=16, cpu_utilization=.1)
        executor.refresh()
        self.until(lambda: executor.status()["limit"] == 3)
        executor.refresh()
        self.until(lambda: executor.status()["limit"] == 4)

    def test_memory_pause_resume_and_queued_cancellation(self):
        monitor = Monitor(memory_total_bytes=8 * GIB, memory_available_bytes=4 * GIB)
        executor = self.executor(monitor)
        gate = self.gate()
        first, second, cancelled = [executor.submit(gate.wait) for _ in range(3)]
        self.until(lambda: first.running())
        self.assertFalse(second.running())
        self.assertTrue(cancelled.cancel())
        monitor.snapshot = replace(monitor.snapshot, memory_available_bytes=0)
        executor.refresh()
        self.until(lambda: executor.status()["limit"] == 0)
        gate.set()
        first.result(5)
        self.assertFalse(second.running())
        monitor.snapshot = replace(monitor.snapshot, memory_available_bytes=6 * GIB, cpu_utilization=.8)
        executor.refresh()
        self.assertTrue(second.result(5))
        self.assertTrue(cancelled.cancelled())

    def test_engine_caps_allow_other_engines_past_blocked_jobs(self):
        executor = self.executor(Monitor(effective_cpus=4))
        gate = self.gate()
        first = executor.submit(gate.wait, resource_engine="analytic")
        self.until(first.running)
        blocked = executor.submit(gate.wait, resource_engine="analytic")
        ns3 = executor.submit(gate.wait, resource_engine="ns3")
        live = executor.submit(gate.wait, resource_engine="mininet")
        blocked_live = executor.submit(gate.wait, resource_engine="mininet")
        self.until(lambda: ns3.running() and live.running())
        self.assertFalse(blocked.running())
        self.assertFalse(blocked_live.running())
        self.assertEqual(executor.status()["active_engines"], {"analytic": 1, "ns3": 1, "mininet": 1})

    def test_estimated_reservations_survive_samples_until_job_finishes(self):
        monitor = Monitor(memory_total_bytes=8 * GIB, memory_available_bytes=8 * GIB)
        executor = self.executor(monitor)
        gate = self.gate()
        jobs = [executor.submit(gate.wait) for _ in range(4)]
        self.until(lambda: executor.status()["active"] == 3)
        self.assertEqual(executor.status()["estimated_reserved_bytes"], 6 * GIB)
        before = monitor.calls
        executor.refresh()
        self.until(lambda: monitor.calls > before)
        self.assertEqual(executor.status()["active"], 3)
        self.assertFalse(jobs[3].running())
        gate.set()
        for job in jobs[:3]:
            job.result(5)
        executor.refresh()
        self.assertTrue(jobs[3].result(5))
        self.until(lambda: executor.status()["estimated_reserved_bytes"] == 0)

    def test_unknown_ram_is_conservative_and_sensor_failure_is_visible(self):
        monitor = Monitor(memory_available_bytes=None)
        executor = self.executor(monitor)
        self.assertEqual(executor.status()["limit"], 1)
        with patch.object(monitor, "read", side_effect=OSError("sensor missing")):
            executor.refresh()
            self.until(lambda: executor.status()["sensor_error"] is not None)
            self.assertEqual(executor.submit(lambda: 7).result(5), 7)

    def test_dispatch_failure_completes_future_and_stale_work_cannot_execute(self):
        executor = self.executor()
        stored = []
        def fail(fn, task):
            stored.append((fn, task))
            raise RuntimeError("thread start failed")
        with patch.object(executor._pool, "submit", side_effect=fail):
            future = executor.submit(lambda: self.fail("abandoned call executed"))
            with self.assertRaisesRegex(RuntimeError, "thread start failed"):
                future.result(5)
        stored[0][0](stored[0][1])
        self.assertEqual(executor.status()["active"], 0)
        self.assertEqual(executor.submit(lambda: 3).result(5), 3)

    def test_completion_callbacks_do_not_hold_dispatch_lock(self):
        executor = self.executor()
        gate = self.gate()
        callback_entered, status_done = threading.Event(), threading.Event()
        outer = threading.Lock()
        future = executor.submit(gate.wait)
        def callback(done):
            callback_entered.set()
            with outer:
                pass
        future.add_done_callback(callback)
        with outer:
            gate.set()
            self.assertTrue(callback_entered.wait(5))
            def status():
                executor.status()
                status_done.set()
            thread = threading.Thread(target=status, daemon=True)
            thread.start()
            completed = status_done.wait(2)
        thread.join(5)
        self.assertTrue(completed, "completion callback held dispatch lock")

    def test_nonwaiting_shutdown_cancels_memory_blocked_work(self):
        executor = self.executor(Monitor(memory_available_bytes=0))
        future = executor.submit(lambda: None)
        executor.shutdown(wait=False, cancel_futures=True)
        self.assertTrue(future.cancelled())
        executor._dispatcher.join(5)
        self.assertFalse(executor._dispatcher.is_alive())
        with self.assertRaises(RuntimeError):
            executor.submit(lambda: None)


class QueueIntegrationTests(WaitTests):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = patch("lab.engines.manager.RESULTS_DIR", self.temp.name)
        self.paths.start()
        self.addCleanup(self.paths.stop)
        self.environment = patch.dict("os.environ", {"NETLAB_RUN_WORKERS": "auto", "NETLAB_RUN_MAX_WORKERS": "64",
                                                   "NETLAB_RUN_MEMORY_MB": "2048", "NETLAB_NS3_PARALLEL": ""})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def manager(self, monitor=None, **kwargs):
        bus = EventBus()
        manager = QueuedRunManager(bus, DemoDriver(bus), resource_monitor=monitor or Monitor(), **kwargs)
        self.addCleanup(manager.close)
        return manager

    def job(self, rid):
        project = empty_project("adaptive")
        scenario = {"duration": 1, "apps": [], "events": []}
        return PreparedRun(rid, "adaptive", project, "test", scenario, "ns3")

    def test_auto_default_study_producer_and_explicit_override(self):
        manager = self.manager()
        self.assertEqual(manager.resource_status()["mode"], "auto")
        self.assertGreater(manager.study_parallelism("ns3"), 4)
        self.assertEqual(manager.study_parallelism("analytic"), 1)
        with patch.dict("os.environ", {"NETLAB_NS3_PARALLEL": "7"}):
            self.assertEqual(manager.study_parallelism("ns3"), 7)
        fixed = self.manager(workers=5)
        self.assertEqual(fixed.resource_status()["limit"], 5)
        self.assertEqual(fixed.resource_status()["mode"], "fixed")
        self.assertEqual(fixed.study_parallelism("ns3"), 5)

    def test_dispatch_failure_cleans_metadata_and_payload_ownership(self):
        manager = self.manager()
        with patch.object(manager.executor._pool, "submit", side_effect=RuntimeError("dispatch fault")):
            future = submit(manager, [self.job("broken")])["broken"]
            with self.assertRaisesRegex(RuntimeError, "dispatch fault"):
                future.result(5)
            self.until(lambda: not manager.futures)
        self.assertEqual(manager.store.get("adaptive", "broken")["status"], "error")
        self.assertFalse(manager.payloads)
        self.assertFalse(manager.tokens)
        self.assertFalse(manager.active)

    def test_memory_blocked_jobs_stay_queued_and_cancel_without_starting(self):
        manager = self.manager(Monitor(memory_available_bytes=0))
        with patch.object(manager, "_execute") as execute:
            future = submit(manager, [self.job("waiting")])["waiting"]
            self.assertEqual(manager.store.get("adaptive", "waiting")["status"], "queued")
            manager.cancel("adaptive", "waiting")
            self.assertTrue(future.cancelled())
            self.assertEqual(manager.store.get("adaptive", "waiting")["status"], "cancelled")
            self.assertFalse(manager.payloads)
            execute.assert_not_called()

    def test_resource_endpoint_authentication_and_serializable_status(self):
        with TestClient(create_app("demo", token="test")) as client:
            self.assertEqual(client.get("/api/resources").status_code, 401)
            client.cookies.set("netlab_token", "test")
            response = client.get("/api/resources")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["mode"], "auto")
            self.assertIn("effective_cpus", response.json()["resources"])

    def test_invalid_configuration_fails_before_starting_executor(self):
        for workers in (0, False, -1, 65, 1.5, "", "many"):
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                self.manager(workers=workers)
        for key, value in (("NETLAB_RUN_MAX_WORKERS", "0"), ("NETLAB_RUN_MEMORY_MB", "0"),
                           ("NETLAB_NS3_PARALLEL", "1000000")):
            with self.subTest(key=key), patch.dict("os.environ", {key: value}), self.assertRaises(ValueError):
                self.manager()


if __name__ == "__main__":
    unittest.main()
