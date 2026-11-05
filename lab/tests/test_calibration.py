import unittest

from lab.calibration import compare, markdown
from lab.stp import convergence_time


class CalibrationTests(unittest.TestCase):
    def sample(self, **kw):
        return {"protocol": "stp", "metric": "port_forwarding_s", "unit": "s", "model": 30,
                "real": 25, "abs_tolerance": 1, "rel_tolerance": 0, "source": "synthetic unit fixture",
                "verified": "true", **kw}

    def test_discrepancy_and_supported_override(self):
        report = compare([self.sample()])
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["suggested_scenario_overrides"], {"stp_convergence_s": 25})
        project = {"devices": {"s1": {"config": {"stp": True}}}, "links": {}}
        self.assertEqual(convergence_time(project, "s1", [], report["suggested_scenario_overrides"]), 25)
        self.assertEqual(convergence_time(project, "s1", []), 30)
        self.assertFalse(report["defaults_changed"])

    def test_aggregate_traffic_gap_is_not_a_vrrp_timer(self):
        report = compare([self.sample(protocol="vrrp", metric="traffic_gap_s", model=4, real=8)])
        self.assertEqual(report["suggested_scenario_overrides"], {})

    def test_unverified_data_cannot_retune_model(self):
        report = compare([self.sample(verified="false")])
        self.assertEqual(report["status"], "unverified")
        self.assertEqual(report["suggested_scenario_overrides"], {})
        self.assertIn("No verified", markdown(report))

    def test_tolerance_and_finite_output(self):
        report = compare([self.sample(model=26, real=25, abs_tolerance=0, rel_tolerance=.04)])
        self.assertEqual(report["status"], "passed")
        for value in (float("nan"), float("inf"), -1):
            with self.assertRaises(ValueError):
                compare([self.sample(real=value)])


if __name__ == "__main__":
    unittest.main()
