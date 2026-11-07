"""Durable study status/report tests; no privileged or packet engine is used."""

import copy
from concurrent.futures import Future
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from lab.engines.study_status import cancel, describe, status
from lab.report import study_report
from lab.server import create_app
from lab.topogen import generate


def record(position, state="done", total=3):
    return {"run_id": f"study-v{position + 1:02d}", "project": "project", "study_id": "study",
            "study_position": position, "study_total": total, "study_index": position, "study_value": position + 1,
            "study_param": {"kind": "link", "link": "wire", "field": "bw"},
            "engine": "analytic", "scenario_name": "test", "status": state,
            "flows": {"f": {"type": "udp", "summary": {"avg_thr_bps": 1000000}}} if state == "done" else {}}


class StudyStateTests(unittest.TestCase):
    def queue(self, records):
        queue = SimpleNamespace(queue_lock=threading.RLock(), _lock=threading.RLock(), active={}, MAX_STUDY_RUNS=49,
                                result_index=SimpleNamespace(reconcile=Mock()), store=SimpleNamespace(list=Mock(return_value=records)))
        return queue

    def test_complete_known_plan_has_canonical_states_and_no_read_mutation(self):
        records = [record(i) for i in range(3)]
        before = copy.deepcopy(records)
        queue = self.queue(records)
        result = status(queue, "project", "study")
        self.assertEqual(result["status"], "done")
        self.assertTrue(result["complete"])
        self.assertTrue(result["settled"])
        self.assertEqual(result["planned_runs"], 3)
        self.assertEqual(result["progress"], 1)
        self.assertEqual(result["missing_run_ids"], [])
        self.assertEqual(records, before)
        queue.result_index.reconcile.assert_called_once_with("project")

    def test_missing_and_invalid_planned_variants_cannot_be_complete(self):
        result = describe([record(0), record(2)])
        self.assertEqual(result["missing_run_ids"], ["study-v02"])
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["complete"])
        self.assertAlmostEqual(result["progress"], 2 / 3)
        records = [record(i) for i in range(3)]
        records[1]["study_position"] = 0
        result = describe(records)
        self.assertEqual(result["invalid_run_ids"], ["study-v02"])
        self.assertFalse(result["settled"])

    def test_inconsistent_or_duplicate_plan_metadata_is_not_success(self):
        for records in ([record(0), record(1, total=4)], [record(0, total=1), record(0, total=1)]):
            with self.subTest(records=records):
                result = describe(records)
                self.assertFalse(result["complete"])
                self.assertEqual(result["status"], "incomplete")

    def test_legacy_archives_have_unknown_planned_count(self):
        records = [record(i) for i in range(2)]
        for row in records:
            del row["study_position"], row["study_total"]
        result = describe(records)
        self.assertEqual(result["planning"], "legacy")
        self.assertIsNone(result["planned_runs"])
        self.assertIsNone(result["progress"])
        self.assertFalse(result["complete"])
        self.assertTrue(result["observed_settled"])

    def test_pending_progress_keeps_store_phase_and_uses_local_progress(self):
        records = [record(0, "running"), record(1, "queued"), record(2, "done")]
        queue = self.queue(records)
        queue.active["study-v01"] = {"project": "project", "progress": .5}
        result = status(queue, "project", "study")
        self.assertEqual(result["statuses"], {"running": 1, "queued": 1, "done": 1})
        self.assertAlmostEqual(result["progress"], .5)
        self.assertFalse(result["settled"])

    def test_terminal_failures_remain_distinct_and_never_complete(self):
        for terminal, expected in (("error", "failed"), ("cancelled", "cancelled"), ("interrupted", "interrupted")):
            with self.subTest(terminal=terminal):
                result = describe([record(0, terminal), record(1), record(2)])
                self.assertEqual(result["status"], expected)
                self.assertTrue(result["settled"])
                self.assertFalse(result["complete"])

    def test_cancel_only_nonterminal_rows_and_preserve_rejections(self):
        records = [record(0), record(1, "queued"), record(2, "running")]
        queue = self.queue(records)
        def cancel_one(project, rid):
            if rid == "study-v03":
                raise ValueError("run has no cancellable local worker")
            records[1]["status"] = "cancelled"
        queue.cancel = Mock(side_effect=cancel_one)
        result = cancel(queue, "project", "study")
        self.assertEqual(result["cancel_requested_run_ids"], ["study-v02"])
        self.assertEqual(result["cancel_requested"], 1)
        self.assertEqual(result["not_cancelled"], [{"run_id": "study-v03", "error": "run has no cancellable local worker"}])
        self.assertEqual(queue.cancel.call_count, 2)
        self.assertEqual(result["statuses"], {"done": 1, "cancelled": 1, "running": 1})

    def test_unknown_or_invalid_study_cannot_be_read(self):
        with self.assertRaises(FileNotFoundError):
            status(self.queue([]), "project", "study")
        with self.assertRaises(ValueError):
            status(self.queue([]), "project", "../study")

    def test_cancel_continues_after_storage_or_other_variant_failure(self):
        records = [record(0, "queued"), record(1, "queued"), record(2)]
        queue = self.queue(records)
        def cancel_one(project, rid):
            if rid == "study-v01":
                raise OSError("storage unavailable for first variant")
            records[1]["status"] = "cancelled"
        queue.cancel = Mock(side_effect=cancel_one)
        result = cancel(queue, "project", "study")
        self.assertEqual(result["cancel_requested_run_ids"], ["study-v02"])
        self.assertEqual(result["not_cancelled"][0]["run_id"], "study-v01")
        self.assertEqual(queue.cancel.call_count, 2)

    def test_only_provably_local_orphans_are_reported_without_mutating_rows(self):
        records = [record(0, "queued"), record(1, "queued"), record(2)]
        queue = self.queue(records)
        queue.futures = {"study-v02": Future()}
        before = copy.deepcopy(records)
        self.assertEqual(status(queue, "project", "study")["orphaned_run_ids"], [])
        queue._study_notices = {"study": {"project": "project", "run_ids": [row["run_id"] for row in records]}}
        result = status(queue, "project", "study")
        self.assertEqual(result["orphaned_run_ids"], ["study-v01"])
        self.assertEqual(result["status"], "incomplete")
        queue.futures = {}
        result = status(queue, "project", "study")
        self.assertEqual(result["status"], "interrupted")
        self.assertFalse(result["settled"])
        self.assertEqual(records, before)


class StudyHtmlTests(unittest.TestCase):
    def test_report_preserves_unsuccessful_rows_and_escapes_errors(self):
        rows = [record(0), record(1, "error"), record(2, "queued")]
        rows[1]["error"] = '<script>alert("error")</script>'
        body = study_report(rows)
        self.assertIn("Variantlar holati", body)
        self.assertIn("study-v02", body)
        self.assertIn("error", body)
        self.assertIn("queued", body)
        self.assertIn("&lt;script&gt;", body)
        self.assertNotIn('<script>alert("error")</script>', body)
        self.assertIn("Rejalashtirilgan", body)

    def test_heatmap_axes_include_failed_outer_variants_and_only_done_metrics(self):
        rows = [record(i, "done" if i == 0 else "error", total=4) for i in range(4)]
        for i, row in enumerate(rows):
            row.update(study_index=i % 2, study_index2=i // 2, study_value=(i % 2) + 1,
                       study_value2=(i // 2) + 10, study_param2={"kind": "app", "app": "f", "field": "size"})
        rows[3]["flows"] = {"f": {"summary": {"avg_thr_bps": 999000000}}}
        with patch("lab.report.svg_heatmap", return_value="<figure>heatmap</figure>") as heatmap:
            body = study_report(rows)
        call = next(call for call in heatmap.call_args_list if "O'tkazuvchanlik" in call.args[3])
        self.assertEqual(call.args[0], ["1", "2"])
        self.assertEqual(len(call.args[1]), 2)
        self.assertEqual(call.args[2], [[1, None], [None, None]])
        self.assertIn("Issiqlik xaritalari", body)

    def test_queued_only_and_legacy_reports_are_visible_without_success_claim(self):
        rows = [record(i, "queued") for i in range(3)]
        body = study_report(rows)
        self.assertIn("queued", body)
        self.assertIn("Tugagan muvaffaqiyatli natija", body)
        for row in rows:
            del row["study_position"], row["study_total"]
        self.assertIn("noma&#x27;lum", study_report(rows))

    def test_corrupt_or_duplicate_coordinates_do_not_generate_misleading_charts(self):
        for value in ("1", {}, [], -1, True):
            rows = [record(i) for i in range(3)]
            rows[1]["study_index"] = value
            with self.subTest(value=value), patch("lab.report.svg_lines") as charts:
                body = study_report(rows)
            charts.assert_not_called()
            self.assertIn("grafiklar hisoblanmadi", body)
        rows = [record(i) for i in range(3)]
        rows[1]["study_index"] = 0
        with patch("lab.report.svg_lines") as charts:
            study_report(rows)
        charts.assert_not_called()


class StudyStatusApiTests(unittest.TestCase):
    def test_authenticated_status_report_and_group_cancel(self):
        project = generate("ring", routers=3, hosts=1, flows=0)
        project["scenarios"] = {"test": {"duration": 1, "apps": [], "events": []}}
        entered, release = threading.Event(), threading.Event()
        def engine(p, sc, progress, log):
            entered.set()
            while not release.wait(.005):
                progress(.1)
            return {"flows": {}, "duration": 1}
        with tempfile.TemporaryDirectory() as directory, patch("lab.engines.manager.RESULTS_DIR", directory), \
                patch("lab.engines.analytic.run", side_effect=engine):
            try:
                with TestClient(create_app("demo", token="study")) as client:
                    self.assertEqual(client.get("/api/studies/project/missing").status_code, 401)
                    self.assertEqual(client.post("/api/studies/project/missing/cancel").status_code, 401)
                    client.cookies.set("netlab_token", "study")
                    response = client.post("/api/studies", json={"project": project, "scenario": "test", "engine": "analytic",
                        "param": {"kind": "link", "link": next(iter(project["links"])), "field": "bw"}, "values": [1, 2]})
                    self.assertEqual(response.status_code, 200, response.text)
                    plan = response.json()
                    self.assertEqual(len(plan["run_ids"]), 2)
                    self.assertTrue(entered.wait(5))
                    base = f"/api/studies/{project['name']}/{plan['study_id']}"
                    response = client.get(base)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["planned_runs"], 2)
                    self.assertEqual(response.json()["observed_runs"], 2)
                    self.assertEqual(client.get(base + "/report.html").status_code, 200)
                    response = client.post(base + "/cancel")
                    self.assertEqual(response.status_code, 200, response.text)
                    deadline = time.monotonic() + 5
                    while not client.get(base).json()["settled"]:
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(.01)
                    result = client.get(base).json()
                    self.assertEqual(result["status"], "cancelled")
                    self.assertFalse(result["complete"])
                    self.assertEqual(client.get(base + "/report.html").status_code, 200)
                    self.assertEqual(client.get("/api/studies/project/missing").status_code, 404)
            finally:
                release.set()


if __name__ == "__main__":
    unittest.main()
