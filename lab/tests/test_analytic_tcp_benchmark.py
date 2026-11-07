"""Benchmark acceptance checks use public CLI behavior and deliberately incomplete results."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lab.benchmarks import analytic_tcp


class AnalyticTcpBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name, "nested/report.json")

    def test_small_synthetic_cli_reports_both_cases_with_expected_totals(self):
        with contextlib.redirect_stdout(io.StringIO()):
            status = analytic_tcp.main(["--flows", "4", "--output", str(self.output)])
        self.assertEqual(status, 0)
        report = json.loads(self.output.read_text())
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["evidence_kind"], "synthetic_fluid_model")
        self.assertFalse(report["real_network_measurement"])
        files, rates = report["cases"]
        self.assertEqual(files["requested_bytes"], 10000)
        self.assertAlmostEqual(files["delivered_bytes"], 10000, delta=.5)
        self.assertEqual(files["completed_flows"], 4)
        self.assertEqual(rates["expected_bps"], 10000000)
        self.assertEqual(rates["measured_bps"], 10000000)

    def test_missing_flow_results_cannot_report_success(self):
        with patch.object(analytic_tcp.analytic, "run", return_value={"flows": {}}), \
                contextlib.redirect_stdout(io.StringIO()):
            status = analytic_tcp.main(["--flows", "3", "--output", str(self.output)])
        self.assertEqual(status, 1)
        report = json.loads(self.output.read_text())
        self.assertEqual(report["status"], "failed")
        self.assertTrue(all(case["status"] == "failed" and "error" in case for case in report["cases"]))

    def test_invalid_counts_never_run_an_engine_or_write_a_report(self):
        for value in ("0", "-1", "2001", "1.5"):
            with self.subTest(value=value), patch.object(analytic_tcp.analytic, "run") as engine, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                analytic_tcp.main(["--flows", value, "--output", str(self.output)])
            self.assertEqual(error.exception.code, 2)
            engine.assert_not_called()
            self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
