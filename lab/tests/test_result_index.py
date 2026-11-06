from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from lab.drivers.base import EventBus
from lab.drivers.demo_driver import DemoDriver
from lab.engines.run_queue import QueuedRunManager
from lab.engines.run_store import RunStore
from lab.model import empty_project
from lab.server import create_app


class ResultIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = patch("lab.engines.manager.RESULTS_DIR", self.temp.name)
        self.paths.start()
        self.manager = self.make_manager()

    def make_manager(self):
        bus = EventBus()
        return QueuedRunManager(bus, DemoDriver(bus), workers=2)

    def tearDown(self):
        self.manager.close()
        self.paths.stop()
        self.temp.cleanup()

    def write(self, rid, project="history", **fields):
        data = {"run_id": rid, "project": project, "engine": "analytic", "engine_label": "Analytic",
                "created": 100, "duration": 2, "status": "done", "scenario_name": "test", "label": "old",
                "flows": {"f": {"series": [{"t": i, "thr_bps": i * 1000} for i in range(100)]}}, **fields}
        path = Path(self.temp.name, project, rid + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return path

    def test_large_results_are_not_reread_after_indexing_or_restart(self):
        points = [{"t": i, "thr_bps": 1234567, "delay_ms": 1.5, "loss_pct": 0} for i in range(3000)]
        for i in range(24):
            self.write(f"r{i:03d}", created=i, flows={"f": {"series": points}},
                       study_id="study", study_param={"kind": "link", "field": "bw"}, study_value=i)
        self.assertEqual(len(self.manager.list("history")), 24)
        self.manager.close()
        self.manager = self.make_manager()
        with patch("lab.engines.result_index.json.load", side_effect=AssertionError("full result reread")):
            rows = self.manager.list("history")
            page = self.manager.page("history", 5, 3, study_id="study")
        self.assertEqual(page["total"], 24)
        self.assertEqual([r["run_id"] for r in page["runs"]], [f"r{i:03d}" for i in range(20, 15, -1)])
        self.assertEqual(rows[0]["engine_label"], "Analytic")
        self.assertEqual(rows[0]["study_param"]["field"], "bw")
        self.assertNotIn("flows", rows[0])

    def test_external_edits_deletes_and_corruption_are_reconciled(self):
        changed, deleted = self.write("changed"), self.write("deleted")
        self.assertEqual(len(self.manager.list("history")), 2)
        data = json.loads(changed.read_text())
        data["label"] = "updated outside API"
        changed.write_text(json.dumps(data))
        deleted.unlink()
        rows = self.manager.list("history")
        self.assertEqual([(r["run_id"], r["label"]) for r in rows], [("changed", "updated outside API")])
        changed.write_text("{broken")
        self.assertEqual(self.manager.list("history"), [])
        changed.write_text(json.dumps(data))
        self.assertEqual(len(self.manager.list("history")), 1)
        self.write("bad-id").write_text(json.dumps({"run_id": "different", "status": "done"}))
        self.write("bad-type").write_text(json.dumps({"run_id": "bad-type", "engine": []}))
        self.assertEqual(self.manager.reindex("history")["invalid"], 2)

    def test_force_reindex_handles_preserved_size_and_mtime(self):
        path = self.write("same")
        self.manager.list("history")
        stat = path.stat()
        path.write_text(path.read_text().replace('"label": "old"', '"label": "new"'))
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(path.stat().st_size, stat.st_size)
        self.assertEqual(self.manager.list("history")[0]["label"], "old")
        self.assertEqual(self.manager.reindex("history")["updated"], 1)
        self.assertEqual(self.manager.list("history")[0]["label"], "new")

    def test_same_legacy_id_in_two_projects_stays_isolated(self):
        self.write("same", project="one", label="first")
        self.write("same", project="two", label="second")
        self.assertEqual(self.manager.list("one")[0]["label"], "first")
        self.assertEqual(self.manager.list("two")[0]["label"], "second")
        self.manager.update_label("one", "same", "renamed")
        self.assertEqual(self.manager.list("one")[0]["label"], "renamed")
        self.manager.delete("one", "same")
        self.assertEqual(self.manager.list("one"), [])
        self.assertEqual(self.manager.list("two")[0]["label"], "second")

    def test_api_pagination_filters_stable_order_and_validation(self):
        for i in range(12):
            self.write(f"r{i:02d}", engine="analytic" if i % 2 else "ns3",
                       status="done" if i % 3 else "error", study_id="study" if i % 2 else None)
        self.manager.close()
        with TestClient(create_app("demo", token="test-token")) as client:
            self.assertEqual(client.get("/api/runs/history?limit=3").status_code, 401)
            self.assertEqual(client.post("/api/runs/history/reindex").status_code, 401)
            client.cookies.set("netlab_token", "test-token")
            legacy = client.get("/api/runs/history").json()
            self.assertEqual(set(legacy), {"runs"})
            page = client.get("/api/runs/history?limit=3&offset=2").json()
            self.assertEqual(page["total"], 12)
            self.assertEqual([r["run_id"] for r in page["runs"]], ["r09", "r08", "r07"])
            filtered = client.get("/api/runs/history?engine=analytic&study_id=study&status=done").json()
            self.assertEqual(filtered["total"], 4)
            self.assertEqual(filtered["limit"], 100)
            self.assertEqual(client.get("/api/runs/history?limit=3&offset=99").json()["runs"], [])
            self.assertEqual(client.get("/api/runs/other?limit=3").json()["total"], 0)
            for query in ("limit=0", "limit=201", "offset=-1", "status=oops", "engine=oops", "study_id=a%27OR1=1"):
                response = client.get("/api/runs/history?" + query)
                self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(client.post("/api/runs/history/reindex").json()["updated"], 12)

    def test_queue_filters_use_actual_phase_and_keep_progress(self):
        project = empty_project("history")
        project["scenarios"]["test"] = {"duration": 1, "apps": [], "events": []}
        release = threading.Event()
        def run(p, sc, progress, log):
            while not release.wait(.01):
                progress(.25)
            return {"flows": {}}
        try:
            with patch("lab.engines.analytic.run", side_effect=run):
                ids = [self.manager.start(project, "test", "analytic")["run_id"] for _ in range(3)]
                deadline = time.monotonic() + 5
                while len(self.manager.store.list("history", status="running")) != 2:
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.01)
                page = self.manager.page("history", status="queued")
                self.assertEqual(page["total"], 1)
                self.assertEqual(page["runs"][0]["status"], "running")
                self.assertEqual(page["runs"][0]["queue_state"], "queued")
                self.manager.update_label("history", ids[0], "renamed while running")
                self.assertFalse(Path(self.temp.name, "history", ids[0] + ".json").exists())
                self.assertEqual(self.manager.get("history", ids[0])["label"], "renamed while running")
                self.manager.cancel("history", ids[-1])
                self.assertEqual(self.manager.page("history", status="cancelled")["total"], 1)
                release.set()
                while self.manager.active:
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.01)
                self.assertEqual(self.manager.get("history", ids[0])["label"], "renamed while running")
        finally:
            release.set()

    def test_experiment_report_only_opens_selected_results(self):
        project = empty_project("history")
        project["scenarios"]["test"] = {"duration": 1, "apps": [], "events": []}
        batch = self.manager.start_experiment(project, "test", "analytic", 3, 7)
        deadline = time.monotonic() + 5
        while self.manager.active:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)
        for i in range(20):
            self.write(f"other{i}")
        self.manager.list("history")
        self.manager.close()
        with TestClient(create_app("demo")) as client:
            with patch("lab.engines.result_index.json.load", wraps=json.load) as load:
                response = client.get(f"/api/experiments/history/{batch['experiment_id']}")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["complete"])
            self.assertEqual(load.call_count, 3)

    def test_concurrent_saves_and_lists_do_not_drop_results(self):
        def save(i):
            result = {"run_id": f"r{i}", "project": "history", "status": "done", "engine": "analytic", "created": i}
            self.manager._save("history", result)
            self.manager.list("history")
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(save, range(24)))
        self.assertEqual(self.manager.page("history")["total"], 24)
        self.assertTrue(all(r["status"] == "done" for r in self.manager.list("history")))


class RegistryMigrationTests(unittest.TestCase):
    def legacy(self, path, invalid=False):
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE runs (id TEXT PRIMARY KEY,project TEXT NOT NULL,status TEXT NOT NULL,"
                       "owner INTEGER NOT NULL,updated REAL NOT NULL,metadata TEXT NOT NULL)")
            for i, status in enumerate(("done", "running")):
                data = {"run_id": f"old{i}", "project": "history", "status": status, "created": i,
                        "label": "kept", "study_id": "study", "study_param": {"field": "bw"}}
                db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)", (f"old{i}", "history", status, os.getpid(), 17,
                           "{broken" if invalid and i else json.dumps(data)))

    def test_legacy_registry_migrates_without_losing_metadata_or_live_owner(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td, "runs.sqlite3")
            self.legacy(path)
            store = RunStore(path)
            self.assertEqual(store.get("history", "old1")["status"], "running")
            self.assertEqual(store.get("history", "old0")["study_param"], {"field": "bw"})
            self.assertEqual(store.page("history", study_id="study")["total"], 2)
            store.save({"run_id": "old0", "project": "other", "status": "done"})
            store.delete("other", "old0")
            self.assertEqual(store.get("history", "old0")["label"], "kept")
            self.assertEqual(len(RunStore(path).list("history")), 2)
            with sqlite3.connect(str(path) + ".pre-index.bak") as backup:
                self.assertEqual(len(backup.execute("PRAGMA table_info(runs)").fetchall()), 6)
                self.assertEqual(backup.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 2)

    def test_bad_legacy_row_rolls_back_schema_and_data_copy(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td, "runs.sqlite3")
            self.legacy(path, invalid=True)
            with self.assertRaises(ValueError):
                RunStore(path)
            with sqlite3.connect(path) as db:
                self.assertEqual(len(db.execute("PRAGMA table_info(runs)").fetchall()), 6)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 2)
                self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='runs_migrating'").fetchone())


if __name__ == "__main__":
    unittest.main()
