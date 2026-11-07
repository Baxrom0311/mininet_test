"""Durable study admission, cancellation and worker sharing without ns-3 or root."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from lab.drivers.base import EventBus
from lab.drivers.demo_driver import DemoDriver
from lab.engines.run_queue import QueuedRunManager
from lab.engines.run_store import RunStore, TERMINAL
from lab.server import create_app
from lab.topogen import generate


def project_fixture():
    project = generate("ring", routers=3, hosts=1, flows=1)
    project["scenarios"] = {"test": {"duration": 1, "events": [], "apps": [
        {"id": "f", "type": "udp", "src": "h0x0", "dst": "h2x0", "rate": "1Mbps", "start": 0, "stop": 1}]}}
    edge = next(lid for lid, link in project["links"].items()
                if "h0x0" in (link["a"]["dev"], link["b"]["dev"]))
    return project, {"kind": "link", "link": edge, "field": "bw"}


class StudyQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = patch("lab.engines.manager.RESULTS_DIR", self.temp.name)
        self.paths.start()
        bus = EventBus()
        self.manager = QueuedRunManager(bus, DemoDriver(bus), workers=2)
        self.project, self.param = project_fixture()
        self.name = self.project["name"]
        self.gates = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        self.manager.close()
        self.paths.stop()
        self.temp.cleanup()

    def wait(self, check):
        deadline = time.monotonic() + 10
        while not check():
            self.assertLess(time.monotonic(), deadline, "study state did not settle")
            time.sleep(.01)

    def start(self, values=(1, 2, 3), **kwargs):
        return self.manager.start_study(self.project, "test", kwargs.pop("engine", "analytic"),
                                        self.param, list(values), **kwargs)

    def blocked_engine(self):
        gate, started, calls = threading.Event(), threading.Event(), []
        self.gates.append(gate)
        def engine(project, scenario, progress, log):
            calls.append(project["links"][self.param["link"]]["params"]["bw"])
            started.set()
            while not gate.wait(.01):
                progress(.1)
            return {"duration": 1, "flows": {}}
        return gate, started, calls, engine

    def test_all_variants_are_durable_and_later_run_cancels_before_first_finishes(self):
        gate, started, calls, engine = self.blocked_engine()
        with patch("lab.engines.analytic.run", side_effect=engine):
            plan = self.start()
            self.assertEqual(plan["runs"], 3)
            self.assertEqual(len(plan["run_ids"]), 3)
            self.assertTrue(started.wait(5))
            rows = [self.manager.store.get(self.name, rid) for rid in plan["run_ids"]]
            self.assertEqual({row["study_id"] for row in rows}, {plan["study_id"]})
            self.assertEqual([row["study_position"] for row in rows], [0, 1, 2])
            self.assertEqual([row["study_total"] for row in rows], [3, 3, 3])
            self.assertEqual([row["status"] for row in rows[1:]], ["queued", "queued"])
            self.assertEqual(len(calls), 1, "study's serial jobs must not occupy other workers")
            cancelled = plan["run_ids"][-1]
            self.manager.cancel(self.name, cancelled)
            self.assertEqual(self.manager.store.get(self.name, cancelled)["status"], "cancelled")
            self.assertNotIn(cancelled, self.manager.payloads)
            gate.set()
            self.wait(lambda: not self.manager.active)
        self.assertEqual(len(calls), 2)
        self.assertEqual([self.manager.get(self.name, rid)["status"] for rid in plan["run_ids"]],
                         ["done", "done", "cancelled"])

    def test_close_cancels_remaining_variants_instead_of_losing_them(self):
        gate, started, calls, engine = self.blocked_engine()
        with patch("lab.engines.analytic.run", side_effect=engine):
            plan = self.start()
            self.assertTrue(started.wait(5))
            self.manager.close()
        rows = [self.manager.store.get(self.name, rid) for rid in plan["run_ids"]]
        self.assertEqual([row["status"] for row in rows], ["cancelled"] * 3)
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.manager.active or self.manager.futures or self.manager.tokens or self.manager.payloads)

    def test_closed_queue_rejects_study_synchronously(self):
        self.manager.close()
        with patch("lab.engines.analytic.run") as engine, self.assertRaises(ValueError):
            self.start()
        engine.assert_not_called()
        self.assertEqual(self.manager.store.list(self.name), [])

    def test_full_queue_rejects_entire_study_without_creating_records(self):
        class FullQueue(dict):
            def __len__(self):
                return 256
        # Simulate admission pressure without fake live futures or leaked workers.
        with patch.object(self.manager, "futures", FullQueue()), patch("lab.engines.analytic.run") as engine, \
                self.assertRaises(ValueError):
            self.start()
        engine.assert_not_called()
        self.assertEqual(self.manager.store.list(self.name), [])
        self.assertFalse(self.manager.active or self.manager.tokens or self.manager.payloads)

    def test_invalid_late_mutation_never_admits_any_variant(self):
        self.project["scenarios"]["test"]["apps"] = [
            {"id": "call", "type": "voip", "src": "h0x0", "dst": "h2x0", "codec": "g711", "start": 0, "stop": 1}]
        param = {"kind": "app", "app": "call", "field": "codec"}
        with patch("lab.engines.analytic.run") as engine, self.assertRaises(ValueError):
            self.manager.start_study(self.project, "test", "analytic", param, ["g711", "BAD_CODEC"])
        engine.assert_not_called()
        self.assertEqual(self.manager.store.list(self.name), [])
        self.assertFalse(self.manager.active or self.manager.tokens or self.manager.payloads)

    def test_two_dimensional_grid_keeps_both_indices_and_does_not_mutate_inputs(self):
        before = copy.deepcopy((self.project, self.param))
        second = {"kind": "app", "app": "f", "field": "rate"}
        plan = self.start(values=(1, 2), param2=second, values2=["1Mbps", "2Mbps"])
        self.assertEqual(plan["runs"], 4)
        self.wait(lambda: not self.manager.active)
        rows = [self.manager.get(self.name, rid) for rid in plan["run_ids"]]
        self.assertEqual([(row["study_index"], row["study_index2"]) for row in rows], [(0, 0), (1, 0), (0, 1), (1, 1)])
        self.assertEqual([row["study_position"] for row in rows], [0, 1, 2, 3])
        self.assertTrue(all(row["status"] == "done" for row in rows))
        self.assertEqual([(row["study_value"], row["study_value2"]) for row in rows],
                         [(1, "1Mbps"), (2, "1Mbps"), (1, "2Mbps"), (2, "2Mbps")])
        self.assertEqual((self.project, self.param), before)

    def test_study_progress_and_completion_events_preserve_existing_shape(self):
        with patch.object(self.manager.bus, "publish", wraps=self.manager.bus.publish) as publish:
            plan = self.start(values=(1, 2))
            def notices():
                return [call.args[0] for call in publish.call_args_list
                        if call.args and call.args[0].get("type") == "study"]
            self.wait(lambda: any(event.get("done") for event in notices()))
            events = notices()
        started = [event for event in events if "run_id" in event]
        finished = [event for event in events if event.get("done")]
        self.assertEqual({event["run_id"] for event in started}, set(plan["run_ids"]))
        self.assertEqual(sorted(event["index"] for event in started), [0, 1])
        self.assertTrue(all(event["total"] == 2 and event["project"] == self.name for event in started))
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]["study_id"], plan["study_id"])
        self.assertEqual(finished[0]["total"], 2)

    def test_admission_storage_failure_rolls_back_every_variant(self):
        write, calls = self.manager.store._write, 0
        def fail(db, data, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected study storage failure")
            return write(db, data, *args, **kwargs)
        with patch.object(self.manager.store, "_write", side_effect=fail), patch("lab.engines.analytic.run") as engine, \
                self.assertRaisesRegex(OSError, "study storage failure"):
            self.start()
        engine.assert_not_called()
        self.assertEqual(self.manager.store.list(self.name), [])
        self.assertFalse(self.manager.active or self.manager.futures or self.manager.tokens or self.manager.payloads)

    def test_scheduler_failure_settles_all_admitted_rows_without_running_engine(self):
        submit, calls = self.manager.executor.submit, 0
        def fail(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected study scheduler failure")
            return submit(*args, **kwargs)
        with patch.object(self.manager.executor, "submit", side_effect=fail), patch("lab.engines.analytic.run") as engine:
            with self.assertRaisesRegex(RuntimeError, "study scheduler failure"):
                self.start()
            self.wait(lambda: not self.manager.active)
            engine.assert_not_called()
        rows = self.manager.store.list(self.name)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["status"] in TERMINAL for row in rows))
        self.assertFalse(self.manager.futures or self.manager.tokens or self.manager.payloads)

    def test_ns3_study_limit_does_not_block_unrelated_job_worker(self):
        gate, study_started, unrelated_started = threading.Event(), threading.Event(), threading.Event()
        self.gates.append(gate)
        lock, study_running, study_peak = threading.Lock(), 0, 0
        def engine(project, scenario, progress, log):
            nonlocal study_running, study_peak
            bandwidth = project["links"][self.param["link"]]["params"]["bw"]
            if bandwidth == 99:
                unrelated_started.set()
                return {"duration": 1, "flows": {}}
            with lock:
                study_running += 1
                study_peak = max(study_peak, study_running)
            study_started.set()
            try:
                while not gate.wait(.01):
                    progress(.1)
                return {"duration": 1, "flows": {}}
            finally:
                with lock:
                    study_running -= 1
        other = copy.deepcopy(self.project)
        other["links"][self.param["link"]]["params"]["bw"] = 99
        with patch.dict("os.environ", {"NETLAB_NS3_PARALLEL": "1"}), \
                patch("lab.engines.manager._ns3_available", return_value=True), \
                patch.object(self.manager, "_run_ns3", side_effect=engine):
            plan = self.start(engine="ns3")
            self.assertTrue(study_started.wait(5))
            rid = self.manager.start(other, "test", "ns3")["run_id"]
            self.assertTrue(unrelated_started.wait(5), "waiting study jobs occupied the unrelated worker")
            self.wait(lambda: self.manager.get(self.name, rid)["status"] == "done")
            self.assertEqual(study_peak, 1)
            gate.set()
            self.wait(lambda: not self.manager.active)
        self.assertTrue(all(self.manager.get(self.name, rid)["status"] == "done" for rid in plan["run_ids"]))
        self.assertEqual(study_peak, 1)

    def test_abrupt_owner_exit_recovers_every_planned_variant(self):
        directory = Path(self.temp.name, "abrupt")
        code = """
import json, os, sys, time
from pathlib import Path
from unittest.mock import patch
from lab.drivers.base import EventBus
from lab.drivers.demo_driver import DemoDriver
from lab.engines import manager
from lab.engines.run_queue import QueuedRunManager
from lab.tests.test_study_queue import project_fixture
manager.RESULTS_DIR = sys.argv[1]
bus = EventBus()
queue = QueuedRunManager(bus, DemoDriver(bus), workers=1)
project, param = project_fixture()
with patch('lab.engines.analytic.run', side_effect=lambda *a, **k: time.sleep(30)):
    plan = queue.start_study(project, 'test', 'analytic', param, [1,2,3])
    print(json.dumps(plan), flush=True)
    os._exit(0)
"""
        child = subprocess.run([sys.executable, "-c", code, str(directory)], capture_output=True, text=True, timeout=10)
        self.assertEqual(child.returncode, 0, child.stderr)
        plan = json.loads(child.stdout)
        store = RunStore(directory / "runs.sqlite3")
        rows = [store.get(self.name, rid) for rid in plan["run_ids"]]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["status"] == "interrupted" for row in rows))
        self.assertEqual([row["study_total"] for row in rows], [3, 3, 3])

    def test_authenticated_two_dimensional_api_and_html_report(self):
        self.manager.close()
        body = {"project": self.project, "scenario": "test", "engine": "analytic", "param": self.param,
                "values": [1, 2], "param2": {"kind": "app", "app": "f", "field": "rate"},
                "values2": ["1Mbps", "2Mbps"]}
        with TestClient(create_app("demo", token="study")) as client:
            self.assertEqual(client.post("/api/studies", json=body).status_code, 401)
            client.cookies.set("netlab_token", "study")
            response = client.post("/api/studies", json=body)
            self.assertEqual(response.status_code, 200, response.text)
            plan = response.json()
            self.assertEqual(plan["runs"], 4)
            self.assertEqual(len(plan["run_ids"]), 4)
            self.wait(lambda: all(client.get(f"/api/runs/{self.name}/{rid}").json()["status"] in TERMINAL
                                  for rid in plan["run_ids"]))
            response = client.get(f"/api/studies/{self.name}/{plan['study_id']}/report.html")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn('class="heatmap"', response.text)
            self.assertIn("Issiqlik xaritalari", response.text)


if __name__ == "__main__":
    unittest.main()
