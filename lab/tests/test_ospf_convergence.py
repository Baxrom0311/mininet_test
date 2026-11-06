"""Forwarding-table delay, not live OSPF. Fixtures below are synthetic."""

import copy
import unittest

from lab.addressing import project_for_family
from lab.drivers.demo_driver import Net
from lab.engines import analytic
from lab.engines.ospf_convergence import OspfConvergence
from lab.model import validate_scenario
from lab.survivability import _convergence_hint
from lab.topogen import generate


def fixture(version=4, delay_s=2.5):
    p = generate("ring", routers=3, hosts=1, flows=1, ipv6=True)
    cut = next(lid for lid, l in p["links"].items()
               if {l["a"]["dev"], l["b"]["dev"]} == {"r0", "r2"})
    sc = {"duration": 14, "ip_version": version, "ospf_convergence_s": delay_s,
          "apps": [{"id": "video", "type": "udp", "src": "h0x0", "dst": "h2x0",
                    "rate": "500kbps", "start": 2, "stop": 14}],
          "events": [{"t": 4, "action": "link_down", "link": cut},
                     {"t": 10, "action": "link_up", "link": cut}]}
    return p, sc, cut


class OspfTests(unittest.TestCase):
    def test_validation(self):
        p, sc, _ = fixture()
        for valid in (0, 1.7, 3600):
            self.assertEqual(validate_scenario(p, {**sc, "ospf_convergence_s": valid}), [])
        for invalid in (-1, True, None, "1.7", "", float("nan"), float("inf"), 3601, 10**500):
            with self.subTest(value=invalid):
                bad = {**sc, "ospf_convergence_s": invalid}
                self.assertTrue(any("ospf_convergence_s" in e for e in validate_scenario(p, bad)))
                with self.assertRaises(ValueError):
                    analytic.run(p, bad)

    def test_analytic_outage_reroute_and_link_up_for_both_families(self):
        for version in (4, 6):
            with self.subTest(ip_version=version):
                p, sc, _ = fixture(version)
                before = copy.deepcopy(p)
                result = analytic.run(p, sc)
                pts = {x["t"]: x["thr_bps"] for x in result["flows"]["video"]["series"]}
                self.assertGreater(pts[4], 400000)
                for t in (5, 6, 7):
                    self.assertEqual(pts[t], 0)
                for t in range(8, 15):
                    self.assertGreater(pts[t], 400000)  # link up keeps a working alternate route
                self.assertTrue(any(e["t"] == 7 and e["dev"] == "r0" for e in result["route_events"]))
                self.assertTrue(any("ospf_convergence_s=2.5" in note for note in result["notes"]))
                self.assertEqual(p, before)

    def test_missing_and_zero_delay_preserve_immediate_spf(self):
        p, sc, _ = fixture()
        absent = dict(sc)
        absent.pop("ospf_convergence_s")
        a, b = analytic.run(p, absent), analytic.run(p, {**sc, "ospf_convergence_s": 0})
        self.assertEqual(a["flows"], b["flows"])
        self.assertEqual(a["route_events"], b["route_events"])
        self.assertGreater(next(pt["thr_bps"] for pt in a["flows"]["video"]["series"] if pt["t"] == 5), 400000)

    def test_noop_does_not_restart_hold_and_initially_down_has_no_hold(self):
        p, sc, cut = fixture()
        sc["events"].insert(1, {"t": 5, "action": "link_down", "link": cut})
        result = analytic.run(p, sc)
        self.assertTrue(any(e["t"] == 7 and e["dev"] == "r0" for e in result["route_events"]))
        p["links"][cut]["up"] = False
        result = analytic.run(p, {**sc, "events": []})
        self.assertTrue(all(pt["thr_bps"] > 400000 for pt in result["flows"]["video"]["series"]))

    def test_overlapping_changes_keep_last_installed_table(self):
        p, sc, cut = fixture()
        p = project_for_family(p, 4)
        ospf = OspfConvergence(sc)
        dst = Net(p).ip_of("h2x0").ip
        old = Net(p).lookup("r0", dst)
        ospf.before_change(p, 4)
        p["links"][cut]["up"] = False
        self.assertNotEqual(Net(p).lookup("r0", dst), old)
        # Another change during convergence must not expose the intermediate SPF table.
        ospf.before_change(p, 5)
        self.assertEqual(ospf.apply(Net(p), 6.5).lookup("r0", dst), old)
        self.assertEqual(ospf.deadline, 7.5)
        self.assertNotEqual(ospf.apply(Net(p), 7.5).lookup("r0", dst), old)
        # The next hold starts with the newly installed alternate table.
        new = Net(p).lookup("r0", dst)
        ospf.before_change(p, 8)
        p["links"][cut]["up"] = True
        self.assertEqual(ospf.apply(Net(p), 9).lookup("r0", dst), new)
        self.assertEqual(ospf.apply(Net(p), 10.5).lookup("r0", dst), old)

    def test_connected_and_static_routes_remain_current(self):
        p, sc, cut = fixture()
        p = project_for_family(p, 4)
        ospf = OspfConvergence(sc)
        ospf.before_change(p, 4)
        p["links"][cut]["up"] = False
        n = Net(p)
        dst = n.ip_of("h2x0").ip
        route = n.lookup("r0", dst)
        prefix = str(route[0])
        p["devices"]["r0"]["config"]["routes"] = [{"prefix": prefix, "via": str(route[1])}]
        n = ospf.apply(Net(p), 5)
        self.assertEqual(n.lookup("r0", dst)[3], "static")
        self.assertTrue(n.forward("h0x0", dst)[0])
        self.assertEqual([r for r in n.routes("r0") if r[3] == "connected"],
                         [r for r in Net(p).routes("r0") if r[3] == "connected"])

    def test_non_ospf_routes_and_wrong_family_are_unaffected(self):
        for daemons, version in ((["isisd"], 4), (["ospfd"], 6), (["ripd"], 4)):
            p, sc, cut = fixture(version)
            for d in p["devices"].values():
                if d["type"] == "router":
                    d["config"]["daemons"] = daemons
            p = project_for_family(p, version)
            ospf = OspfConvergence(sc)
            ospf.before_change(p, 4)
            p["links"][cut]["up"] = False
            n = ospf.apply(Net(p), 5)
            self.assertFalse(ospf.pending(5))
            self.assertEqual(n.routes("r0"), Net(p).routes("r0"))

    def test_survivability_hint_uses_selected_family_and_override(self):
        for version, protocol in ((4, "OSPF"), (6, "OSPFv3")):
            p, sc, cut = fixture(version)
            self.assertEqual(_convergence_hint(p, [cut], sc)[protocol], 2.5)


if __name__ == "__main__":
    unittest.main()
