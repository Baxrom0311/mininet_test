"""Comprehensive unit tests for Spanning Tree Protocol (802.1D STP / 802.1w RSTP).

Verifies bridge priority, root bridge election, port roles (root, designated, alternate,
disabled), path costs, hold port convergence states, broadcast storm detection,
and configuration validation without requiring root privileges or live Mininet.
"""

import copy
import unittest

from lab.stp import (
    DEFAULT_PRIORITY,
    FORWARD_DELAY,
    MAX_AGE,
    RSTP_CONV,
    bridge_priority,
    compute,
    convergence_time,
    default_cost,
    forwarding_ports,
    inspect_section,
    port_cost,
    stp_enabled,
    stp_mode,
    validate,
)


class TestSTPBasics(unittest.TestCase):
    def test_default_cost_bandwidth_mapping(self):
        self.assertEqual(default_cost(10000), 2)
        self.assertEqual(default_cost(1000), 4)
        self.assertEqual(default_cost(622), 6)
        self.assertEqual(default_cost(155), 14)
        self.assertEqual(default_cost(100), 19)
        self.assertEqual(default_cost(45), 39)
        self.assertEqual(default_cost(16), 62)
        self.assertEqual(default_cost(10), 100)
        self.assertEqual(default_cost(4), 250)
        self.assertEqual(default_cost(1), 2000)

    def test_stp_enabled_and_modes(self):
        sw = {"type": "switch", "config": {"stp": True}}
        self.assertTrue(stp_enabled(sw))

        sw_sdn = {"type": "switch", "config": {"stp": True, "mode": "sdn"}}
        self.assertFalse(stp_enabled(sw_sdn))

        sw_bond = {"type": "switch", "config": {"stp": True, "bonds": {"b1": {}}}}
        self.assertFalse(stp_enabled(sw_bond))

        router = {"type": "router", "config": {"stp": True}}
        self.assertFalse(stp_enabled(router))

        self.assertEqual(stp_mode(sw), "stp")
        sw_rstp = {"type": "switch", "config": {"stp": True, "stp_mode": "rstp"}}
        self.assertEqual(stp_mode(sw_rstp), "rstp")

    def test_bridge_priority(self):
        self.assertEqual(bridge_priority({"config": {}}), DEFAULT_PRIORITY)
        self.assertEqual(bridge_priority({"config": {"stp_priority": 4096}}), 4096)
        self.assertEqual(bridge_priority({"config": {"stp_priority": "8192"}}), 8192)
        self.assertEqual(bridge_priority({"config": {"stp_priority": "invalid"}}), DEFAULT_PRIORITY)


class TestSTPValidation(unittest.TestCase):
    def test_valid_project_passes(self):
        project = {
            "devices": {
                "s1": {"type": "switch", "config": {"stp": True, "stp_priority": 4096, "stp_mode": "stp"}},
                "s2": {"type": "switch", "config": {"stp": True, "stp_priority": 32768, "stp_mode": "rstp"}},
            }
        }
        self.assertEqual(validate(project), [])

    def test_invalid_priority_rejected(self):
        project = {
            "devices": {
                "s1": {"type": "switch", "config": {"stp_priority": 5000}},
                "s2": {"type": "switch", "config": {"stp_priority": 65536}},
                "s3": {"type": "switch", "config": {"stp_priority": -1}},
            }
        }
        errs = validate(project)
        self.assertEqual(len(errs), 3)
        self.assertTrue(all("stp_priority" in e for e in errs))

    def test_invalid_mode_and_port_cost_rejected(self):
        project = {
            "devices": {
                "s1": {
                    "type": "switch",
                    "config": {
                        "stp_mode": "pvst",
                        "ports": {"1": {"stp_cost": 0}, "2": {"stp_cost": "bad"}},
                    },
                }
            }
        }
        errs = validate(project)
        self.assertTrue(any("stp_mode" in e for e in errs))
        self.assertTrue(any("stp_cost" in e for e in errs))


class TestSTPGraphComputation(unittest.TestCase):
    def setUp(self):
        # Triangle topology: s1 - s2 - s3 - s1
        self.project = {
            "devices": {
                "s1": {"id": 1, "type": "switch", "config": {"stp": True, "stp_priority": 4096}},
                "s2": {"id": 2, "type": "switch", "config": {"stp": True, "stp_priority": 32768}},
                "s3": {"id": 3, "type": "switch", "config": {"stp": True, "stp_priority": 32768}},
                "h1": {"id": 4, "type": "host", "config": {"interfaces": {"h1-eth1": {"ip": "10.0.0.1/24"}}}},
            },
            "links": {
                "l1": {"a": {"dev": "s1", "port": 1}, "b": {"dev": "s2", "port": 1}, "params": {"bw": 100}, "up": True},
                "l2": {"a": {"dev": "s1", "port": 2}, "b": {"dev": "s3", "port": 1}, "params": {"bw": 100}, "up": True},
                "l3": {"a": {"dev": "s2", "port": 2}, "b": {"dev": "s3", "port": 2}, "params": {"bw": 100}, "up": True},
                "lh": {"a": {"dev": "s1", "port": 3}, "b": {"dev": "h1", "port": 1}, "params": {"bw": 100}, "up": True},
            },
        }

    def test_root_election_and_port_roles(self):
        st = compute(self.project)
        self.assertEqual(len(st["storm"]), 0)
        self.assertEqual(len(st["blocked"]), 1)

        # s1 has lowest priority 4096, must be root
        s1_info = st["bridges"]["s1"]
        self.assertTrue(s1_info["is_root"])
        self.assertEqual(s1_info["root"], "s1")
        self.assertIsNone(s1_info["root_port"])
        self.assertEqual(s1_info["root_cost"], 0)

        # All operational ports on root switch must be designated and forwarding
        for p in (1, 2, 3):
            self.assertEqual(s1_info["ports"][p]["role"], "designated")
            self.assertEqual(s1_info["ports"][p]["state"], "forwarding")

        # s2 and s3 should pick s1 as root
        s2_info = st["bridges"]["s2"]
        s3_info = st["bridges"]["s3"]
        self.assertEqual(s2_info["root"], "s1")
        self.assertEqual(s3_info["root"], "s1")
        self.assertEqual(s2_info["root_port"], 1)
        self.assertEqual(s3_info["root_port"], 1)

        # Link l3 between s2 and s3: s2 has lower device id (2 < 3), so s2:2 is designated, s3:2 is alternate
        self.assertEqual(s2_info["ports"][2]["role"], "designated")
        self.assertEqual(s2_info["ports"][2]["state"], "forwarding")
        self.assertEqual(s3_info["ports"][2]["role"], "alternate")
        self.assertEqual(s3_info["ports"][2]["state"], "blocking")
        self.assertIn(("s3", 2), st["blocked"])

    def test_custom_port_cost_shifts_root_port(self):
        # Make link l1 (s1-s2) high cost
        self.project["devices"]["s2"]["config"]["ports"] = {"1": {"stp_cost": 500}}
        self.assertEqual(port_cost(self.project, "s2", 1, "l1"), 500)

        st = compute(self.project)
        s2_info = st["bridges"]["s2"]
        # s2 should now reach s1 via s3 (cost 19 + 19 = 38 < 500)
        self.assertEqual(s2_info["root_port"], 2)
        self.assertEqual(s2_info["ports"][1]["role"], "alternate")
        self.assertEqual(s2_info["ports"][1]["state"], "blocking")

    def test_broadcast_storm_when_stp_disabled(self):
        p = copy.deepcopy(self.project)
        for s in ("s1", "s2", "s3"):
            p["devices"][s]["config"]["stp"] = False
        st = compute(p)
        self.assertEqual(st["storm"], {"s1", "s2", "s3"})
        for s in ("s1", "s2", "s3"):
            self.assertIn(s, st["storm_cycle"])
            self.assertEqual(set(st["storm_cycle"][s]), {"l1", "l2", "l3"})

    def test_hold_port_convergence(self):
        st = compute(self.project, hold={("s1", 1)})
        self.assertIn(("s1", 1), st["blocked"])
        self.assertEqual(st["bridges"]["s1"]["ports"][1]["state"], "listening")

    def test_forwarding_ports_helper(self):
        st = compute(self.project)
        fp = forwarding_ports(st)
        self.assertIn("s1", fp)
        self.assertEqual(fp["s1"][1], "forwarding")
        self.assertEqual(fp["s3"][2], "blocking")

    def test_convergence_time(self):
        # 802.1D direct vs indirect failure
        self.assertEqual(convergence_time(self.project, "s1", ["l1"]), 2 * FORWARD_DELAY)
        self.assertEqual(convergence_time(self.project, "s1", ["l3"]), 2 * FORWARD_DELAY + MAX_AGE)

        # RSTP convergence time
        p_rstp = copy.deepcopy(self.project)
        p_rstp["devices"]["s1"]["config"]["stp_mode"] = "rstp"
        self.assertEqual(convergence_time(p_rstp, "s1", ["l1"]), RSTP_CONV)

        # Explicit scenario override
        sc = {"stp_convergence_s": 5.0}
        self.assertEqual(convergence_time(self.project, "s1", ["l1"], sc), 5.0)

    def test_inspect_section(self):
        sec = inspect_section(self.project, "s1")
        self.assertIsNotNone(sec)
        self.assertEqual(sec["kind"], "table")
        self.assertEqual(sec["columns"], ["Port", "Rol", "Holat"])
        self.assertIn("BU ROOT", sec["hint"])


if __name__ == "__main__":
    unittest.main()
