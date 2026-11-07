"""Failure schedules reject malformed inputs before interval generation/union."""

import copy
import unittest
from unittest.mock import patch

from lab import failures


def project():
    return {
        "devices": {"r1": {"type": "router"}, "h1": {"type": "host"}, "h2": {"type": "host"}},
        "links": {
            "l1": {"a": {"dev": "r1"}, "b": {"dev": "h1"}},
            "l2": {"a": {"dev": "r1"}, "b": {"dev": "h2"}},
        },
    }


def scenario():
    return {"duration": 40, "events": [], "failures": {
        "seed": -2, "distribution": "fixed",
        "links": {"mtbf_s": 10, "mttr_s": 2, "targets": ["l1"]},
    }}


class FailureValidationTests(unittest.TestCase):
    def test_disabled_failures_are_unchanged(self):
        for value in (None, {}):
            sc = {"failures": value}
            self.assertEqual(failures.validate(project(), sc), [])
            self.assertIs(failures.expand(project(), sc), sc)

    def test_malformed_failure_objects_report_errors(self):
        for value in (False, True, 0, 1, "x", [], ["x"]):
            with self.subTest(value=value):
                sc = scenario()
                sc["failures"] = value
                self.assertTrue(failures.validate(project(), sc))

    def test_malformed_groups_report_errors_without_expanding(self):
        for group in failures.GROUPS:
            for value in (False, True, 0, 1, "x", [], ["x"], {}):
                with self.subTest(group=group, value=value):
                    sc = scenario()
                    sc["failures"] = {group: value}
                    with patch.object(failures, "expand", side_effect=AssertionError("must not expand")):
                        self.assertTrue(failures.validate(project(), sc))

    def test_timer_fields_require_finite_positive_numbers(self):
        invalid = (None, False, True, 0, -1, "0", "-1", "x", [], {},
                   float("nan"), float("inf"), float("-inf"), "NaN", "Infinity", 10 ** 1000)
        for field in ("mtbf_s", "mttr_s"):
            for value in invalid:
                with self.subTest(field=field, value=value):
                    sc = scenario()
                    sc["failures"]["links"][field] = value
                    with patch.object(failures, "expand", side_effect=AssertionError("must not expand")):
                        errors = failures.validate(project(), sc)
                    self.assertTrue(any(field in error for error in errors), errors)

    def test_seed_rejects_boolean_fraction_and_nonfinite_values(self):
        for value in (True, False, 1.5, "1.5", float("nan"), float("inf"),
                      float("-inf"), "NaN", "Infinity", None, [], {}):
            with self.subTest(value=value):
                sc = scenario()
                sc["failures"]["seed"] = value
                self.assertTrue(any("seed" in e for e in failures.validate(project(), sc)))

    def test_target_lists_require_nonempty_known_names(self):
        for value in (None, False, 0, "l1", {}, [None], [False], [1], [[]], [{}], [""], ["missing"]):
            with self.subTest(value=value):
                sc = scenario()
                sc["failures"]["links"]["targets"] = value
                self.assertTrue(failures.validate(project(), sc))
        sc = scenario()
        sc["failures"]["links"]["targets"] = []
        self.assertEqual(failures.validate(project(), sc), [])

    def test_duration_is_finite_before_generation(self):
        for value in (None, True, False, -1, [], {}, "x", float("nan"), float("inf"),
                      "Infinity", 10 ** 1000):
            with self.subTest(value=value):
                sc = scenario()
                sc["duration"] = value
                with patch.object(failures, "expand", side_effect=AssertionError("must not expand")):
                    self.assertTrue(any("duration" in e for e in failures.validate(project(), sc)))

    def test_event_shapes_and_times_are_checked_before_expansion(self):
        for value in (False, "x", {}, [None], [False], [3], [[]], [{"t": []}],
                      [{"t": True}], [{"t": float("nan")}], [{"t": float("inf")}],
                      [{"t": 10 ** 1000}]):
            with self.subTest(value=value):
                sc = scenario()
                sc["events"] = value
                with patch.object(failures, "expand", side_effect=AssertionError("must not expand")):
                    self.assertTrue(any("events" in e for e in failures.validate(project(), sc)))

    def test_null_events_are_empty_with_active_failures(self):
        sc = scenario()
        expected = failures.expand(project(), sc)["events"]
        sc["events"] = None
        self.assertEqual(failures.validate(project(), sc), [])
        self.assertEqual(failures.expand(project(), sc)["events"], expected)
        self.assertIsNone(sc["events"])

    def test_numeric_strings_and_signed_integer_seeds_preserve_schedules(self):
        expected = failures.expand(project(), scenario())
        for seed in (-2, -2.0, "-2"):
            sc = scenario()
            sc["duration"] = "40"
            sc["failures"]["seed"] = seed
            sc["failures"]["links"].update(mtbf_s="10", mttr_s="2")
            self.assertEqual(failures.validate(project(), sc), [])
            self.assertEqual(failures.expand(project(), sc)["events"], expected["events"])

    def test_fixed_and_seeded_schedules_keep_existing_results(self):
        self.assertEqual(failures.down_intervals(-2, "links", "l1", 10, 2, 40, "fixed"),
                         [(10.0, 12.0), (22.0, 24.0), (34.0, 36.0)])
        expected = [(4.05, 13.148), (24.289, 25.789), (30.773, 33.742)]
        self.assertEqual(failures.down_intervals(-2, "links", "l1", 10, 2, 40), expected)
        self.assertEqual(failures.down_intervals(-2, "links", "l1", 10, 2, 40), expected)
        self.assertNotEqual(failures.down_intervals(-1, "links", "l1", 10, 2, 40), expected)

    def test_expand_preserves_input_and_event_order(self):
        sc = scenario()
        sc["events"] = [{"t": "10", "action": "link_up", "link": "l1"}]
        before = copy.deepcopy(sc)
        expanded = failures.expand(project(), sc)
        self.assertEqual(sc, before)
        self.assertEqual(expanded["events"][0], sc["events"][0])
        self.assertEqual(expanded["events"][1]["action"], "link_down")

    def test_raw_budget_raises_instead_of_truncating(self):
        with patch.object(failures, "MAX_RAW_INTERVALS", 3):
            self.assertEqual(len(failures.down_intervals(1, "links", "l1", 1, 1, 6, "fixed")), 3)
            with self.assertRaisesRegex(ValueError, "raw interval budget 3"):
                failures.down_intervals(1, "links", "l1", 1, 1, 10, "fixed")
            with self.assertRaisesRegex(ValueError, "raw interval budget 3"):
                failures.down_intervals(1, "links", "l1", 5e-324, 5e-324, 1)

    def test_raw_budget_is_global_across_targets_before_union(self):
        sc = scenario()
        sc["duration"] = 4
        sc["failures"]["links"].update(mtbf_s=1, mttr_s=1, targets=["l1", "l2"])
        with patch.object(failures, "MAX_RAW_INTERVALS", 3), \
                patch.object(failures, "_union", side_effect=AssertionError("must not union")):
            with self.assertRaisesRegex(ValueError, "raw interval budget 3.*across targets"):
                failures.link_down_map(project(), sc)
            self.assertTrue(any("raw interval budget 3" in e for e in failures.validate(project(), sc)))

    def test_device_fanout_is_bounded_before_union(self):
        sc = {"duration": 4, "failures": {"distribution": "fixed", "devices": {
            "mtbf_s": 1, "mttr_s": 1, "targets": ["r1"]}}}
        with patch.object(failures, "MAX_RAW_INTERVALS", 3), \
                patch.object(failures, "_union", side_effect=AssertionError("must not union")):
            with self.assertRaisesRegex(ValueError, "raw interval budget 3.*fan-out"):
                failures.link_down_map(project(), sc)

    def test_final_event_budget_is_still_enforced(self):
        sc = scenario()
        sc["duration"] = 4.5
        sc["failures"]["links"].update(mtbf_s=1, mttr_s=1)
        with patch.object(failures, "MAX_EVENTS", 3):
            self.assertTrue(any("4 hodisa > 3" in e for e in failures.validate(project(), sc)))


if __name__ == "__main__":
    unittest.main()
