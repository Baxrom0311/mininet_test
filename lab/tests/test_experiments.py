import copy
import io
import json
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient

from lab.experiments import interval, summarize, variants
from lab.reproducibility import capture, verify
from lab.server import create_app
from lab.topogen import generate


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.project = generate("ring", routers=3, hosts=1, flows=1)
        self.scenario = next(iter(self.project["scenarios"]))
        self.project["scenarios"][self.scenario]["duration"] = 2

    def records(self):
        rows = []
        for i, p in enumerate(variants(self.project, self.scenario, 3, 7)):
            sc = p["scenarios"][self.scenario]
            rows.append({"run_id": f"r{i}", "status": "done", "engine": "analytic", "experiment_id": "exp",
                         "replication_index": i, "replication_count": 3, "seed": 7, "rng_run": i + 1,
                         "reproducibility": capture(p, self.scenario, sc, sc, "analytic"),
                         "flows": {"f": {"summary": {"avg_thr_bps": i + 1}}}})
        return rows

    def test_student_t_reference_and_one_sample(self):
        result = interval([1, 2, 3, 4, 5])
        self.assertEqual(result["mean"], 3)
        self.assertAlmostEqual(result["sample_stddev"], 1.58113883)
        # Reference: t(df=4, .975) * s / sqrt(5) = 1.96324316.
        self.assertAlmostEqual(result["ci95"][0], 1.03675684, places=7)
        self.assertAlmostEqual(result["ci95"][1], 4.96324316, places=7)
        self.assertIsNone(interval([2])["ci95"])
        self.assertEqual(interval([3, 3])["ci95"], [3, 3])
        for values in ([True, 1], [float("nan")], [float("inf")], [-1e308, 1e308], list(range(26))):
            with self.assertRaises(ValueError):
                interval(values)

    def test_seed_validation_and_input_isolation(self):
        before = copy.deepcopy(self.project)
        self.project["scenarios"][self.scenario]["failures"] = {"seed": 999}
        projects = variants(self.project, self.scenario, 3, 7)
        self.assertEqual([p["scenarios"][self.scenario]["seed"] for p in projects], [7, 7, 7])
        self.assertEqual([p["scenarios"][self.scenario]["rng_run"] for p in projects], [1, 2, 3])
        self.assertEqual([p["scenarios"][self.scenario]["failures"]["seed"] for p in projects], [7, 8, 9])
        self.project["scenarios"][self.scenario].pop("failures")
        self.assertEqual(before, self.project)
        for n, seed in ((True, 1), (1, 1), (26, 1), (2, 0), (2, 4294967295), (2, 1.5)):
            with self.assertRaises(ValueError):
                variants(self.project, self.scenario, n, seed)

    def test_no_intervals_for_failed_partial_or_missing_metric(self):
        rows = self.records()
        self.assertTrue(summarize(rows)["complete"])
        self.assertEqual(summarize(rows)["metrics"][0]["n"], 3)
        self.assertIsNotNone(summarize(rows)["metrics"][0]["ci95"])
        for status in ("error", "cancelled", "queued", "interrupted"):
            bad = copy.deepcopy(rows)
            bad[1]["status"] = status
            result = summarize(bad)
            self.assertFalse(result["complete"])
            self.assertIsNone(result["metrics"][0]["ci95"])
        self.assertIsNone(summarize(rows[:2])["metrics"][0]["ci95"])
        rows[1]["flows"]["f"]["summary"]["avg_thr_bps"] = float("inf")
        result = summarize(rows)["metrics"][0]
        self.assertEqual(result["missing"], 1)
        self.assertIsNone(result["ci95"])

    def test_incompatible_or_tampered_inputs_are_rejected(self):
        rows = self.records()
        for change in ("seed", "project", "runtime", "hash", "index"):
            bad = copy.deepcopy(rows)
            record = bad[1]["reproducibility"]
            if change == "seed":
                bad[1]["rng_run"] = bad[0]["rng_run"]
            elif change == "index":
                bad[1]["replication_index"] = bad[0]["replication_index"]
            elif change == "runtime":
                record["runtime"]["python"] = "different"
            elif change == "hash":
                record["project"]["name"] = "tampered"
            else:
                p = copy.deepcopy(record["project"])
                p["scenarios"][self.scenario]["duration"] = 3
                sc = p["scenarios"][self.scenario]
                bad[1]["reproducibility"] = capture(p, self.scenario, sc, sc, "analytic")
            with self.subTest(change=change), self.assertRaises(ValueError):
                summarize(bad)

    def test_api_batch_stats_export_and_replay(self):
        before = copy.deepcopy(self.project)
        with tempfile.TemporaryDirectory() as td, patch("lab.engines.manager.RESULTS_DIR", td), \
                TestClient(create_app("demo")) as client:
            for options in ({"repetitions": 1}, {"engine": "mininet"}, {"seed": False}, {"project": [1]},
                            {"scenario": []}, {"engine": []}):
                resp = client.post("/api/experiments", json={"project": self.project, "scenario": self.scenario, **options})
                self.assertEqual(resp.status_code, 400, resp.text)
                self.assertEqual(client.get(f"/api/runs/{self.project['name']}").json()["runs"], [])
            response = client.post("/api/experiments", json={"project": self.project, "scenario": self.scenario,
                                                           "repetitions": 3, "seed": 50})
            self.assertEqual(response.status_code, 200, response.text)
            batch = response.json()
            for i, rid in enumerate(batch["run_ids"]):
                stored = client.app.state.runs.store.get(self.project["name"], rid)
                self.assertEqual(stored["experiment_id"], batch["experiment_id"])
                self.assertEqual(stored["rng_run"], i + 1)
            base = f"/api/experiments/{self.project['name']}/{batch['experiment_id']}"
            deadline = time.monotonic() + 10
            while True:
                resp = client.get(base)
                self.assertEqual(resp.status_code, 200, resp.text)
                summary = resp.json()
                if summary["complete"]:
                    break
                if time.monotonic() > deadline:
                    self.fail(f"experiment timed out: {summary}")
                time.sleep(.02)
            self.assertEqual(summary["statuses"], {"done": 3})
            self.assertTrue(summary["metrics"])
            self.assertTrue(all(row["ci95"] is not None for row in summary["metrics"]))
            report = client.get(base + "/report.html")
            self.assertEqual(report.status_code, 200)
            self.assertIn("Student t", report.text)
            archive = client.get(base + "/export.zip")
            self.assertEqual(archive.status_code, 200, archive.text if archive.status_code != 200 else "")
            with zipfile.ZipFile(io.BytesIO(archive.content)) as z:
                self.assertEqual(json.loads(z.read("experiment.json")), summary)
                request = json.loads(z.read("runs/01/run-request.json"))
                manifest = json.loads(z.read("runs/01/manifest.json"))
                project = json.loads(z.read("runs/01/project.json"))
                effective = json.loads(z.read("runs/01/effective-input.json"))
                self.assertTrue(verify({**manifest, "project": project, "effective_scenario": effective["scenario"]}))
            replay_id = client.post("/api/runs", json=request).json()["run_id"]
            while True:
                replay = client.get(f"/api/runs/{project['name']}/{replay_id}").json()
                if replay["status"] == "done":
                    break
                self.assertNotEqual(replay["status"], "error", replay)
                if time.monotonic() > deadline:
                    self.fail("replay timed out")
                time.sleep(.02)
            first = client.get(f"/api/runs/{project['name']}/{batch['run_ids'][0]}").json()
            self.assertEqual(first["flows"], replay["flows"])
            self.assertEqual(first["reproducibility"]["effective_scenario_sha256"],
                             replay["reproducibility"]["effective_scenario_sha256"])
            # Unknown experiments and cross-project access cannot expose another project's inputs.
            self.assertEqual(client.get(f"/api/experiments/other/{batch['experiment_id']}").status_code, 404)
            self.assertEqual(self.project, before)

    def test_stochastic_failure_replications_change_events(self):
        sc = self.project["scenarios"][self.scenario]
        sc["duration"] = 12
        for app in sc["apps"]:
            app["stop"] = 12
        link = next(lid for lid, l in self.project["links"].items()
                    if "h0x0" in (l["a"]["dev"], l["b"]["dev"]))
        sc["failures"] = {"seed": 99, "links": {"targets": [link], "mtbf_s": 2, "mttr_s": 2}}
        with tempfile.TemporaryDirectory() as td, patch("lab.engines.manager.RESULTS_DIR", td), \
                TestClient(create_app("demo")) as client:
            response = client.post("/api/experiments", json={"project": self.project, "scenario": self.scenario,
                                                           "repetitions": 3, "seed": 7})
            self.assertEqual(response.status_code, 200, response.text)
            batch = response.json()
            deadline = time.monotonic() + 10
            runs = []
            for rid in batch["run_ids"]:
                while time.monotonic() < deadline:
                    run = client.get(f"/api/runs/{self.project['name']}/{rid}").json()
                    if run["status"] == "done":
                        runs.append(run)
                        break
                    self.assertNotEqual(run["status"], "error", run)
                    time.sleep(.02)
                else:
                    self.fail("failure replication timed out")
            self.assertEqual([r["scenario"]["failures"]["seed"] for r in runs], [7, 8, 9])
            event_sets = [json.dumps(r["reproducibility"]["effective_scenario"]["events"], sort_keys=True) for r in runs]
            self.assertEqual(len(set(event_sets)), 3)
            summary = summarize(runs)
            self.assertTrue(summary["complete"])
            self.assertTrue(any(row["sample_stddev"] > 0 for row in summary["metrics"]))


if __name__ == "__main__":
    unittest.main()
