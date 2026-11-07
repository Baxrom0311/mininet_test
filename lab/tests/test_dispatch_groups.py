"""Study admission groups must not occupy workers while waiting for their turn."""

import threading
import time
import unittest
from unittest.mock import patch

from lab.engines.adaptive_executor import AdaptiveExecutor
from lab.engines.resources import Resources


class EmptyMachine:
    def read(self):
        return Resources(4, 4, 4., 1024 ** 3, 0, cpu_utilization=1.)


class DispatchGroupTests(unittest.TestCase):
    def executor(self, *, adaptive=False):
        executor = AdaptiveExecutor(ceiling=2, adaptive=adaptive, monitor=EmptyMachine(), interval=.01)
        self.addCleanup(lambda: executor.shutdown(cancel_futures=True))
        return executor

    def gate(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        return gate

    def until(self, check):
        deadline = time.monotonic() + 5
        while not check():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.005)

    def test_fixed_mode_keeps_manual_capacity_with_zero_ram_and_high_load(self):
        executor = self.executor()
        gate = self.gate()
        jobs = [executor.submit(gate.wait, resource_engine="analytic") for _ in range(2)]
        self.until(lambda: all(job.running() for job in jobs))
        self.assertEqual(executor.status()["mode"], "fixed")
        self.assertEqual(executor.status()["limit"], 2)

    def test_group_limit_skips_waiters_and_other_group_uses_free_worker(self):
        executor = self.executor()
        gate = self.gate()
        first = executor.submit(gate.wait, resource_group="study-a", resource_parallelism=1)
        self.until(first.running)
        waiting = executor.submit(gate.wait, resource_group="study-a", resource_parallelism=1)
        other = executor.submit(lambda: 17, resource_group="study-b", resource_parallelism=1)
        self.assertEqual(other.result(5), 17)
        self.assertFalse(waiting.running())
        self.assertTrue(waiting.cancel())
        gate.set()
        first.result(5)
        self.until(lambda: not executor._group_limits)
        self.assertEqual(executor.status()["active_groups"], {})

    def test_live_engine_serializes_without_blocking_other_work_in_fixed_mode(self):
        executor = self.executor()
        gate = self.gate()
        first = executor.submit(gate.wait, resource_engine="mininet")
        self.until(first.running)
        waiting = executor.submit(gate.wait, resource_engine="mininet")
        other = executor.submit(lambda: 28, resource_engine="ns3")
        self.assertEqual(other.result(5), 28)
        self.assertFalse(waiting.running())
        self.assertTrue(waiting.cancel())

    def test_cancelled_resource_blocked_group_releases_its_metadata(self):
        executor = self.executor(adaptive=True)
        waiting = executor.submit(lambda: self.fail("blocked work ran"), resource_group="study", resource_parallelism=1)
        self.assertTrue(waiting.cancel())
        self.until(lambda: not executor._group_limits)
        self.assertFalse(executor._outstanding_groups)
        self.assertEqual(executor.status()["queued"], 0)

    def test_conflicting_or_invalid_group_limits_reject_without_disturbing_admitted_work(self):
        executor = self.executor()
        gate = self.gate()
        first = executor.submit(gate.wait, resource_group="study", resource_parallelism=1)
        self.until(first.running)
        with self.assertRaisesRegex(ValueError, "consistent"):
            executor.submit(lambda: None, resource_group="study", resource_parallelism=2)
        for group, limit in ((None, 1), ("", 1), ("new", 0), ("new", True), ("new", 65)):
            with self.subTest(group=group, limit=limit), self.assertRaises(ValueError):
                executor.submit(lambda: None, resource_group=group, resource_parallelism=limit)
        self.assertEqual(executor._outstanding_groups, {"study": 1})

    def test_dispatch_failure_releases_group_and_allows_a_new_limit(self):
        executor = self.executor()
        with patch.object(executor._pool, "submit", side_effect=RuntimeError("dispatch failed")):
            failed = executor.submit(lambda: None, resource_group="study", resource_parallelism=1)
            with self.assertRaisesRegex(RuntimeError, "dispatch failed"):
                failed.result(5)
        self.until(lambda: not executor._group_limits)
        self.assertEqual(executor.submit(lambda: 5, resource_group="study", resource_parallelism=2).result(5), 5)


if __name__ == "__main__":
    unittest.main()
