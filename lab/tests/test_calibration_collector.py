"""Synthetic collector/probe tests. No root, network namespace or FRR is used."""

import csv
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lab.benchmarks import calibration_probes as probes
from lab.benchmarks import real_calibration as real
from lab.benchmarks.calibration_collection import collect
from lab.benchmarks.calibration_probes import PingTrace, Pinger, poll


def trace(*stamps):
    result = PingTrace()
    for sequence, stamp in enumerate(stamps, 1):
        result.record(f"[{stamp:.6f}] 64 bytes from 192.0.2.1: icmp_seq={sequence} ttl=64 time=1 ms")
    return result


class ProbeTests(unittest.TestCase):
    def test_reply_filter_duplicate_sequence_and_completed_gap(self):
        result = trace(100, 100.1, 102.3, 102.4)
        result.record("[102.500000] From 192.0.2.1 icmp_seq=5 Destination Host Unreachable")
        result.record("[102.600000] 64 bytes from 192.0.2.1: icmp_seq=4 ttl=64 time=1 ms (DUP!)")
        self.assertEqual(len(result.times), 4)
        self.assertTrue(result.ready_before(100.2))
        self.assertAlmostEqual(result.gap_after(100.2, 102.5), 2.1)
        self.assertAlmostEqual(result.first_after(100.2), 2.1)

    def test_missing_stale_baseline_and_unresolved_tail_are_not_zero_gaps(self):
        self.assertIsNone(trace(100, 102).gap_after(100.1, 102.1))  # Only one pre-event reply.
        self.assertIsNone(trace(90, 90.1, 102).gap_after(100, 102.1))
        self.assertIsNone(trace(100, 100.1).gap_after(100.2, 102))
        self.assertIsNone(trace(100, 100.1, 100.3).gap_after(100.2, 103))
        self.assertIsNone(trace(100, 100.1, 103).gap_after(100.2, 102))

    def test_backward_wall_clock_invalidates_ping_metrics(self):
        result = trace(100, 100.1, 99, 102)
        self.assertTrue(result.invalid_clock)
        self.assertIsNone(result.first_after(100.2))
        self.assertIsNone(result.gap_after(100.2, 102.1))

    def test_poll_includes_link_command_time_and_does_not_swallow_errors(self):
        clock = SimpleNamespace(monotonic=Mock(side_effect=[105, 105.2]), sleep=Mock())
        with patch.object(probes, "time", clock):
            self.assertAlmostEqual(poll(lambda: True, 10, started_at=100), 5.2)
        with self.assertRaisesRegex(RuntimeError, "route read failed"):
            poll(lambda: (_ for _ in ()).throw(RuntimeError("route read failed")), 1)
        with patch.object(probes, "time", SimpleNamespace(monotonic=Mock(return_value=110), sleep=Mock())):
            self.assertIsNone(poll(lambda: True, 10, started_at=100))

    def new_probe(self, script):
        process = subprocess.Popen([sys.executable, "-u", "-c", script], stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, bufsize=1)
        def reap():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            if not process.stdout.closed:
                process.stdout.close()
        self.addCleanup(reap)
        driver = SimpleNamespace(net=SimpleNamespace(get=lambda host: SimpleNamespace(pid=123)))
        with patch.object(probes.subprocess, "Popen", return_value=process):
            result = Pinger(driver, "fixture", "192.0.2.1")
        self.addCleanup(lambda: result.stop(timeout=.5))
        return result

    def test_real_child_is_reaped_and_reader_closed_when_event_raises(self):
        script = """
import time
for seq in (1, 2):
    print(f'[{time.time():.6f}] 64 bytes from 192.0.2.1: icmp_seq={seq} ttl=64 time=1 ms', flush=True)
    time.sleep(.1)
while True: time.sleep(1)
"""
        p = self.new_probe(script)
        with self.assertRaisesRegex(RuntimeError, "fixture event failed"):
            with p:
                p.wait_ready(timeout=3)
                p.mark_event()
                raise RuntimeError("fixture event failed")
        self.assertIsNotNone(p.p.poll())
        self.assertFalse(p.reader.is_alive())
        self.assertTrue(p.p.stdout.closed)
        p.stop()  # Idempotent; never sends a second signal to a reused PID.

    def test_no_baseline_prevents_event_and_stops_child(self):
        p = self.new_probe("import time\nwhile True: time.sleep(1)")
        with self.assertRaisesRegex(RuntimeError, "baseline"):
            with p:
                p.mark_event()
        self.assertIsNotNone(p.p.poll())
        self.assertFalse(p.reader.is_alive())

    def test_intentionally_down_probe_can_mark_up_event_after_startup(self):
        p = self.new_probe("import time\nprint('PING synthetic fixture',flush=True)\nwhile True: time.sleep(1)")
        with p:
            epoch, monotonic = p.mark_event(require_ready=False)
            self.assertGreater(epoch, 0)
            self.assertGreater(monotonic, 0)
            self.assertFalse(p.ready_before(epoch))
        self.assertIsNone(p.first_after(epoch))

    @unittest.skipUnless(os.name == "posix", "SIGTERM/SIGKILL semantics required")
    def test_stubborn_child_is_killed_and_waited(self):
        script = "import signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nprint('ready',flush=True)\nwhile True: time.sleep(1)"
        process = subprocess.Popen([sys.executable, "-u", "-c", script], stdout=subprocess.PIPE, text=True)
        try:
            self.assertTrue(select.select([process.stdout], [], [], 3)[0])
            self.assertEqual(process.stdout.readline().strip(), "ready")
            driver = SimpleNamespace(net=SimpleNamespace(get=lambda host: SimpleNamespace(pid=123)))
            with patch.object(probes.subprocess, "Popen", return_value=process):
                p = Pinger(driver, "fixture", "192.0.2.1")
            p.stop(timeout=.5)
            self.assertEqual(process.returncode, -signal.SIGKILL)
            self.assertFalse(p.reader.is_alive())
            self.assertTrue(process.stdout.closed)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)

    def test_wall_clock_step_detected_on_stop(self):
        process = Mock(stdout=io.StringIO(""), returncode=0)
        process.poll.return_value = 0
        driver = SimpleNamespace(net=SimpleNamespace(get=lambda host: SimpleNamespace(pid=123)))
        clock = SimpleNamespace(time=Mock(side_effect=[100, 105]), monotonic=Mock(side_effect=[50, 52]))
        with patch.object(probes, "time", clock), patch.object(probes.subprocess, "Popen", return_value=process):
            p = Pinger(driver, "fixture", "192.0.2.1")
            p.stop()
        self.assertTrue(p.trace.invalid_clock)


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="synthetic-collector-")
        self.addCleanup(self.tmp.cleanup)
        self.output, self.report = Path(self.tmp.name, "out.csv"), Path(self.tmp.name, "out.csv.collection.json")
        self.driver = Mock()
        self.driver.capabilities.return_value = {"ovs_datapath": "synthetic", "netem": False}
        self.expected = {"ospf": [("ospf", "route_update_s"), ("ospf", "traffic_gap_s")]}

    def values(self, real_value=1.7):
        return [("ospf", "route_update_s", 0., real_value, 1., 0.),
                ("ospf", "traffic_gap_s", 0., 1.8, 1.5, 0.)]

    def run_collection(self, fn, reps=2):
        return collect(self.driver, {"ospf": fn}, self.expected, ["ospf"], reps, self.output, self.report,
                       "synthetic-unit-fixture", log=lambda message: None)

    def read_rows(self):
        with self.output.open(newline="") as file:
            return list(csv.DictReader(file))

    def test_all_attempts_complete_and_csv_hash_matches(self):
        result = self.run_collection(lambda driver: self.values(), reps=10)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(len(result["attempts"]), 10)
        self.assertEqual(len(self.read_rows()), 20)
        self.assertEqual(self.driver.stop.call_count, 10)
        saved = json.loads(self.report.read_text())
        self.assertEqual(saved["csv_sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())
        self.assertEqual(saved["rows"], 20)
        self.assertEqual(saved["measurement_method"], "event_before_command_v2")
        self.assertTrue(all("method=event_before_command_v2" in row["source"] for row in self.read_rows()))
        self.assertEqual(len({r["source"] for r in self.read_rows()}), 10)
        previous_id = saved["run_id"]
        self.assertNotEqual(self.run_collection(lambda driver: self.values())["run_id"], previous_id)

    def test_missing_metric_is_incomplete_even_with_valid_rows(self):
        result = self.run_collection(lambda driver: self.values(None))
        self.assertEqual(result["status"], "failed")
        self.assertTrue(all(a["status"] == "incomplete" for a in result["attempts"]))
        self.assertEqual(len(self.read_rows()), 2)
        self.assertTrue(all(r["metric"] == "traffic_gap_s" for r in self.read_rows()))

    def test_exception_is_recorded_and_not_hidden_by_later_success(self):
        fn = Mock(side_effect=[RuntimeError("synthetic route timeout"), self.values()])
        result = self.run_collection(fn)
        self.assertEqual(result["status"], "failed")
        self.assertIn("synthetic route timeout", result["attempts"][0]["error"])
        self.assertEqual(result["attempts"][1]["status"], "passed")
        self.assertEqual(self.driver.stop.call_count, 2)

    def test_invalid_and_duplicate_metric_sets_do_not_publish_rows(self):
        for values in ([], [self.values()[0]], [self.values()[0]] * 2,
                       self.values(float("nan")), self.values(float("inf")), self.values(-1), self.values(True)):
            result = self.run_collection(lambda driver: values, reps=1)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(self.read_rows(), [])

    def test_cleanup_failure_stops_subsequent_networks(self):
        self.driver.stop.side_effect = RuntimeError("synthetic cleanup failure")
        fn = Mock(return_value=self.values())
        result = self.run_collection(fn, reps=10)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["stopped_early"])
        self.assertEqual(fn.call_count, 1)
        self.assertIn("cleanup failure", result["attempts"][0]["cleanup_error"])

    def test_interrupt_persists_ledger_and_cleans_up(self):
        with self.assertRaises(KeyboardInterrupt):
            self.run_collection(Mock(side_effect=KeyboardInterrupt()))
        report = json.loads(self.report.read_text())
        self.assertEqual(report["status"], "interrupted")
        self.assertEqual(report["attempts"][0]["status"], "interrupted")
        self.assertEqual(self.driver.stop.call_count, 1)

    def test_invalid_plan_and_overlapping_artifacts_rejected(self):
        for reps, cases in ((0, ["ospf"]), (True, ["ospf"]), (1, []), (1, ["ospf", "ospf"]), (1, ["missing"])):
            with self.assertRaises(ValueError):
                collect(self.driver, {}, {}, cases, reps, self.output, self.report, "fixture")
        with self.assertRaises(ValueError):
            collect(self.driver, {"ospf": lambda d: self.values()}, self.expected, ["ospf"], 1,
                    self.output, self.output, "fixture")

    def test_cli_non_root_and_partial_exit_codes(self):
        args = ["--only", "ospf", "--reps", "1", "--output", str(self.output)]
        with patch.object(real.os, "geteuid", return_value=1000):
            self.assertEqual(real.main(args), 2)
        with patch.object(real.os, "geteuid", return_value=0), patch.object(real, "MininetDriver", return_value=self.driver), \
                patch.dict(real.MEASURES, {"ospf": lambda d: self.values(None)}):
            self.assertEqual(inspect.unwrap(real.main)(args), 1)
        for bad in (["--reps", "0"], ["--only", "ospf", "ospf"], ["--report", str(self.output)]):
            with self.assertRaises(SystemExit) as caught:
                inspect.unwrap(real.main)(["--output", str(self.output), *bad])
            self.assertEqual(caught.exception.code, 2)


class OspfCollectorTests(unittest.TestCase):
    def test_up_and_down_use_distinct_metrics_and_correct_event_order(self):
        for restore in (False, True):
            with self.subTest(restore=restore):
                class Driver:
                    def __init__(self):
                        self.actions = []
                        self.up = True
                    def start(self, project):
                        link = project["links"]["l1"]
                        end = next(link[s] for s in ("a", "b") if link[s]["dev"] == "r1")
                        self.direct = project["devices"]["r1"]["config"]["interfaces"][f"r1-eth{end['port']}"]["ip"].split('/')[0]
                    def update_link(self, link, *, up):
                        self.actions.append((link, up))
                        self.up = up
                driver = Driver()
                probe_started = []
                class Probe:
                    def __init__(self, drv, *args):
                        probe_started.append(drv.up)
                    def __enter__(self):
                        return self
                    def __exit__(self, *args):
                        pass
                    def wait_ready(self):
                        pass
                    def mark_event(self):
                        return 1000, 10
                    def ready_before(self, event):
                        return True
                    def gap_after(self, event):
                        return .1
                starts = []
                def observe(fn, timeout, step=.1, *, started_at=None):
                    self.assertTrue(fn())
                    starts.append(started_at)
                    return .2
                with patch.object(real, "Pinger", Probe), patch.object(real, "wait_ping", return_value=.1), \
                        patch.object(real, "_route_via", side_effect=lambda d, *args: d.direct if d.up else "192.0.2.99"), \
                        patch.object(real, "poll", side_effect=observe), \
                        patch.object(real, "time", SimpleNamespace(sleep=lambda seconds: None)):
                    result = real.m_ospf(driver, restore=restore)
                self.assertEqual(driver.actions, [("l1", False), ("l1", True)] if restore else [("l1", False)])
                self.assertEqual(probe_started, [not restore])
                self.assertEqual(starts[-1], 10)  # Includes the link command in elapsed time.
                self.assertEqual([r[1] for r in result],
                                 ["route_restore_s", "traffic_gap_up_s"] if restore else ["route_update_s", "traffic_gap_s"])

    def test_up_probe_does_not_start_without_alternate_route(self):
        driver = Mock()
        with patch.object(real, "wait_ping", return_value=.1), patch.object(real, "poll", side_effect=[.1, None]), \
                patch.object(real, "_route_via", return_value="192.0.2.1"), patch.object(real, "Pinger") as probe:
            with self.assertRaisesRegex(RuntimeError, "muqobil"):
                real.m_ospf(driver, restore=True)
            probe.assert_not_called()
            driver.update_link.assert_called_once_with("l1", up=False)


if __name__ == "__main__":
    unittest.main()
