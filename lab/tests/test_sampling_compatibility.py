"""Sample-grid compatibility for current results and historical analytic archives."""

import copy
import unittest

from lab.analysis_windows import analyze
from lab.engines import analytic
from lab.experiment_comparison import _conditions
from lab.sampling import sample_interval
from lab.topogen import generate


def result(*, marker=False, step=.25):
    run = {"engine": "analytic", "status": "done", "duration": 2.5,
           "scenario": {"duration": 2.5, "sample": step},
           "flows": {"f": {"start": 0, "stop": 2.5, "series": [
               {"t": b, "t_start": a, "t_end": b, "thr_bps": value}
               for a, b, value in ((0, 1, 100), (1, 2, 0), (2, 2.5, 100))]}}}
    if marker:
        run["sample_s"] = step
    return run


def recorded(run):
    run = copy.deepcopy(run)
    scenario = run["scenario"]
    run["reproducibility"] = {"scenario_name": "test", "project": {"name": "sampling", "scenarios": {"test": scenario}},
                              "runtime": {"lab_python_sha256": "synthetic-source"}}
    return run


class SamplingCompatibilityTests(unittest.TestCase):
    def test_legacy_analytic_ignores_requested_fine_sample(self):
        run = result()
        before = copy.deepcopy(run)
        self.assertEqual(sample_interval(run), 1)
        report = analyze(run)
        self.assertEqual(report["sample_s"], 1)
        flow = report["flows"]["f"]
        self.assertEqual(flow["expected_bins"], 3)
        self.assertTrue(flow["metrics"]["avg_thr_bps"]["complete"])
        self.assertEqual(flow["metrics"]["avg_thr_bps"]["value"], 60)
        self.assertEqual(_conditions(recorded(run))["sample_s"], 1)
        self.assertEqual(run, before)

    def test_legacy_timestamps_remain_unambiguous_one_second_bins(self):
        run = result()
        for point in run["flows"]["f"]["series"]:
            del point["t_start"], point["t_end"]
        report = analyze(run)["flows"]["f"]
        self.assertTrue(report["legacy_intervals"])
        self.assertEqual(report["expected_bins"], 3)
        self.assertTrue(report["metrics"]["avg_thr_bps"]["complete"])

    def test_new_marker_controls_grid_and_fractional_tail(self):
        run = result(marker=True)
        run["duration"] = run["scenario"]["duration"] = 1.1
        flow = run["flows"]["f"]
        flow["stop"] = 1.1
        flow["series"] = [{"t": b, "t_start": a, "t_end": b, "thr_bps": 100 * (i + 1)}
                          for i, (a, b) in enumerate(((0, .25), (.25, .5), (.5, .75), (.75, 1), (1, 1.1)))]
        report = analyze(run)
        self.assertEqual(report["sample_s"], .25)
        measured = report["flows"]["f"]
        self.assertEqual(measured["expected_bins"], 5)
        self.assertAlmostEqual(measured["metrics"]["avg_thr_bps"]["value"], 300 / 1.1)
        self.assertTrue(measured["metrics"]["avg_thr_bps"]["complete"])
        self.assertEqual(_conditions(recorded(run))["sample_s"], .25)

    def test_ns3_scenario_sample_semantics_are_unchanged(self):
        run = result(marker=True, step=.4)
        run.update(engine="ns3", sample_s=999)
        self.assertEqual(sample_interval(run), .4)
        self.assertEqual(_conditions(recorded(run))["sample_s"], .4)

    def test_invalid_markers_and_duration_bounds_cannot_enter_analysis(self):
        for value in (0, -.1, True, None, "0.1", float("nan"), float("inf"), .0001, 3601, 10**500):
            run = result(marker=True)
            run["sample_s"] = value
            with self.subTest(sample_s=value), self.assertRaises(ValueError):
                analyze(run)
        for duration in (None, True, 0, float("inf"), 10**500):
            run = result(marker=True)
            run["duration"] = duration
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                sample_interval(run)
        run = result(marker=True, step=.001)
        run["duration"] = 3600
        with self.assertRaisesRegex(ValueError, "100000"):
            sample_interval(run)

    def test_comparison_rejects_marker_mismatch_with_recorded_sampling(self):
        run = recorded(result(marker=True))
        run["sample_s"] = .5
        with self.assertRaisesRegex(ValueError, "sample_s does not match"):
            _conditions(run)
        for invalid in (True, ".25", None, float("nan")):
            run = recorded(result(marker=True))
            run["scenario"]["sample"] = invalid
            with self.subTest(recorded_sample=invalid), self.assertRaises(ValueError):
                _conditions(run)

    def test_other_comparison_guards_are_preserved(self):
        run = recorded(result(marker=True))
        run["scenario"] = {**run["scenario"], "sample": .5}
        with self.assertRaisesRegex(ValueError, "scenario metadata"):
            _conditions(run)
        run = recorded(result(marker=True))
        run["duration"] = 3
        with self.assertRaisesRegex(ValueError, "duration does not match"):
            _conditions(run)
        run = recorded(result(marker=True))
        run["reproducibility"]["runtime"] = {}
        with self.assertRaisesRegex(ValueError, "runtime/source identity"):
            _conditions(run)

    def test_real_analytic_result_exposes_consumable_fine_grid(self):
        project = generate("ring", routers=3, hosts=1, flows=1)
        scenario = {"duration": 1.1, "sample": .25, "apps": [{"id": "f", "type": "udp", "src": "h0x0",
                    "dst": "h2x0", "rate": "1Mbps", "start": 0, "stop": 1.1}], "events": []}
        run = analytic.run(project, scenario)
        run.update(status="done", scenario=scenario)
        self.assertEqual(sample_interval(run), .25)
        self.assertEqual(len(run["flows"]["f"]["series"]), 5)
        report = analyze(run)
        flow = report["flows"]["f"]
        self.assertEqual(flow["expected_bins"], 5)
        self.assertTrue(flow["metrics"]["avg_thr_bps"]["complete"])
        self.assertEqual(flow["metrics"]["avg_thr_bps"]["value"], 1000000)


if __name__ == "__main__":
    unittest.main()
