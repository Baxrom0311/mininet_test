import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import weakref
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient

from lab.campaign_report import campaign_bundle
from lab.drivers.base import EventBus
from lab.drivers.demo_driver import DemoDriver
from lab.engines.run_queue import QueuedRunManager
from lab.engines.manager import RunManager
from lab.engines.run_store import TERMINAL
from lab.server import create_app
from lab.topogen import generate


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = patch("lab.engines.manager.RESULTS_DIR", self.temp.name)
        self.paths.start()
        self.manager = self.make_manager()
        self.project = generate("ring", routers=3, hosts=1, flows=1)
        self.project["scenarios"] = {"test": {"duration": 3, "events": [], "apps": [{"id": "f", "type": "udp",
            "src": "h0x0", "dst": "h2x0", "rate": "3Mbps", "start": 0, "stop": 3}]}}
        link = next(lid for lid, l in self.project["links"].items() if "h0x0" in (l["a"]["dev"], l["b"]["dev"]))
        self.project["links"][link]["params"]["bw"] = .5
        self.body = {"project": self.project, "scenario": "test", "param": {"kind": "link", "link": link, "field": "bw"},
                     "values": [1, 2], "repetitions": 2, "seed": 7, "start_s": 1, "end_s": 3}
        self.name = self.project["name"]

    def make_manager(self):
        bus = EventBus()
        return QueuedRunManager(bus, DemoDriver(bus), workers=2)

    def tearDown(self):
        self.manager.close()
        self.paths.stop()
        self.temp.cleanup()

    def wait(self, check):
        deadline = time.monotonic() + 10
        while not check():
            self.assertLess(time.monotonic(), deadline, "campaign state did not settle")
            time.sleep(.01)

    def complete(self):
        plan = self.manager.campaigns.start(self.body)
        self.wait(lambda: self.manager.campaigns.status(self.name, plan["campaign_id"])["complete"])
        return plan

    def test_preview_and_invalid_late_variants_never_schedule(self):
        before = copy.deepcopy(self.body)
        preview = self.manager.campaigns.preview(self.body)
        self.assertFalse(preview["scheduled"])
        self.assertEqual(preview["run_count"], 6)
        self.assertEqual(preview["conditions"][0]["design_diff"]["changes"], [])
        self.assertTrue(any(d["path"].endswith("/params/bw") and d["before"] == .5 and d["after"] == 1
                            for d in preview["conditions"][1]["design_diff"]["changes"]))
        self.assertNotIn("project_snapshot", preview)
        self.assertEqual(self.manager.campaigns.store.list(self.name)["total"], 0)
        cases = ({"values": []}, {"values": list(range(9))}, {"values": [True]}, {"values": [float("inf")]},
                 {"values": [1, "bad"]}, {"repetitions": True}, {"repetitions": 25, "values": list(range(8))},
                 {"seed": False}, {"engine": "mininet"}, {"start_s": -1},
                 {"param": {"kind": "failure", "field": "seed"}})
        with patch("lab.engines.analytic.run") as engine:
            for changes in cases:
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    self.manager.campaigns.start({**self.body, **changes})
            engine.assert_not_called()
        self.assertFalse(self.manager.active)
        self.assertEqual(self.manager.store.list(self.name), [])
        self.assertEqual(self.manager.campaigns.store.list(self.name)["total"], 0)
        self.assertEqual(self.body, before)

    def test_api_campaign_effects_exports_auth_and_restart_polling(self):
        self.manager.close()
        with TestClient(create_app("demo", token="test")) as client:
            self.assertEqual(client.post("/api/campaigns/preview", json=self.body).status_code, 401)
            client.cookies.set("netlab_token", "test")
            response = client.post("/api/campaigns", json=self.body)
            self.assertEqual(response.status_code, 200, response.text)
            plan = response.json()
            base = f"/api/campaigns/{self.name}/{plan['campaign_id']}"
            deadline = time.monotonic() + 10
            while not client.get(base).json()["complete"]:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.02)
            status = client.get(base).json()
            self.assertEqual(status["status"], "done")
            self.assertEqual(status["progress"], 1)
            self.assertNotIn("project_snapshot", status)
            report = client.get(base + "/report.json").json()
            self.assertTrue(report["analysis_complete"])
            self.assertEqual(len(report["comparisons"]), 2)
            for comparison in report["comparisons"]:
                row = next(r for r in comparison["result"]["metrics"] if r["metric"] == "avg_thr_bps")
                self.assertGreater(row["delta_mean"], 0)
                self.assertTrue(row["ci_excludes_zero"])
            self.assertIn("no multiple-comparison adjustment", client.get(base + "/report.html").text)
            response = client.get(base + "/export.zip")
            self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else "")
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                self.assertEqual(json.loads(archive.read("campaign.json")), report)
                self.assertIn("project_snapshot", json.loads(archive.read("plan.json")))
                self.assertIn("delta_mean", archive.read("effects.csv").decode())
                with zipfile.ZipFile(io.BytesIO(archive.read("experiments/baseline.zip"))) as nested:
                    records = json.loads(nested.read("results.json"))
                    self.assertEqual(len(records), 2)
                    self.assertTrue(all(r["campaign_variant"] == "baseline" for r in records))
            listed = client.get(f"/api/campaigns/{self.name}?limit=1").json()
            self.assertEqual(listed["total"], 1)
            self.assertNotIn("project_snapshot", listed["campaigns"][0])
            self.assertEqual(client.get(f"/api/campaigns/other/{plan['campaign_id']}").status_code, 404)
            self.assertEqual(client.post(f"/api/campaigns/other/{plan['campaign_id']}/cancel").status_code, 404)
            self.assertEqual(client.get(f"/api/campaigns/{self.name}?limit=0").status_code, 400)
            self.assertEqual(client.post(base + "/cancel").json()["cancel_requested"], 0)
            self.assertEqual(client.post(base + "/cancel", headers={"origin": "https://outside.example"}).status_code, 403)
        self.manager = self.make_manager()
        loads = json.loads
        def metadata_only(raw, *args, **kwargs):
            self.assertNotIn('"project_snapshot":', raw, "full campaign plan decoded on status polling")
            return loads(raw, *args, **kwargs)
        with patch("lab.engines.result_index.json.load", side_effect=AssertionError("series read during status polling")), \
                patch("lab.engines.campaign_store.json.loads", side_effect=metadata_only):
            status = self.manager.campaigns.status(self.name, plan["campaign_id"])
            self.assertTrue(status["complete"])

    def test_cancel_all_keeps_unrelated_job_alive(self):
        release, started = threading.Event(), threading.Event()
        def engine(project, sc, progress, log):
            started.set()
            while not release.wait(.01):
                progress(.25)
            return {"duration": 3, "flows": {}}
        try:
            with patch("lab.engines.analytic.run", side_effect=engine):
                plan = self.manager.campaigns.start(self.body)
                other = self.manager.start(self.project, "test", "analytic")["run_id"]
                self.assertTrue(started.wait(3))
                result = self.manager.campaigns.cancel(self.name, plan["campaign_id"])
                self.assertGreater(result["cancel_requested"], 0)
                self.wait(lambda: all(r["status"] in TERMINAL for r in self.manager.campaigns.status(self.name, plan["campaign_id"])["runs"]))
                self.assertEqual(self.manager.campaigns.status(self.name, plan["campaign_id"])["status"], "cancelled")
                self.assertNotEqual(self.manager.store.get(self.name, other)["status"], "cancelled")
                release.set()
                self.wait(lambda: not self.manager.active)
        finally:
            release.set()

    def test_admission_database_failure_rolls_back_plan_and_every_run(self):
        write, calls = self.manager.store._write, 0
        def fail(db, data, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError("injected database failure")
            return write(db, data, *args, **kwargs)
        with patch.object(self.manager.store, "_write", side_effect=fail), patch("lab.engines.analytic.run") as engine:
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.manager.campaigns.start(self.body)
            engine.assert_not_called()
        self.assertFalse(self.manager.active or self.manager.tokens or self.manager.futures)
        self.assertEqual(self.manager.store.list(self.name), [])
        self.assertEqual(self.manager.campaigns.store.list(self.name)["total"], 0)

    def test_executor_failure_cancels_entire_admitted_batch_without_running_engines(self):
        original, calls = self.manager.executor.submit, 0
        def fail(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected scheduler failure")
            return original(*args, **kwargs)
        with patch.object(self.manager.executor, "submit", side_effect=fail), patch("lab.engines.analytic.run") as engine:
            with self.assertRaisesRegex(RuntimeError, "scheduler failure"):
                self.manager.campaigns.start(self.body)
            self.wait(lambda: not self.manager.active)
            engine.assert_not_called()
        self.assertFalse(self.manager.tokens or self.manager.futures)
        cid = self.manager.campaigns.store.list(self.name)["campaigns"][0]["campaign_id"]
        status = self.manager.campaigns.status(self.name, cid)
        self.assertEqual(status["status"], "submission_error")
        self.assertFalse(status["complete"])
        self.assertTrue(all(r["status"] in TERMINAL for r in status["runs"]))

    def test_unexpected_worker_failure_cleans_state_and_preserves_committed_results(self):
        with patch.object(RunManager, "_worker", side_effect=RuntimeError("before result")):
            plan = self.manager.campaigns.start(self.body)
            self.wait(lambda: not self.manager.futures)
        status = self.manager.campaigns.status(self.name, plan["campaign_id"])
        self.assertEqual(status["status"], "failed")
        self.assertTrue(all(r["status"] == "error" for r in status["runs"]))
        self.assertFalse(self.manager.active or self.manager.tokens or self.manager.payloads)
        original = RunManager._worker
        def after_result(instance, *args, **kwargs):
            original(instance, *args, **kwargs)
            raise RuntimeError("after committed result")
        with patch.object(RunManager, "_worker", new=after_result):
            plan = self.manager.campaigns.start(self.body)
            self.wait(lambda: not self.manager.futures)
        status = self.manager.campaigns.status(self.name, plan["campaign_id"])
        self.assertTrue(status["complete"])
        self.assertTrue(self.manager.campaigns.analysis(self.name, plan["campaign_id"])[1]["analysis_complete"])

    def test_persistent_state_write_failure_and_cancel_save_failure_have_no_hidden_jobs(self):
        write = self.manager.store._write
        def only_admission(db, data, *args, **kwargs):
            if data["status"] != "queued":
                raise OSError("state storage unavailable")
            return write(db, data, *args, **kwargs)
        with patch.object(self.manager.store, "_write", side_effect=only_admission), patch("lab.engines.analytic.run") as engine:
            plan = self.manager.campaigns.start(self.body)
            self.wait(lambda: not self.manager.futures)
            engine.assert_not_called()
            status = self.manager.campaigns.status(self.name, plan["campaign_id"])
            self.assertEqual(status["status"], "interrupted")
            self.assertEqual(len(status["orphaned_run_ids"]), 6)
            self.assertFalse(status["complete"])
        foreign = self.manager.campaigns.store.get(self.name, plan["campaign_id"])
        foreign.update(submission="admitted", owner_instance="another-live-instance")
        with self.manager.store.connect() as db:
            self.manager.campaigns.store.put(db, foreign)
        observed = self.manager.campaigns.status(self.name, plan["campaign_id"])
        self.assertEqual(observed["status"], "queued")
        self.assertEqual(observed["orphaned_run_ids"], [])
        release = threading.Event()
        def block(project, sc, progress, log):
            while not release.wait(.01):
                progress(.1)
            return {"duration": 3, "flows": {}}
        try:
            with patch("lab.engines.analytic.run", side_effect=block):
                plan = self.manager.campaigns.start(self.body)
                self.wait(lambda: len(self.manager.payloads) == 4)
                rid = next(iter(self.manager.payloads))
                with patch.object(self.manager, "_save", side_effect=OSError("result storage unavailable")):
                    with self.assertRaises(OSError):
                        self.manager.cancel(self.name, rid)
                self.assertTrue(all(rid not in d for d in (self.manager.active, self.manager.tokens, self.manager.futures, self.manager.payloads)))
                self.assertIn(rid, self.manager.campaigns.status(self.name, plan["campaign_id"])["orphaned_run_ids"])
                release.set()
                self.wait(lambda: not self.manager.futures)
        finally:
            release.set()

    def test_capacity_rejection_and_single_run_storage_failure_do_not_leak(self):
        with patch.dict(self.manager.futures, {f"reserved{i}": object() for i in range(251)}):
            with self.assertRaisesRegex(ValueError, "full"):
                self.manager.campaigns.start(self.body)
            self.assertEqual(len(self.manager.futures), 251)
        self.assertEqual(self.manager.campaigns.store.list(self.name)["total"], 0)
        with patch.object(self.manager.store, "_write", side_effect=RuntimeError("single write failed")):
            with self.assertRaises(RuntimeError):
                self.manager.start(self.project, "test", "analytic")
        self.assertFalse(self.manager.active or self.manager.tokens or self.manager.futures)

    def test_cancelled_queued_projects_release_before_busy_workers_finish(self):
        class WeakProject(dict):
            pass
        release, references = threading.Event(), {}
        prepare = self.manager.campaigns.prepare
        def wrap(body):
            plan, jobs = prepare(body)
            for job in jobs:
                job.project = WeakProject(job.project)
                references[job.run_id] = weakref.ref(job.project)
            return plan, jobs
        def engine(project, sc, progress, log):
            while not release.wait(.01):
                progress(.1)
            return {"duration": 3, "flows": {}}
        try:
            with patch.object(self.manager.campaigns, "prepare", side_effect=wrap), patch("lab.engines.analytic.run", side_effect=engine):
                self.manager.campaigns.start(self.body)
                self.wait(lambda: len(self.manager.store.list(self.name, status="running")) == 2)
                queued = self.manager.store.list(self.name, status="queued")
                self.assertEqual(len(queued), 4)
                for record in queued:
                    self.manager.cancel(self.name, record["run_id"])
                self.assertTrue(all(references[r["run_id"]]() is None for r in queued), "cancelled payload retained by executor queue")
                self.assertEqual(len(self.manager.store.list(self.name, status="running")), 2)
                release.set()
                self.wait(lambda: not self.manager.active)
        finally:
            release.set()

    def test_abrupt_child_exit_recovers_campaign_without_replay(self):
        self.manager.close()
        request = Path(self.temp.name, "request.json")
        identity = Path(self.temp.name, "identity.txt")
        request.write_text(json.dumps(self.body))
        code = """
import json, os, sys, threading
from pathlib import Path
from lab.engines import manager, analytic
from lab.engines.run_queue import QueuedRunManager
from lab.drivers.base import EventBus
from lab.drivers.demo_driver import DemoDriver
manager.RESULTS_DIR = sys.argv[1]
started = threading.Event()
def block(*args):
    started.set()
    threading.Event().wait(60)
analytic.run = block
bus = EventBus()
q = QueuedRunManager(bus, DemoDriver(bus))
plan = q.campaigns.start(json.loads(Path(sys.argv[2]).read_text()))
Path(sys.argv[3]).write_text(plan['campaign_id'])
assert started.wait(5)
os._exit(0)
"""
        subprocess.run([sys.executable, "-c", code, self.temp.name, str(request), str(identity)],
                       cwd=Path(__file__).resolve().parents[2], check=True, timeout=15)
        with patch("lab.engines.analytic.run") as engine:
            self.manager = self.make_manager()
            status = self.manager.campaigns.status(self.name, identity.read_text())
            self.assertEqual(status["status"], "interrupted")
            self.assertFalse(status["complete"])
            self.assertTrue(all(r["status"] == "interrupted" for r in status["runs"]))
            self.assertFalse(self.manager.active or self.manager.futures)
            engine.assert_not_called()

    def test_deleted_cohort_is_visible_and_exportable_without_fake_comparison(self):
        plan = self.complete()
        for rid in plan["conditions"][0]["run_ids"]:
            self.manager.delete(self.name, rid)
        stored, report, cohorts = self.manager.campaigns.analysis(self.name, plan["campaign_id"])
        self.assertEqual(report["campaign"]["status"], "incomplete")
        self.assertFalse(report["analysis_complete"])
        self.assertTrue(all(c["result"] is None and c["error"] for c in report["comparisons"]))
        with zipfile.ZipFile(io.BytesIO(campaign_bundle(stored, report, cohorts))) as archive:
            self.assertIn("experiments/baseline-unavailable.txt", archive.namelist())
            self.assertIn("ValueError", archive.read("comparison_errors.csv").decode())

    def test_runtime_incompatibility_and_invalid_membership_are_reported(self):
        plan = self.complete()
        variant = plan["conditions"][1]
        for rid in variant["run_ids"]:
            record = self.manager.get(self.name, rid)
            record["reproducibility"]["runtime"]["python"] = "different"
            self.manager._save(self.name, record)
        _, report, _ = self.manager.campaigns.analysis(self.name, plan["campaign_id"])
        self.assertFalse(report["analysis_complete"])
        self.assertIsNone(report["comparisons"][0]["result"])
        self.assertIn("runtime", report["comparisons"][0]["error"])
        record = self.manager.get(self.name, variant["run_ids"][0])
        record["campaign_id"] = "other"
        self.manager._save(self.name, record)
        status = self.manager.campaigns.status(self.name, plan["campaign_id"])
        self.assertEqual(status["conditions"][1]["invalid_run_ids"], [record["run_id"]])

    def test_corrupt_condition_path_cannot_enter_archive(self):
        plan = self.complete()
        stored = self.manager.campaigns.store.get(self.name, plan["campaign_id"])
        stored["conditions"][1]["key"] = "../outside"
        with self.manager.store.connect() as db:
            self.manager.campaigns.store.put(db, stored)
        with self.assertRaisesRegex(ValueError, "condition"):
            self.manager.campaigns.analysis(self.name, plan["campaign_id"])


if __name__ == "__main__":
    unittest.main()
