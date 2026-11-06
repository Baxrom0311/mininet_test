import copy
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient

from lab.experiment_comparison import compare, design_diff
from lab.experiment_comparison_report import comparison_bundle, comparison_report
from lab.reproducibility import capture, comparison_key
from lab.server import create_app
from lab.topogen import generate


def cohort(name, values, *, seed=7, bandwidth=10):
    runs = []
    for index, value in enumerate(values):
        project = generate("ring", routers=3, hosts=1, flows=1, ipv6=True)
        for link in project["links"].values():
            link["params"]["bw"] = bandwidth
        sc = {"duration": 2, "seed": seed, "rng_run": index + 1, "events": [],
              "apps": [{"id": "f", "type": "udp", "src": "h0x0", "dst": "h2x0", "start": 0, "stop": 2, "rate": "1Mbps"}]}
        project["scenarios"] = {"test": sc}
        runs.append({"run_id": f"{name}{index}", "project": project["name"], "scenario": sc, "duration": 2,
                     "engine": "analytic", "status": "done", "experiment_id": name, "replication_count": len(values),
                     "replication_index": index, "seed": seed, "rng_run": index + 1,
                     "reproducibility": capture(project, "test", sc, sc, "analytic"),
                     "flows": {"f": {"type": "udp", "src": "h0x0", "dst": "h2x0", "start": 0, "stop": 2,
                                      "summary": {"avg_thr_bps": value}, "series": [
                                          {"t": t, "t_start": t - 1, "t_end": t, "thr_bps": value} for t in (1, 2)]}}})
    return runs


def rebuild(run):
    record = run["reproducibility"]
    run["reproducibility"] = capture(record["project"], record["scenario_name"], run["scenario"], run["scenario"], run["engine"])


class PairedComparisonTests(unittest.TestCase):
    def setUp(self):
        self.baseline = cohort("baseline", [10, 20, 30, 40, 50])
        self.candidate = cohort("candidate", [11, 22, 33, 44, 55], bandwidth=20)

    def test_paired_reference_seed_matching_and_input_isolation(self):
        before = copy.deepcopy((self.baseline, self.candidate))
        result = compare(self.baseline, list(reversed(self.candidate)))
        row = result["metrics"][0]
        self.assertEqual(row["pairs"], 5)
        self.assertEqual(row["baseline_mean"], 30)
        self.assertEqual(row["candidate_mean"], 33)
        self.assertEqual(row["delta_mean"], 3)
        self.assertEqual(row["relative_change_pct"], 10)
        self.assertAlmostEqual(row["ci95"][0], 1.03675684, places=7)
        self.assertAlmostEqual(row["ci95"][1], 4.96324316, places=7)
        self.assertTrue(row["ci_excludes_zero"])
        self.assertEqual([s["delta"] for s in result["samples"]], [1, 2, 3, 4, 5])
        self.assertTrue(any(d["path"].endswith("/params/bw") and d["before"] == 10 and d["after"] == 20
                            for d in result["design_diff"]["changes"]))
        self.assertEqual((self.baseline, self.candidate), before)

    def test_reverse_direction_and_zero_baseline(self):
        row = compare(self.candidate, self.baseline)["metrics"][0]
        self.assertEqual(row["delta_mean"], -3)
        self.assertLess(row["ci95"][1], 0)
        self.assertAlmostEqual(row["relative_change_pct"], -100 / 11)
        zero, two = cohort("zero", [0, 0]), cohort("two", [2, 2])
        row = compare(zero, two)["metrics"][0]
        self.assertEqual(row["ci95"], [2, 2])
        self.assertIsNone(row["relative_change_pct"])
        huge = compare(cohort("tiny", [1e-308, 1e-308]), cohort("huge", [1e308, 1e308]))
        self.assertIsNone(huge["metrics"][0]["relative_change_pct"])
        json.dumps(huge, allow_nan=False)
        for run in self.baseline:
            run["flows"]["f"]["summary"]["loss_pct"] = 10
        for run in self.candidate:
            run["flows"]["f"]["summary"]["loss_pct"] = 12
        loss = next(r for r in compare(self.baseline, self.candidate)["metrics"] if r["metric"] == "loss_pct")
        self.assertEqual(loss["delta_mean"], 2)
        self.assertEqual(loss["delta_unit"], "percentage_points")
        self.assertEqual(loss["relative_change_pct"], 20)

    def test_failed_pending_deleted_and_missing_metrics_suppress_intervals(self):
        for status in ("error", "cancelled", "interrupted", "queued", "running"):
            candidate = copy.deepcopy(self.candidate)
            candidate[0]["status"] = status
            result = compare(self.baseline, candidate)
            self.assertFalse(result["complete"])
            self.assertEqual(result["completed_pairs"], 4)
            self.assertIsNone(result["metrics"][0]["ci95"])
        result = compare(self.baseline, self.candidate[1:])
        self.assertEqual(result["unmatched_baseline"], ["baseline0"])
        self.assertEqual(result["metrics"][0]["pairs"], 4)
        self.assertIsNone(result["metrics"][0]["ci95"])
        for value in (None, float("nan"), float("inf")):
            candidate = copy.deepcopy(self.candidate)
            candidate[0]["flows"]["f"]["summary"]["avg_thr_bps"] = value
            row = compare(self.baseline, candidate)["metrics"][0]
            self.assertEqual(row["missing_pairs"], 1)
            self.assertIsNone(row["ci95"])
        candidate = copy.deepcopy(self.candidate)
        candidate[0]["status"] = "queued"
        candidate[0].pop("reproducibility")
        result = compare(self.baseline, candidate)
        with zipfile.ZipFile(io.BytesIO(comparison_bundle(self.baseline, candidate, result))) as archive:
            mapping = list(csv.DictReader(io.StringIO(archive.read("run_map.csv").decode())))
            pending = next(r for r in mapping if r["run_id"] == "candidate0")
            self.assertEqual(pending["replay_inputs_available"], "False")
            self.assertEqual(pending["input_directory"], "")
            self.assertNotIn("runs/06/run-request.json", archive.namelist())

    def test_incompatible_pair_schedules_counts_and_shared_run_ids_rejected(self):
        with self.assertRaisesRegex(ValueError, "distinct"):
            compare(self.baseline, self.baseline)
        with self.assertRaisesRegex(ValueError, "fixed seed"):
            compare(self.baseline, cohort("wrong", [11, 22, 33, 44, 55], seed=8))
        with self.assertRaisesRegex(ValueError, "counts"):
            compare(self.baseline, cohort("short", [1, 2, 3, 4]))
        candidate = copy.deepcopy(self.candidate)
        candidate[0]["run_id"] = self.baseline[0]["run_id"]
        with self.assertRaisesRegex(ValueError, "reuse"):
            compare(self.baseline, candidate)
        candidate = copy.deepcopy(self.candidate)
        for run in candidate:
            run["rng_run"] += 10
            run["scenario"]["rng_run"] += 10
            rebuild(run)
        with self.assertRaisesRegex(ValueError, "schedules"):
            compare(self.baseline, candidate)

    def test_runtime_engine_family_timing_and_endpoint_guards(self):
        for change in ("runtime", "engine", "family", "duration", "endpoint", "flowtype", "active"):
            candidate = copy.deepcopy(self.candidate)
            for run in candidate:
                if change == "runtime":
                    run["reproducibility"]["runtime"]["python"] = "different"
                elif change == "engine":
                    run["engine"] = "ns3"; rebuild(run)
                elif change == "family":
                    run["scenario"]["ip_version"] = 6; rebuild(run)
                elif change == "duration":
                    run["scenario"]["duration"] = 3; run["duration"] = 3; rebuild(run)
                elif change == "endpoint":
                    run["flows"]["f"]["dst"] = "h1x0"
                elif change == "flowtype":
                    run["flows"]["f"]["type"] = "tcp"
                else:
                    run["flows"]["f"]["start"] = 1
            with self.subTest(change=change), self.assertRaises(ValueError):
                compare(self.baseline, candidate)

    def test_window_means_and_incomplete_coverage(self):
        result = compare(self.baseline, self.candidate, start_s=1, end_s=2)
        self.assertEqual(result["window"]["start_s"], 1)
        self.assertEqual(result["metrics"][0]["delta_mean"], 3)
        self.assertIsNotNone(result["metrics"][0]["ci95"])
        candidate = copy.deepcopy(self.candidate)
        candidate[0]["flows"]["f"]["series"].pop()
        row = compare(self.baseline, candidate, start_s=1)["metrics"][0]
        self.assertEqual(row["missing_pairs"], 1)
        self.assertIsNone(row["ci95"])

    def test_sampling_and_failure_pair_mismatches_are_rejected(self):
        baseline, candidate = copy.deepcopy(self.baseline), copy.deepcopy(self.candidate)
        for collection, sample in ((baseline, 1), (candidate, .5)):
            for run in collection:
                run["engine"] = "ns3"
                run["scenario"]["sample"] = sample
                rebuild(run)
        with self.assertRaisesRegex(ValueError, "sampling"):
            compare(baseline, candidate)
        baseline, candidate = copy.deepcopy(self.baseline), copy.deepcopy(self.candidate)
        for collection in (baseline, candidate):
            for run in collection:
                if collection is candidate:
                    run["replication_index"] = (run["replication_index"] + 1) % 5
                run["scenario"]["failures"] = {"seed": 7 + run["replication_index"],
                    "links": {"targets": "all", "mtbf_s": 10, "mttr_s": 1}}
                rebuild(run)
        with self.assertRaisesRegex(ValueError, "failure generator seeds"):
            compare(baseline, candidate)

    def test_offline_cli_matches_library_and_rejects_nonarray_input(self):
        with tempfile.TemporaryDirectory() as td:
            left, right, output = (Path(td, name) for name in ("baseline.json", "candidate.json", "comparison.json"))
            left.write_text(json.dumps(self.baseline))
            right.write_text(json.dumps(self.candidate))
            command = [sys.executable, "-m", "lab.experiment_comparison", str(left), str(right), "-o", str(output)]
            result = subprocess.run(command, cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(output.read_text()), compare(self.baseline, self.candidate))
            right.write_text("{}")
            result = subprocess.run(command, cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 2)
            self.assertIn("results.json array", result.stderr)

    def test_bounded_diff_json_pointers_and_html_escaping(self):
        result = design_diff({"a/b": {"~x": [1, 2]}}, {"a/b": {"~x": [1, 3, 4]}})
        self.assertEqual([d["path"] for d in result["changes"]], ["/a~1b/~0x/1", "/a~1b/~0x/2"])
        result = design_diff({}, {str(i): i for i in range(105)})
        self.assertEqual(len(result["changes"]), 100)
        self.assertTrue(result["truncated"])
        result = design_diff({"code": "a" * 1000}, {"code": "b" * 1000})
        self.assertTrue(result["changes"][0]["after"]["truncated"])
        for run in self.baseline + self.candidate:
            run["flows"]["<script>alert(1)</script>"] = run["flows"].pop("f")
        doc = comparison_report(compare(self.baseline, self.candidate))
        self.assertNotIn("<script>alert(1)</script>", doc)
        self.assertIn("&lt;script&gt;", doc)

    def test_api_analytic_effect_html_raw_export_and_auth(self):
        project = generate("ring", routers=3, hosts=1, flows=1)
        project["scenarios"] = {"test": {"duration": 3, "events": [], "apps": [{"id": "f", "type": "udp",
                                  "src": "h0x0", "dst": "h2x0", "rate": "2Mbps", "start": 0, "stop": 3}]}}
        with tempfile.TemporaryDirectory() as td, patch("lab.engines.manager.RESULTS_DIR", td), \
                TestClient(create_app("demo", token="test")) as client:
            client.cookies.set("netlab_token", "test")
            ids = []
            for bandwidth in (.5, 2):
                for link in project["links"].values():
                    link["params"]["bw"] = bandwidth
                response = client.post("/api/experiments", json={"project": project, "scenario": "test", "repetitions": 3, "seed": 7})
                self.assertEqual(response.status_code, 200, response.text)
                ids.append(response.json()["experiment_id"])
            deadline = time.monotonic() + 10
            while not all(client.get(f"/api/experiments/{project['name']}/{eid}").json()["complete"] for eid in ids):
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.02)
            body = {"project": project["name"], "baseline_id": ids[0], "candidate_id": ids[1], "start_s": 1, "end_s": 3}
            response = client.post("/api/experiments/compare", json=body)
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            row = next(r for r in result["metrics"] if r["metric"] == "avg_thr_bps")
            self.assertGreater(row["delta_mean"], 1000000)
            self.assertEqual(row["pairs"], 3)
            self.assertTrue(row["ci_excludes_zero"])
            html = client.post("/api/experiments/compare/report.html", json=body)
            self.assertEqual(html.status_code, 200)
            self.assertIn("Changed conditions", html.text)
            response = client.post("/api/experiments/compare/export.zip", json=body)
            self.assertEqual(response.status_code, 200)
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                self.assertEqual(json.loads(archive.read("comparison.json")), result)
                self.assertEqual(len(json.loads(archive.read("results.json"))), 6)
                self.assertIn("delta_stddev", archive.read("paired_statistics.csv").decode())
                self.assertIn("rng_run", archive.read("paired_samples.csv").decode())
                self.assertIn("candidate", archive.read("run_map.csv").decode())
                self.assertIn("runs/01/run-request.json", archive.namelist())
            for options, status in (({"candidate_id": "missing"}, 404), ({"project": "other"}, 404),
                                    ({"candidate_id": []}, 400), ({"project": []}, 400), ({"start_s": -1}, 400),
                                    ({"candidate_id": ids[0]}, 400)):
                self.assertEqual(client.post("/api/experiments/compare", json={**body, **options}).status_code, status)
            self.assertEqual(client.post("/api/experiments/compare", json=body, headers={"origin": "https://outside.example"}).status_code, 403)
            client.cookies.clear()
            self.assertEqual(client.post("/api/experiments/compare", json=body).status_code, 401)


class ArchiveCompatibilityTests(unittest.TestCase):
    def test_precomparison_archive_fingerprint_is_unchanged(self):
        sc = {"duration": 2, "seed": 7, "rng_run": 1,
              "failures": {"seed": 9, "links": {"targets": "all", "mtbf_s": 10, "mttr_s": 1}}}
        project = {"name": "legacy", "devices": {}, "links": {}, "scenarios": {"test": sc}}
        # Recorded with comparison_key from commit 470ca17, before extracting comparison_inputs.
        self.assertEqual(comparison_key(project, sc, "analytic"),
                         "a406675b38d1af7ec11e3bd21e1576d2ceb917d6ef9c3933c6ae587e795d37cb")


if __name__ == "__main__":
    unittest.main()
