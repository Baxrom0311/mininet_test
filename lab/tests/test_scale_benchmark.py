"""Synthetic scale evidence tests; these never start Mininet, FRR or namespaces."""

import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lab.benchmarks import real_scale as real
from lab.benchmarks import scale_resources as resources


def process_stat(pid=7, *, cpu=10, identity=100, threads=2):
    fields = ["S"] + ["0"] * 20
    fields[11], fields[12], fields[17], fields[19] = str(cpu), "0", str(threads), str(identity)
    return f"{pid} (name with ) parentheses) " + " ".join(fields)


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.proc = Path(self.temp.name)
        path = self.proc / "7"
        path.mkdir()
        (path / "stat").write_text(process_stat())
        (path / "statm").write_text("10 3 0 0 0")
        (path / "fd").mkdir()
        (path / "fd/0").touch()

    def test_complete_process_and_ordinary_exit_race(self):
        errors = []
        usage, ticks = resources.process_usage({7, 999}, proc=self.proc, on_error=lambda *e: errors.append(e))
        self.assertEqual(usage, {"rss_bytes": 3 * os.sysconf("SC_PAGE_SIZE"), "fds": 1,
                                 "threads": 2, "processes": 1})
        self.assertEqual(ticks, {(7, "100"): 10})
        self.assertEqual(errors, [])

    def test_incomplete_reads_do_not_leave_partial_counters_and_report_errors(self):
        errors = []
        (self.proc / "7/statm").write_text("malformed")
        usage, ticks = resources.process_usage({7}, proc=self.proc, on_error=lambda *e: errors.append(e))
        self.assertTrue(errors)
        self.assertFalse(ticks)
        self.assertTrue(all(value == 0 for value in usage.values()))
        (self.proc / "7/statm").write_text("10 -3")
        usage, ticks = resources.process_usage({7}, proc=self.proc, on_error=lambda *e: errors.append(e))
        self.assertEqual(usage["rss_bytes"], 0)
        self.assertEqual(len(errors), 2)

    def test_permission_failure_is_not_a_process_exit(self):
        errors = []
        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            resources.process_usage({7}, proc=self.proc, on_error=lambda *e: errors.append(e))
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0][1], PermissionError)

    def test_pid_replacement_during_read_is_discarded(self):
        with patch.object(Path, "read_text", side_effect=[process_stat(), "10 3", process_stat(identity=101)]):
            usage, ticks = resources.process_usage({7}, proc=self.proc)
        self.assertFalse(ticks)
        self.assertEqual(usage["processes"], 0)


class SamplerTests(unittest.TestCase):
    def sampler(self):
        sampler = resources.Sampler(SimpleNamespace(net=None), interval=.01)
        return sampler

    def test_real_local_process_is_sampled_and_thread_is_joined(self):
        sampler = self.sampler()
        with patch.object(sampler, "_pids", return_value=({os.getpid()}, set())):
            with sampler:
                sampler.sample()
                sampler.check()
        self.assertFalse(sampler.thread.is_alive())
        summary = sampler.summary()
        self.assertTrue(summary["valid"], summary)
        self.assertGreaterEqual(summary["samples"], 3)
        self.assertGreater(summary["peak_rss_bytes"], 0)

    def test_background_discovery_failure_invalidates_evidence(self):
        sampler = self.sampler()
        with patch.object(sampler, "_pids", side_effect=OSError("proc unavailable")):
            with sampler:
                deadline = time.monotonic() + 3
                while sampler.error_count < 2 and time.monotonic() < deadline:
                    time.sleep(.005)
                with self.assertRaisesRegex(RuntimeError, "resource evidence incomplete"):
                    sampler.check()
        summary = sampler.summary()
        self.assertFalse(summary["valid"])
        self.assertGreaterEqual(summary["sampling_error_count"], 2)
        self.assertIn("proc unavailable", summary["sampling_errors"][0])
        self.assertFalse(sampler.thread.is_alive())

    def test_sampler_startup_failure_and_bounded_error_log(self):
        sampler = self.sampler()
        with patch.object(sampler, "_pids", side_effect=OSError("unreadable")), \
                patch.object(sampler.thread, "start", side_effect=RuntimeError("thread limit")):
            with sampler:
                for _ in range(30):
                    sampler.sample()
        self.assertFalse(sampler.summary()["valid"])
        self.assertEqual(len(sampler.summary()["sampling_errors"]), 20)
        self.assertGreater(sampler.summary()["sampling_error_count"], 30)

    def test_cpu_identity_history_prevents_recounting_a_temporarily_missing_pid(self):
        sampler = self.sampler()
        owned = {"rss_bytes": 1, "fds": 1, "threads": 1, "processes": 1}
        empty = {**owned, "processes": 0}
        reads = []
        for ticks in ({(7, "100"): 100}, {}, {(7, "100"): 130}, {(7, "101"): 5}):
            reads.extend([(owned if ticks else empty, ticks), (empty, {})])
        with patch.object(sampler, "_pids", return_value=({7}, set())), \
                patch.object(resources, "process_usage", side_effect=reads):
            for _ in range(4):
                sampler.sample()
        self.assertAlmostEqual(sampler.cpu_seconds, 35 / os.sysconf("SC_CLK_TCK"))

    def test_namespace_discovery_includes_daemon_and_excludes_shared_ovs(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            for pid, comm in ((200, "bash"), (201, "ospfd"), (300, "unrelated"), (400, "ovs-vswitchd")):
                path = proc / str(pid)
                (path / "ns").mkdir(parents=True)
                (path / "comm").write_text(comm)
                if pid in (201, 400):
                    os.link(proc / "200/ns/net", path / "ns/net")
                else:
                    (path / "ns/net").touch()
            node = SimpleNamespace(inNamespace=True, pid=200)
            sampler = resources.Sampler(SimpleNamespace(net=SimpleNamespace(hosts=[node])), proc=proc)
            owned, ovs = sampler._pids()
            self.assertEqual(owned, {os.getpid(), 200, 201})
            self.assertEqual(ovs, {400})
            self.assertEqual(sampler.error_count, 0)


class StubSampler:
    def __init__(self):
        self.data = {"valid": True, "peak_rss_bytes": 10, "sampling_errors": [], "samples": 3}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def sample(self):
        pass

    def check(self):
        pass

    def summary(self):
        return self.data


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        self.args = SimpleNamespace(hosts=1, flows=2, duration=3, timeout=30, settle=1, rss_budget_mb=2048)
        self.driver = Mock()
        self.driver.net.get.return_value.cmd.side_effect = lambda command: (
            "1 packets transmitted, 1 received, 0% packet loss" if command.startswith("ping") else "Full/DR\n" * 3)
        self.sampler = StubSampler()
        self.traffic = {"flows": {f"f{i}": {"summary": {"avg_thr_bps": 1000}} for i in range(2)}}
        for target, value in (("lab.drivers.mininet_driver.MininetDriver", self.driver),
                              ("lab.benchmarks.real_scale.Sampler", self.sampler),
                              ("lab.engines.emulation.run", self.traffic)):
            mocked = patch(target, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)
        mocked = patch.object(real.signal, "alarm")
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_valid_measurement_and_link_command_included_in_probe(self):
        self.driver.update_link.side_effect = lambda *a, **kw: time.sleep(.02)
        result = real.measure(3, self.args)
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["link_failure_probe_method"], "before_link_command_v2")
        self.assertGreaterEqual(result["link_failure_probe_s"], .02)
        self.driver.stop.assert_called_once()

    def test_sampler_failure_cannot_pass_traffic_success(self):
        self.sampler.data.update(valid=False, sampling_errors=["broken monitor"])
        result = real.measure(3, self.args)
        self.assertEqual(result["status"], "error")
        self.assertIn("incomplete", result["resource_error"])
        self.driver.stop.assert_called_once()

    def test_sampler_initial_failure_prevents_network_start(self):
        with patch.object(self.sampler, "check", side_effect=RuntimeError("monitor failed")):
            result = real.measure(3, self.args)
        self.assertEqual(result["status"], "error")
        self.driver.start.assert_not_called()
        self.driver.stop.assert_called_once()

    def test_cleanup_failure_keeps_original_timeout_and_resource_evidence(self):
        self.driver.start.side_effect = TimeoutError("startup timed out")
        self.driver.stop.side_effect = RuntimeError("cleanup failed")
        self.sampler.data["peak_rss_bytes"] = 3 * 1024 ** 3
        result = real.measure(3, self.args)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "TimeoutError: startup timed out")
        self.assertIn("cleanup failed", result["cleanup_error"])
        self.assertTrue(result["rss_budget_exceeded"])
        self.assertIn("resources", result)

    def test_cleanup_failure_after_success_is_an_error(self):
        self.driver.stop.side_effect = RuntimeError("stop failed")
        result = real.measure(3, self.args)
        self.assertEqual(result["status"], "error")
        self.assertIn("stop failed", result["error"])

    def test_successful_traffic_over_the_rss_budget_is_not_a_pass(self):
        self.sampler.data["peak_rss_bytes"] = 3 * 1024 ** 3
        result = real.measure(3, self.args)
        self.assertEqual(result["status"], "budget_exceeded")
        self.assertTrue(result["rss_budget_exceeded"])
        self.driver.stop.assert_called_once()

    def test_interrupt_cleans_driver_and_preserves_interrupted_status(self):
        self.driver.start.side_effect = KeyboardInterrupt("cancelled")
        result = real.measure(3, self.args)
        self.assertEqual(result["status"], "interrupted")
        self.driver.stop.assert_called_once()

    def test_missing_or_nonfinite_traffic_cannot_pass(self):
        for value in (None, 0, -1, True, float("nan"), float("inf")):
            with self.subTest(value=value):
                self.traffic["flows"]["f0"]["summary"]["avg_thr_bps"] = value
                result = real.measure(3, self.args)
                self.assertEqual(result["status"], "error")
                json.dumps(result, allow_nan=False)
        self.traffic["flows"]["f0"]["summary"] = {"avg_thr_bps": 1000, "avg_delay_ms": float("nan")}
        result = real.measure(3, self.args)
        self.assertEqual(result["status"], "error")
        json.dumps(result, allow_nan=False)
        del self.traffic["flows"]["f0"]
        self.assertEqual(real.measure(3, self.args)["status"], "error")


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "report.json"
        self.argv = ["--routers", "10", "20", "30", "--output", str(self.output)]
        self.stdout = patch("sys.stdout", new_callable=io.StringIO)
        self.stdout.start()
        self.addCleanup(self.stdout.stop)

    def test_prerequisites_block_without_measurement(self):
        with patch.object(real, "prerequisites", return_value=["root required"]), patch.object(real, "measure") as measure:
            self.assertEqual(real.main.__wrapped__(self.argv), 2)
            measure.assert_not_called()
        report = json.loads(self.output.read_text())
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["maximum_capacity_established"])

    def test_each_result_is_checkpointed_and_failure_stops_later_networks(self):
        def measure(n, args):
            checkpoint = json.loads(self.output.read_text())
            self.assertEqual(checkpoint["active_routers"], n)
            self.assertEqual(len(checkpoint["runs"]), 0 if n == 10 else 1)
            if n == 20:
                raise RuntimeError("constructor failure")
            return {"routers": n, "status": "passed"}
        before_alarm, before_term = signal.getsignal(signal.SIGALRM), signal.getsignal(signal.SIGTERM)
        with patch.object(real, "prerequisites", return_value=[]), patch.object(real, "measure", side_effect=measure) as run:
            self.assertEqual(real.main.__wrapped__(self.argv), 1)
        self.assertEqual(run.call_count, 2)
        report = json.loads(self.output.read_text())
        self.assertEqual(report["schema_version"], 2)
        self.assertEqual([r["status"] for r in report["runs"]], ["passed", "error"])
        self.assertEqual(report["status"], "failed")
        self.assertTrue(report["stopped_early"])
        self.assertIsNone(report["active_routers"])
        self.assertEqual(signal.getsignal(signal.SIGALRM), before_alarm)
        self.assertEqual(signal.getsignal(signal.SIGTERM), before_term)

    def test_interrupt_persists_previous_results_and_returns_130(self):
        with patch.object(real, "prerequisites", return_value=[]), \
                patch.object(real, "measure", side_effect=[{"routers": 10, "status": "passed"}, KeyboardInterrupt()]):
            self.assertEqual(real.main.__wrapped__(self.argv), 130)
        report = json.loads(self.output.read_text())
        self.assertEqual(report["status"], "interrupted")
        self.assertEqual([r["status"] for r in report["runs"]], ["passed", "interrupted"])

    def test_pass_requires_all_planned_sizes_and_preserves_settings(self):
        with patch.object(real, "prerequisites", return_value=[]), \
                patch.object(real, "measure", side_effect=lambda n, args: {"routers": n, "status": "passed"}):
            self.assertEqual(real.main.__wrapped__(self.argv), 0)
        report = json.loads(self.output.read_text())
        self.assertEqual(report["status"], "passed")
        self.assertEqual([r["routers"] for r in report["runs"]], [10, 20, 30])
        self.assertEqual(report["settings"]["rss_budget_mb"], 2048)
        self.assertTrue(report["run_id"])
        self.assertFalse(report["stopped_early"])

    def test_atomic_write_failure_preserves_previous_json_and_removes_temporary_file(self):
        real._save(self.output, {"status": "running"})
        with patch.object(real.os, "replace", side_effect=OSError("disk error")), self.assertRaises(OSError):
            real._save(self.output, {"status": "passed"})
        self.assertEqual(json.loads(self.output.read_text()), {"status": "running"})
        self.assertEqual(list(self.output.parent.iterdir()), [self.output])
        with self.assertRaises(ValueError):
            real._save(self.output, {"bad": float("nan")})

    def test_duplicate_topology_sizes_rejected_before_any_network_or_report(self):
        with patch.object(real, "prerequisites") as prerequisites, patch("sys.stderr", new_callable=io.StringIO), \
                self.assertRaises(SystemExit) as error:
            real.main.__wrapped__(["--routers", "10", "10", "--output", str(self.output)])
        self.assertEqual(error.exception.code, 2)
        prerequisites.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_sigterm_subprocess_checkpoints_and_stops_without_root(self):
        code = """
import os, signal, sys
from unittest.mock import patch
from lab.benchmarks import real_scale as real
def measurement(n, args):
    if n == 20:
        os.kill(os.getpid(), signal.SIGTERM)
    return {'routers': n, 'status': 'passed'}
with patch.object(real, 'prerequisites', return_value=[]), patch.object(real, 'measure', side_effect=measurement):
    raise SystemExit(real.main.__wrapped__(['--routers', '10', '20', '30', '--output', sys.argv[1]]))
"""
        child = subprocess.run([sys.executable, "-c", code, str(self.output)], capture_output=True, text=True, timeout=10)
        self.assertEqual(child.returncode, 130, child.stderr)
        report = json.loads(self.output.read_text())
        self.assertEqual(report["status"], "interrupted")
        self.assertEqual([r["routers"] for r in report["runs"]], [10, 20])


if __name__ == "__main__":
    unittest.main()
