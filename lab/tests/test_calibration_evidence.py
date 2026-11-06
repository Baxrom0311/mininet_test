"""Synthetic evidence-quality fixtures; no real measurements or network changes."""

import copy
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from lab.calibration import compare, markdown


def sample(index=1, **values):
    return {"protocol": "ospf", "metric": "route_update_s", "unit": "s", "model": 1.7,
            "real": 1.7, "source": f"synthetic-fixture/run=example rep={index}", "verified": "true",
            "abs_tolerance": .2, "rel_tolerance": 0, **values}


class EvidenceTests(unittest.TestCase):
    def test_small_matching_sample_cannot_pass_requested_ten_trials(self):
        report = compare([sample(1), sample(2)], min_samples=10)
        self.assertEqual(report["status"], "insufficient")
        self.assertTrue(report["summary"][0]["within_bounds"])
        self.assertFalse(report["summary"][0]["sufficient_samples"])
        self.assertEqual(report["suggested_scenario_overrides"], {})
        self.assertIn("2/10", markdown(report))

    def test_ten_distinct_sources_and_failed_fit_are_separate_from_count(self):
        rows = [sample(i) for i in range(10)]
        before = copy.deepcopy(rows)
        report = compare(rows, min_samples=10)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["suggested_scenario_overrides"], {"ospf_convergence_s": 1.7})
        self.assertEqual(rows, before)
        report = compare([sample(i, model=0) for i in range(10)], min_samples=10)
        self.assertEqual(report["status"], "failed")
        self.assertTrue(report["summary"][0]["sufficient_samples"])
        self.assertEqual(report["suggested_scenario_overrides"], {"ospf_convergence_s": 1.7})
        self.assertFalse(report["defaults_changed"])

    def test_duplicate_rows_do_not_inflate_count_or_bias_median(self):
        rows = [sample(1, model=10, real=0, abs_tolerance=30)] * 8
        rows += [sample(2, model=10, real=10, abs_tolerance=30), sample(3, model=10, real=20, abs_tolerance=30)]
        report = compare(rows, min_samples=10)
        group = report["summary"][0]
        self.assertEqual((group["input_samples"], group["samples"], group["duplicate_samples"]), (10, 3, 7))
        self.assertEqual(group["real_median"], 10)
        self.assertEqual(report["duplicate_rows"], 7)
        self.assertEqual(len(report["observations"]), 10)
        self.assertEqual(report["observations"][7]["duplicate_of"], 1)
        self.assertEqual(report["status"], "insufficient")

    def test_conflicting_source_observation_is_rejected(self):
        for field in ("real", "model", "abs_tolerance", "rel_tolerance"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "conflicts with row 1"):
                compare([sample(), sample(**{field: 10})])

    def test_source_whitespace_is_normalized_and_blank_source_rejected(self):
        row = sample()
        report = compare([row, {**row, "source": "  " + row["source"] + "  "}])
        self.assertEqual(report["summary"][0]["samples"], 1)
        for source in ("", "   ", None):
            with self.assertRaisesRegex(ValueError, "source are required"):
                compare([sample(source=source)])

    def test_source_identity_is_scoped_to_protocol_metric_and_unit(self):
        report = compare([sample(), sample(protocol="rip"), sample(metric="traffic_gap_s"), sample(unit="ms")])
        self.assertEqual(report["duplicate_rows"], 0)
        self.assertEqual(len(report["summary"]), 4)
        self.assertTrue(all(group["samples"] == 1 for group in report["summary"]))

    def test_missing_required_metric_is_visible_even_if_other_metric_passes(self):
        report = compare([sample(i) for i in range(10)], min_samples=10,
                         required_metrics=[("RIP", "route_recovery_s", "s")])
        missing = next(g for g in report["summary"] if g["protocol"] == "rip")
        self.assertEqual(report["status"], "insufficient")
        self.assertEqual(missing["samples"], 0)
        self.assertIsNone(missing["real_median"])
        self.assertIsNone(missing["within_bounds"])
        self.assertFalse(missing["override_eligible"])
        self.assertIn("0/10", markdown(report))
        json.dumps(report, allow_nan=False)

    def test_unverified_and_empty_input_never_count_as_verified_samples(self):
        rows = [sample(i, verified="false") for i in range(10)]
        self.assertEqual(compare(rows, min_samples=10)["status"], "unverified")
        report = compare(rows, min_samples=10, required_metrics=[("ospf", "route_update_s", "s")])
        self.assertEqual(report["status"], "insufficient")
        self.assertEqual(report["summary"][0]["samples"], 0)
        self.assertIn("No verified", markdown(report))
        self.assertEqual(compare([])["status"], "unverified")

    def test_mixed_model_predictions_are_not_pooled_into_one_override(self):
        report = compare([sample(i, model=1.7 if i % 2 else 1.8) for i in range(10)], min_samples=10)
        self.assertEqual(report["status"], "passed")  # Each observation fits its declared tolerance.
        self.assertTrue(report["summary"][0]["mixed_model_values"])
        self.assertFalse(report["summary"][0]["override_eligible"])
        self.assertEqual(report["suggested_scenario_overrides"], {})
        self.assertIn("Mixed model values", markdown(report))

    def test_invalid_evidence_requirements(self):
        for count in (0, -1, True, 1.0, "10", None, float("nan"), 10001):
            with self.subTest(count=count), self.assertRaises(ValueError):
                compare([], min_samples=count)
        for key in ("ospf:route_update_s:s", ("ospf", "s"), ("ospf", "", "s"), (1, "metric", "s")):
            with self.assertRaises(ValueError):
                compare([], required_metrics=[key])

    def test_saved_real_data_preserve_historical_fit_and_medians(self):
        root = Path(__file__).parents[2]
        path = root / "docs/validation/real-calibration-2026-11-06.csv"
        before = path.read_bytes()
        with path.open(newline="") as f:
            records = list(csv.DictReader(f))
        baseline = json.loads(path.with_suffix(".json").read_text())
        report = compare(records)
        keys = ("protocol", "metric", "unit", "samples", "model_median", "real_median", "max_absolute_error", "within_bounds")
        self.assertEqual([{key: group[key] for key in keys} for group in report["summary"]], baseline["summary"])
        self.assertEqual(report["status"], baseline["status"])
        strict = compare(records, min_samples=10)
        self.assertEqual(strict["status"], "insufficient")
        self.assertEqual(strict["suggested_scenario_overrides"], {})
        self.assertEqual(path.read_bytes(), before)
        # Old exported reports still render without new additive fields.
        self.assertIn("OSPF".lower(), markdown(baseline))

    def test_cli_json_markdown_and_exit_codes(self):
        with tempfile.TemporaryDirectory(prefix="netlab-evidence-") as td:
            path = Path(td, "input.csv")
            def invoke(rows, *args):
                with path.open("w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=list(sample()))
                    writer.writeheader()
                    writer.writerows(rows)
                return subprocess.run([sys.executable, "-m", "lab.calibration", str(path), *args],
                                      capture_output=True, text=True, timeout=10)
            cases = [(0, [sample(i) for i in range(10)]), (1, [sample(i, model=0) for i in range(10)]),
                     (2, [sample(verified="false")]), (3, [sample()])]
            for code, rows in cases:
                r = invoke(rows, "--min-samples", "10")
                self.assertEqual(r.returncode, code, r.stderr)
                self.assertFalse(json.loads(r.stdout)["defaults_changed"])
            r = invoke([sample(i) for i in range(10)], "--min-samples", "10",
                       "--require-metric", "rip:route_recovery_s:s", "--format", "markdown")
            self.assertEqual(r.returncode, 3, r.stderr)
            self.assertIn("0/10", r.stdout)
            for args in (("--min-samples", "0"), ("--require-metric", "bad-format")):
                r = invoke([sample()], *args)
                self.assertEqual(r.returncode, 2)
                self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
