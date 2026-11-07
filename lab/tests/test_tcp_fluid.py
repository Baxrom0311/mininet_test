"""Resource and byte conservation for the analytical fluid TCP solver."""

import copy
import math
import unittest

from lab.engines.tcp_fluid import advance, allocate


def flow(name, resources=(("wire", 1.),), speed=1., limit=math.inf):
    return {"app": {"id": name}, "res": list(resources), "spd": speed, "cap": limit}


class AllocationTests(unittest.TestCase):
    def test_shared_capacity_with_weighted_fairness_and_cap(self):
        flows = [flow("a", speed=2), flow("b"), flow("c", limit=10)]
        allocate(flows, {"wire": 100})
        self.assertEqual([f["rate"] for f in flows], [60, 30, 10])

    def test_multiple_bottlenecks_and_resource_weight_units(self):
        flows = [flow("a", (("left", 1),)), flow("b", (("left", 1), ("right", 1))),
                 flow("c", (("right", 1),))]
        allocate(flows, {"left": 10, "right": 20})
        self.assertEqual([f["rate"] for f in flows], [5, 5, 15])
        wifi = [flow("fast", (("air", .01),)), flow("slow", (("air", .02),), speed=.5)]
        allocate(wifi, {"air": 1})
        self.assertEqual([f["rate"] for f in wifi], [50, 25])

    def test_repeated_resource_keys_and_order_invariance(self):
        flows = [flow("a", (("wire", .25), ("wire", .75))), flow("b")]
        other = list(reversed(copy.deepcopy(flows)))
        capacity = {"wire": 100}
        allocate(flows, capacity)
        allocate(other, capacity)
        self.assertEqual([f["rate"] for f in flows], [50, 50])
        self.assertEqual({f["app"]["id"]: f["rate"] for f in flows},
                         {f["app"]["id"]: f["rate"] for f in other})
        self.assertEqual(capacity, {"wire": 100})

    def test_zero_capacity_zero_caps_and_no_resource_flows(self):
        flows = [flow("a"), flow("zero", limit=0), flow("local", resources=())]
        allocate(flows, {"wire": 0})
        self.assertEqual([f["rate"] for f in flows], [0, 0, 0])
        allocate(flows, {"wire": 100})
        self.assertEqual([f["rate"] for f in flows], [100, 0, 0])
        allocate([], {})

    def test_hundreds_of_cap_levels_complete_filling(self):
        flows = [flow(str(i), limit=i) for i in range(1, 257)]
        allocate(flows, {"wire": 100000})
        self.assertEqual([f["rate"] for f in flows], list(range(1, 257)))


class IntegrationTests(unittest.TestCase):
    def test_many_completions_conserve_files_and_capacity(self):
        flows = [flow(str(i)) for i in range(1, 129)]
        remaining = {str(i): float(i) for i in range(1, 129)}
        expected = sum(remaining.values())
        done = {}
        advance(flows, {"wire": 1000000}, remaining, {}, done, 0, 1)
        self.assertEqual(len(done), 128)
        self.assertTrue(all(value == 0 for value in remaining.values()))
        self.assertAlmostEqual(sum(f["thr_fixed"] for f in flows) / 8, expected)
        self.assertAlmostEqual(sum(f["rate"] for f in flows), expected * 8)
        self.assertAlmostEqual(max(done.values()), expected * 8 / 1000000)

    def test_submicrosecond_events_do_not_overdeliver_or_extend_bin(self):
        flows = [flow("one"), flow("two"), flow("stream")]
        remaining, done = {"one": 1., "two": 2.}, {}
        advance(flows, {"wire": 1e12}, remaining, {}, done, 0, .001)
        self.assertEqual([f["thr_fixed"] * .001 / 8 for f in flows[:2]], [1, 2])
        self.assertLess(done["two"], 1e-6)
        self.assertLessEqual(max(done.values()), .001)
        self.assertAlmostEqual(sum(f["rate"] for f in flows), 1e12)

    def test_setup_reservation_and_freed_share_preserve_existing_model(self):
        flows = [flow("file"), flow("stream")]
        remaining, setup, done = {"file": 1.}, {"file": .5}, {}
        advance(flows, {"wire": 16}, remaining, setup, done, 10, 2)
        self.assertEqual(done["file"], 11.5)
        self.assertEqual(flows[0]["thr_fixed"], 4)
        self.assertEqual(flows[0]["rate"], 6)
        self.assertEqual(flows[1]["rate"], 10)
        self.assertEqual(setup["file"], 0)

    def test_zero_budget_and_simultaneous_completion(self):
        flows = [flow("a"), flow("b")]
        remaining, setup, done = {"a": 1., "b": 1.}, {"a": .25, "b": .25}, {}
        advance(flows, {"wire": 0}, remaining, setup, done, 0, 1)
        self.assertEqual(remaining, {"a": 1, "b": 1})
        self.assertEqual(setup, {"a": 0, "b": 0})
        self.assertEqual(done, {})
        advance(flows, {"wire": 16}, remaining, setup, done, 1, 1)
        self.assertEqual(done, {"a": 2, "b": 2})
        self.assertEqual([f["thr_fixed"] for f in flows], [8, 8])


if __name__ == "__main__":
    unittest.main()
