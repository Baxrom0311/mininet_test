import copy
import io
import json
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient

from lab.analysis_windows import analyze
from lab.apps import postprocess
from lab.experiments import summarize
from lab.model import empty_project, validate_scenario
from lab.reproducibility import capture
from lab.server import create_app
from lab.topogen import generate


def fixture():
    points = [{"t": b, "t_start": a, "t_end": b, "thr_bps": value, "delay_ms": value / 10,
               "jitter_ms": 1, "loss_pct": 0} for a, b, value in ((0, 1, 100), (1, 2, 20), (2, 3, 40), (3, 3.5, 80))]
    return {"run_id": "run", "engine": "analytic", "status": "done", "duration": 3.5,
            "scenario": {"duration": 3.5}, "flows": {"f": {"start": 0, "stop": 3.5, "type": "udp",
            "series": points, "summary": {"avg_thr_bps": 999}}}}


def records():
    out = []
    for i in range(3):
        run = fixture()
        project = empty_project("windows")
        sc = {"duration": 3.5, "seed": 7, "rng_run": i + 1, "apps": [], "events": []}
        project["scenarios"]["test"] = sc
        run.update(run_id=f"r{i}", experiment_id="exp", replication_index=i, replication_count=3,
                   seed=7, rng_run=i + 1, scenario=sc, reproducibility=capture(project, "test", sc, sc, "analytic"))
        run["flows"]["f"]["summary"]["avg_thr_bps"] += i
        for point in run["flows"]["f"]["series"]:
            point["thr_bps"] += i
        out.append(run)
    return out


class WindowTests(unittest.TestCase):
    def test_duration_weighting_includes_fractional_tail(self):
        run = fixture()
        before = copy.deepcopy(run)
        flow = analyze(run, 1, 3.5)["flows"]["f"]
        self.assertEqual(flow["expected_bins"], 3)
        self.assertEqual(flow["expected_s"], 2.5)
        self.assertEqual(flow["metrics"]["avg_thr_bps"]["value"], 40)
        self.assertTrue(flow["metrics"]["avg_thr_bps"]["complete"])
        self.assertAlmostEqual(analyze(run)["flows"]["f"]["metrics"]["avg_thr_bps"]["value"], 200 / 3.5)
        self.assertEqual(run, before)

    def test_boundary_bins_trimmed_and_inactive_flows_have_no_sample(self):
        run = fixture()
        flow = analyze(run, 1.2, 3.2)["flows"]["f"]
        self.assertEqual((flow["selected_start_s"], flow["selected_end_s"]), (2, 3))
        self.assertAlmostEqual(flow["trimmed_s"], 1)
        self.assertEqual(flow["metrics"]["avg_thr_bps"]["value"], 40)
        self.assertTrue(flow["metrics"]["avg_thr_bps"]["complete"])
        run["flows"]["f"]["start"] = 3
        flow = analyze(run, 0, 2)["flows"]["f"]
        self.assertEqual(flow["expected_bins"], 0)
        self.assertIsNone(flow["metrics"]["avg_thr_bps"]["value"])
        self.assertFalse(flow["metrics"]["avg_thr_bps"]["complete"])

    def test_missing_bins_and_missing_delay_do_not_get_imputed(self):
        run = fixture()
        run["flows"]["f"]["series"].pop(2)
        flow = analyze(run, 1, 3.5)["flows"]["f"]
        self.assertEqual(flow["expected_s"], 2.5)
        self.assertEqual(flow["metrics"]["avg_thr_bps"]["covered_s"], 1.5)
        self.assertFalse(flow["metrics"]["avg_thr_bps"]["complete"])
        run = fixture()
        run["flows"]["f"]["series"][2].update(thr_bps=0, delay_ms=None, loss_pct=100)
        flow = analyze(run, 1, 3.5)["flows"]["f"]
        self.assertTrue(flow["metrics"]["avg_thr_bps"]["complete"])
        self.assertFalse(flow["metrics"]["avg_delay_ms"]["complete"])
        self.assertEqual(flow["metrics"]["loss_pct"]["value"], 40)

    def test_legacy_grid_gaps_and_bad_intervals_are_diagnosed(self):
        run = fixture()
        for point in run["flows"]["f"]["series"]:
            point.pop("t_start"); point.pop("t_end")
        self.assertTrue(analyze(run, 1, 3.5)["flows"]["f"]["legacy_intervals"])
        run["flows"]["f"]["series"].pop(2)
        self.assertEqual(analyze(run, 1, 3.5)["flows"]["f"]["metrics"]["avg_thr_bps"]["covered_s"], 1.5)
        for error in ("duplicate", "offgrid", "invalid"):
            bad = fixture()
            if error == "duplicate":
                bad["flows"]["f"]["series"].append(bad["flows"]["f"]["series"][1])
            elif error == "offgrid":
                bad["flows"]["f"]["series"][1]["t_start"] = 1.1
            else:
                bad["flows"]["f"]["series"][1]["t_end"] = None
            flow = analyze(bad)["flows"]["f"]
            self.assertIsNotNone(flow["error"])
            self.assertFalse(flow["metrics"]["avg_thr_bps"]["complete"])
        # Old millisecond timestamps cannot disambiguate a tiny terminal bin.
        short = {"run_id": "short", "engine": "ns3", "status": "done", "duration": 1.0001,
                 "scenario": {"sample": 1}, "flows": {"f": {"series": [{"t": 1, "thr_bps": 20}]}}}
        self.assertIsNotNone(analyze(short)["flows"]["f"]["error"])
        short["flows"]["f"]["series"] = [{"t": 1, "t_start": 0, "t_end": 1, "thr_bps": 20},
                                         {"t": 1, "t_start": 1, "t_end": 1.0001, "thr_bps": 20}]
        self.assertTrue(analyze(short)["flows"]["f"]["metrics"]["avg_thr_bps"]["complete"])

    def test_invalid_windows_states_and_nonfinite_values(self):
        for start, end in ((-1, 2), (2, 2), (0, 4), (float("nan"), 2), (0, float("inf")), (True, 2)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                analyze(fixture(), start, end)
        for state in ("running", "error", "cancelled"):
            run = fixture(); run["status"] = state
            with self.assertRaises(ValueError):
                analyze(run)
        run = fixture()
        run["flows"]["f"]["series"][1]["thr_bps"] = float("nan")
        self.assertFalse(analyze(run)["flows"]["f"]["metrics"]["avg_thr_bps"]["complete"])

    def test_experiment_uses_one_sample_per_run_and_preserves_default_summary(self):
        runs = records()
        default = summarize(runs)
        self.assertEqual(next(r for r in default["metrics"] if r["metric"] == "avg_thr_bps")["mean"], 1000)
        self.assertNotIn("window", default)
        result = summarize(runs, start_s=1, end_s=3.5)
        metric = next(r for r in result["metrics"] if r["metric"] == "avg_thr_bps")
        self.assertEqual(metric["n"], 3)
        self.assertEqual(metric["mean"], 41)
        self.assertAlmostEqual(metric["ci95"][0], 38.515862, places=5)
        runs[0]["flows"]["f"]["series"].pop(2)
        metric = next(r for r in summarize(runs, start_s=1)["metrics"] if r["metric"] == "avg_thr_bps")
        self.assertEqual(metric["n"], 3)
        self.assertEqual(metric["incomplete"], 1)
        self.assertIsNone(metric["ci95"])
        runs = records()
        runs[0]["flows"]["f"]["stop"] = 3
        metric = next(r for r in summarize(runs, start_s=1)["metrics"] if r["metric"] == "avg_thr_bps")
        self.assertTrue(metric["window_mismatch"])
        self.assertIsNone(metric["ci95"])

    def test_postprocessed_apps_keep_matching_exact_intervals(self):
        for kind in ("voip", "mcast", "http", "ftp"):
            with self.subTest(kind=kind):
                parts = ("up", "down") if kind == "voip" else ("h1", "h2") if kind == "mcast" else ("1",)
                flows = {f"app.{part}": {**copy.deepcopy(fixture()["flows"]["f"]), "group": "app", "done_t": 2} for part in parts}
                sc = {"apps": [{"id": "app", "type": kind, "src": "h0", "dst": "h1", "start": 0, "stop": 3.5}]}
                run = {**fixture(), "flows": flows}
                result = postprocess(run, sc)
                self.assertTrue(all(p.get("t_start") is not None and p.get("t_end") is not None
                                    for p in result["flows"]["app"]["series"]))
                self.assertIn("app", analyze(result)["flows"])

    def test_sampling_validation(self):
        project = empty_project("windows")
        sc = {"duration": 3.5, "apps": [], "events": []}
        self.assertEqual(validate_scenario(project, {**sc, "sample": .4}), [])
        for sample in (0, -1, True, None, float("nan"), float("inf"), .0001, 3601):
            self.assertTrue(any("sample" in e for e in validate_scenario(project, {**sc, "sample": sample})), sample)
        self.assertTrue(any("100000" in e for e in validate_scenario(project, {**sc, "duration": 3600, "sample": .001})))

    def test_api_window_reports_exports_and_result_immutability(self):
        project = generate("ring", routers=3, hosts=1, flows=1)
        project["scenarios"] = {"test": {"duration": 4.5, "apps": [{"id": "video", "type": "udp", "src": "h0x0",
                                  "dst": "h2x0", "rate": "500kbps", "start": 0, "stop": 4.5}], "events": []}}
        with tempfile.TemporaryDirectory() as td, patch("lab.engines.manager.RESULTS_DIR", td), \
                TestClient(create_app("demo", token="test")) as client:
            client.cookies.set("netlab_token", "test")
            batch = client.post("/api/experiments", json={"project": project, "scenario": "test", "repetitions": 2}).json()
            base = f"/api/experiments/{project['name']}/{batch['experiment_id']}"
            deadline = time.monotonic() + 10
            while not client.get(base).json()["complete"]:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.02)
            run_url = f"/api/runs/{project['name']}/{batch['run_ids'][0]}"
            original = client.get(run_url).json()
            result = client.get(base + "?start_s=1&end_s=4.5")
            self.assertEqual(result.status_code, 200, result.text)
            summary = result.json()
            self.assertEqual(summary["run_windows"][0]["flows"]["video"]["expected_s"], 3.5)
            metric = next(r for r in summary["metrics"] if r["metric"] == "avg_thr_bps")
            self.assertGreater(metric["mean"], 400000)
            self.assertIsNotNone(metric["ci95"])
            self.assertEqual(client.get(run_url + "/window?start_s=1&end_s=4.5").json(), summary["run_windows"][0])
            self.assertIn("Window coverage", client.get(base + "/report.html?start_s=1&end_s=4.5").text)
            response = client.get(base + "/export.zip?start_s=1&end_s=4.5")
            self.assertEqual(response.status_code, 200)
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                self.assertEqual(json.loads(z.read("experiment.json")), summary)
                self.assertIn("covered_s", z.read("window_coverage.csv").decode())
                self.assertIn("ci95_low", z.read("experiment_statistics.csv").decode())
            self.assertEqual(client.get(run_url).json(), original)
            for query in ("start_s=-1", "end_s=7", "start_s=nan", "start_s=inf", "start_s=2&end_s=2"):
                self.assertEqual(client.get(base + "?" + query).status_code, 400)
            self.assertEqual(client.get(base + "?start_s=bad").status_code, 422)
            self.assertEqual(client.get("/api/runs/other/missing/window").status_code, 404)
            client.cookies.clear()
            self.assertEqual(client.get(run_url + "/window").status_code, 401)


if __name__ == "__main__":
    unittest.main()
