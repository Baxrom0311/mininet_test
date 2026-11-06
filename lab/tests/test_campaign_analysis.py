import copy
import json
import tempfile
import time
import unittest
from unittest.mock import patch
import weakref

from fastapi.testclient import TestClient

from lab import analysis_windows, campaigns
from lab import experiment_comparison as comparison
from lab.benchmarks.campaign_analysis import benchmark, independent_reference
from lab.campaign_report import compact_report
from lab.drivers.base import EventBus
from lab.drivers.demo_driver import DemoDriver
from lab.engines.run_queue import QueuedRunManager
from lab.server import create_app
from lab.topogen import generate


class CampaignAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = patch("lab.engines.manager.RESULTS_DIR", self.temp.name)
        self.paths.start()
        bus = EventBus()
        self.manager = QueuedRunManager(bus, DemoDriver(bus))
        project = generate("ring", routers=3, hosts=1, flows=1)
        project["scenarios"] = {"test": {"duration": 3.5, "events": [], "apps": [{"id": "f", "type": "udp",
            "src": "h0x0", "dst": "h2x0", "rate": "3Mbps", "start": 0, "stop": 3.5}]}}
        link = next(k for k, l in project["links"].items() if "h0x0" in (l["a"]["dev"], l["b"]["dev"]))
        project["links"][link]["params"]["bw"] = .5
        self.name = project["name"]
        self.plan = self.manager.campaigns.start({"project": project, "scenario": "test", "param": {"kind": "link", "link": link, "field": "bw"},
            "values": [1, 2, 3], "repetitions": 2, "seed": 7, "start_s": 1, "end_s": 3.5})
        deadline = time.monotonic() + 10
        while self.manager.futures:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)
        self.cid = self.plan["campaign_id"]

    def tearDown(self):
        self.manager.close()
        self.paths.stop()
        self.temp.cleanup()

    def analyze(self):
        return self.manager.campaigns.analysis(self.name, self.cid)[1]

    def test_each_cohort_prepared_once_with_identical_reference_values(self):
        with patch.object(comparison, "summarize", wraps=comparison.summarize) as summaries, \
                patch.object(analysis_windows, "analyze", wraps=analysis_windows.analyze) as windows, \
                patch.object(comparison, "_conditions", wraps=comparison._conditions) as guards:
            report = self.analyze()
        self.assertTrue(report["analysis_complete"])
        self.assertEqual(summaries.call_count, 4)
        self.assertEqual(windows.call_count, 8)
        self.assertEqual(guards.call_count, 8)
        reference = independent_reference(self.manager.campaigns, self.name, self.cid)
        self.assertEqual({k: report[k] for k in reference}, reference)
        measured = benchmark(flows=2, duration=4, variants=2, repetitions=2, samples=1)
        self.assertTrue(measured["identical_report_values"])
        self.assertEqual(measured["independent_reference"]["calls"][0]["cohort_summaries"], 7)
        self.assertEqual(measured["request_local"]["calls"][0]["cohort_summaries"], 3)

    def test_guard_failure_reused_inside_request_but_revalidated_after_repair(self):
        baseline = self.plan["conditions"][0]
        for rid in baseline["run_ids"]:
            run = self.manager.get(self.name, rid)
            run["scenario"]["duration"] = 4
            self.manager._save(self.name, run)
        with patch.object(comparison, "_conditions", wraps=comparison._conditions) as guards:
            report = self.analyze()
        self.assertEqual(guards.call_count, 1)
        self.assertFalse(report["analysis_complete"])
        self.assertTrue(all("scenario metadata" in c["error"] and c["result"] is None for c in report["comparisons"]))
        for rid in baseline["run_ids"]:
            run = self.manager.get(self.name, rid)
            record = run["reproducibility"]
            run["scenario"] = copy.deepcopy(record["project"]["scenarios"][record["scenario_name"]])
            self.manager._save(self.name, run)
        with patch.object(comparison, "_conditions", wraps=comparison._conditions) as guards:
            self.assertTrue(self.analyze()["analysis_complete"])
        self.assertEqual(guards.call_count, 8)

    def test_later_requests_see_changed_metrics_and_deleted_runs(self):
        def throughput(report):
            return next(r for r in report["comparisons"][0]["result"]["metrics"] if r["metric"] == "avg_thr_bps")
        before = self.analyze()
        rid = self.plan["conditions"][1]["run_ids"][0]
        run = self.manager.get(self.name, rid)
        for point in run["flows"]["f"]["series"]:
            point["thr_bps"] += 100000
        self.manager._save(self.name, run)
        after = self.analyze()
        self.assertAlmostEqual(throughput(after)["delta_mean"] - throughput(before)["delta_mean"], 50000)
        self.manager.delete(self.name, self.plan["conditions"][0]["run_ids"][0])
        missing = self.analyze()
        self.assertFalse(missing["analysis_complete"])
        self.assertEqual(throughput(missing)["pairs"], 1)
        self.assertIsNone(throughput(missing)["ci95"])

    def test_compact_json_references_resolve_without_mutating_full_report(self):
        report = self.analyze()
        before = copy.deepcopy(report)
        compact = compact_report(report)
        self.assertEqual(report, before)
        self.assertLess(len(json.dumps(compact)), len(json.dumps(report)))
        self.assertEqual(compact["representation"], "cohort_references")
        for source, reduced in zip(report["comparisons"], compact["comparisons"]):
            self.assertEqual(source["result"]["metrics"], reduced["result"]["metrics"])
            for side in ("baseline", "candidate"):
                pointer = reduced["result"][side]["summary_ref"]
                resolved = compact
                for part in pointer.lstrip("/").split("/"):
                    resolved = resolved[int(part)] if isinstance(resolved, list) else resolved[part]
                self.assertEqual(resolved, source["result"][side])
                self.assertNotIn("run_windows", reduced["result"][side])
        report["comparisons"][0].update(result=None, error="unavailable")
        self.assertIsNone(compact_report(report)["comparisons"][0]["result"])

    def test_compact_api_is_opt_in_and_preserves_scope_and_auth(self):
        self.manager.close()
        with TestClient(create_app("demo", token="test")) as client:
            base = f"/api/campaigns/{self.name}/{self.cid}/report.json"
            self.assertEqual(client.get(base + "?compact=true").status_code, 401)
            client.cookies.set("netlab_token", "test")
            full = client.get(base).json()
            compact = client.get(base + "?compact=true").json()
            self.assertEqual(compact, compact_report(full))
            self.assertNotIn("representation", client.get(base + "?compact=false").json())
            self.assertEqual(client.get(base + "?compact=invalid").status_code, 422)
            self.assertEqual(client.get(f"/api/campaigns/other/{self.cid}/report.json?compact=true").status_code, 404)

    def test_prepared_contexts_are_released_even_after_guard_errors(self):
        cls, references = comparison._ComparisonInput, []
        def track(*args, **kwargs):
            obj = cls(*args, **kwargs)
            references.append(weakref.ref(obj))
            return obj
        with patch.object(campaigns, "_ComparisonInput", side_effect=track):
            result = self.analyze()
        self.assertTrue(result["analysis_complete"])
        self.assertTrue(all(ref() is None for ref in references))
        references.clear()
        rid = self.plan["conditions"][0]["run_ids"][0]
        run = self.manager.get(self.name, rid)
        run["scenario"]["duration"] = 4
        self.manager._save(self.name, run)
        with patch.object(campaigns, "_ComparisonInput", side_effect=track):
            result = self.analyze()
        self.assertFalse(result["analysis_complete"])
        self.assertTrue(all(ref() is None for ref in references), "request context retained an error traceback")


if __name__ == "__main__":
    unittest.main()
