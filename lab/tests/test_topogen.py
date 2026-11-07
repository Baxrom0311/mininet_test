"""Topology budgets are checked before constructing any graph objects."""

import copy
import ipaddress
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from lab.model import validate_project, validate_scenario
from lab.topogen import KINDS, campus, generate, plan, ring, star, tree


class TopologyPlanningTests(unittest.TestCase):
    def test_all_kinds_plan_exact_generated_counts_and_valid_both_families(self):
        cases = (("ring", {"routers": 7, "hosts": 2, "flows": 8}),
                 ("star", {"branches": 4, "hosts": 2, "flows": 6}),
                 ("tree", {"depth": 4, "hosts": 2, "flows": 10}),
                 ("campus", {"core": 3, "dist": 4, "access": 7, "hosts": 2, "flows": 8}),
                 ("campus", {"core": 1, "dist": 2, "access": 3, "hosts": 2, "flows": 4}))
        for kind, options in cases:
            for ipv6 in (False, True):
                with self.subTest(kind=kind, ipv6=ipv6):
                    request = plan(kind, ipv6=ipv6, **options)
                    project = generate(kind, ipv6=ipv6, **options)
                    counts = request["counts"]
                    self.assertEqual(counts["devices"], len(project["devices"]))
                    self.assertEqual(counts["links"], len(project["links"]))
                    for dtype, field in (("router", "routers"), ("switch", "switches"), ("host", "hosts")):
                        self.assertEqual(counts[field], sum(d["type"] == dtype for d in project["devices"].values()))
                    transit = sum(all(project["devices"][link[end]["dev"]]["type"] == "router" for end in ("a", "b"))
                                  for link in project["links"].values())
                    self.assertEqual(counts["transit_links"], transit)
                    self.assertEqual(validate_project(project), [])
                    self.assertEqual(set(project["scenarios"]), {"asosiy", "asosiy_ipv6"} if ipv6 else {"asosiy"})
                    for scenario in project["scenarios"].values():
                        self.assertEqual(len(scenario["apps"]), counts["flows"])
                        self.assertEqual(validate_scenario(project, scenario), [])

    def test_small_rings_and_tree_depths_have_exact_transit_counts(self):
        for routers in range(3, 20):
            with self.subTest(routers=routers):
                project = generate("ring", routers=routers, hosts=0, flows=0)
                self.assertEqual(plan("ring", routers=routers, hosts=0, flows=0)["counts"]["transit_links"],
                                 len(project["links"]) - routers)
        for depth in range(1, 5):
            project = generate("tree", depth=depth, hosts=0, flows=0)
            self.assertEqual(plan("tree", depth=depth, hosts=0, flows=0)["counts"]["links"], len(project["links"]))

    def test_planning_does_not_allocate_builder_and_is_detached(self):
        options = {"routers": "4", "hosts": 2.0, "flows": "6", "core_bw": "100", "ipv6": True}
        before = copy.deepcopy(options)
        with patch("lab.topogen._B", side_effect=AssertionError("must not construct")):
            first = plan("ring", **options)
            second = plan("ring", **options)
        self.assertEqual(first, second)
        self.assertEqual(options, before)
        self.assertEqual(first["options"]["routers"], 4)
        self.assertIs(type(first["options"]["hosts"]), int)
        self.assertTrue(first["ipv6"])
        first["options"]["hosts"] = 100
        first["limits"]["devices"] = 0
        self.assertEqual(plan("ring", **options), second)

    def test_exact_device_budget_is_checked_without_construction(self):
        with patch("lab.topogen._B", side_effect=AssertionError("must not construct")):
            self.assertEqual(plan("ring", routers=1500, hosts=0, flows=0)["counts"]["devices"], 3000)
            self.assertEqual(plan("campus", core=2, dist=12, access=1493, hosts=1, flows=0)["counts"]["devices"], 3000)
            for kind, options in (("ring", {"routers": 1501, "hosts": 0, "flows": 0}),
                                  ("campus", {"core": 2, "dist": 12, "access": 1494, "hosts": 1, "flows": 0}),
                                  ("tree", {"depth": 11, "hosts": 0, "flows": 0}),
                                  ("tree", {"depth": 10, "hosts": 3, "flows": 0})):
                with self.subTest(kind=kind, options=options), self.assertRaisesRegex(ValueError, "3000"):
                    generate(kind, **options)
        self.assertEqual(plan("tree", depth=10, hosts=2, flows=0)["counts"]["devices"], 2559)

    def test_transit_link_budget_precedes_dense_core_build(self):
        options = {"core": 179, "dist": 34, "access": 1, "hosts": 0, "flows": 0}
        self.assertEqual(plan("campus", **options)["counts"]["transit_links"], 15999)
        with patch("lab.topogen._B", side_effect=AssertionError("must not construct")):
            with self.assertRaisesRegex(ValueError, "16000"):
                generate("campus", **{**options, "dist": 35})
        with patch("lab.topogen_plan.MAX_TRANSIT_LINKS", 3):
            self.assertEqual(plan("ring", routers=3, hosts=0, flows=0)["counts"]["transit_links"], 3)
            with self.assertRaisesRegex(ValueError, "transit links"):
                plan("ring", routers=4, hosts=0, flows=0)

    def test_native_port_limits_include_shared_router_fanout(self):
        for kind, good, bad in (
            ("star", {"branches": 255, "hosts": 0, "flows": 0}, {"branches": 256}),
            ("campus", {"core": 2, "dist": 254, "access": 1, "hosts": 0, "flows": 0}, {"dist": 255}),
            ("campus", {"core": 1, "dist": 1, "access": 254, "hosts": 0, "flows": 0}, {"access": 255}),
        ):
            with self.subTest(kind=kind):
                plan(kind, **good)
                with patch("lab.topogen._B", side_effect=AssertionError("must not construct")), self.assertRaisesRegex(ValueError, "255 ports"):
                    generate(kind, **{**good, **bad})

    def test_per_lan_ipv4_budget_prevents_broadcast_host_assignment(self):
        project = generate("ring", routers=3, hosts=245, flows=0)
        self.assertEqual(project["devices"]["h0x244"]["config"]["interfaces"]["h0x244-eth1"]["ip"], "10.0.0.254/24")
        for device in project["devices"].values():
            for interface in device.get("config", {}).get("interfaces", {}).values():
                address = ipaddress.ip_interface(interface["ip"])
                self.assertNotEqual(address.ip, address.network.broadcast_address)
        self.assertEqual(validate_project(project), [])
        with patch("lab.topogen._B", side_effect=AssertionError("must not construct")), self.assertRaises(ValueError):
            generate("ring", routers=3, hosts=246, flows=0)

    def test_flow_budget_and_required_endpoints_are_explicit(self):
        self.assertEqual(plan("ring", flows=10000)["counts"]["flows"], 10000)
        for options in ({"flows": 10001}, {"hosts": 0, "flows": 1}):
            with patch("lab.topogen._B", side_effect=AssertionError("must not construct")), self.assertRaises(ValueError):
                generate("ring", **options)
        for kind, options in (("star", {"branches": 1, "hosts": 1}), ("tree", {"depth": 1, "hosts": 1})):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "at least two hosts"):
                generate(kind, **options)
        self.assertEqual(generate("ring", hosts=0, flows=0)["scenarios"], {})
        project = generate("ring", routers=3, hosts=1, flows=13)
        self.assertEqual(len(project["scenarios"]["asosiy"]["apps"]), 13)

    def test_bad_counts_fail_before_build_in_all_entry_points(self):
        for value in (True, False, 1.5, "1.5", float("nan"), float("inf"), 10**1000, [], {}, None):
            with self.subTest(value=value):
                with patch("lab.topogen._B", side_effect=AssertionError("must not construct")), self.assertRaises(ValueError):
                    generate("ring", routers=value)
        calls = ((ring, {"routers": 10**1000}), (star, {"branches": 256}),
                 (tree, {"depth": 10**1000}), (campus, {"dist": 0}))
        for builder, options in calls:
            with self.subTest(builder=builder.__name__), patch("lab.topogen._B", side_effect=AssertionError("must not construct")), self.assertRaises(ValueError):
                builder(**options)

    def test_kind_dimensions_and_option_names_do_not_leak_type_errors(self):
        for kind in (None, [], {}, True, "mesh"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                generate(kind)
        for kind, options in (("ring", {"depth": 2}), ("tree", {"routers": 3}),
                              ("star", {"core": 2}), ("campus", {"branches": 4}),
                              ("ring", {"unknown": 1}), ("ring", {"routers": 2}),
                              ("tree", {"depth": 0}), ("star", {"branches": 0}),
                              ("campus", {"core": 0}), ("campus", {"dist": 0}),
                              ("campus", {"access": 0})):
            with self.subTest(kind=kind, options=options), self.assertRaises(ValueError):
                generate(kind, **options)
        with self.assertRaises(ValueError):
            ring(depth=2)
        for builder in KINDS.values():
            with self.subTest(builder=builder.__name__), self.assertRaises(ValueError):
                builder(kind="ring")

    def test_bandwidth_name_and_ipv6_inputs_are_checked_before_build(self):
        for field, values in (("core_bw", (True, 0, 1e-7, 10001, float("nan"), float("inf"), 10**1000)),
                              ("lan_bw", (False, -1, [], {})),
                              ("name", (None, True, "", "bad/name", "trailing\n", "x" * 41)),
                              ("ipv6", (None, "true", 0, 1, [], {}))):
            for value in values:
                with self.subTest(field=field, value=value), patch("lab.topogen._B", side_effect=AssertionError("must not construct")), self.assertRaises(ValueError):
                    generate("ring", **{field: value})
        self.assertEqual(plan("ring", core_bw=1e-6, lan_bw="10000")["options"]["core_bw"], 1e-6)

    def test_healthy_outputs_match_direct_entry_points_and_original_default_types(self):
        for kind, builder in KINDS.items():
            self.assertEqual(builder(), generate(kind))
            project = generate(kind)
            self.assertIs(type(next(iter(project["links"].values()))["params"]["bw"]), int)
            self.assertEqual(project, generate(kind))


class TopologyCliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "lab.topogen", *args],
                              cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=15)

    def test_cli_reports_invalid_options_without_traceback_or_partial_json(self):
        for args in (("ring", "--depth", "2"), ("campus", "--dist", "0"),
                     ("tree", "--depth", "1000000000"), ("ring", "--hosts", "246")):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("error:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_cli_generates_valid_ipv6_json(self):
        result = self.run_cli("ring", "--routers", "3", "--hosts", "1", "--flows", "2", "--ipv6")
        self.assertEqual(result.returncode, 0, result.stderr)
        project = json.loads(result.stdout)
        self.assertEqual(validate_project(project), [])
        self.assertEqual(project["scenarios"]["asosiy_ipv6"]["ip_version"], 6)


if __name__ == "__main__":
    unittest.main()
