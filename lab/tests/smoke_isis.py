"""IS-IS weighted SPF/area validation and optional ns-3 packet checks."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from lab.drivers.demo_driver import Net
from lab.isis import config, frr_config
from lab.model import validate_project
from lab.topogen import add_ipv6, generate


def fixture():
    p = generate("ring", routers=3, hosts=1, flows=1, name="isis_lab")
    for name, dev in p["devices"].items():
        if dev["type"] == "router":
            dev["config"].update(daemons=["isisd"], startup="", isis={"level": "level-2-only", "metric": 10})
    # Prefer two 10-cost hops over the direct 50-cost edge r0-r2.
    link = next(l for l in p["links"].values() if {l["a"]["dev"], l["b"]["dev"]} == {"r0", "r2"})
    end = next(link[e] for e in ("a", "b") if link[e]["dev"] == "r0")
    p["devices"]["r0"]["config"]["isis"]["interfaces"] = {f"r0-eth{end['port']}": {"metric": 50}}
    p["scenarios"] = {"asosiy": {"duration": 10, "apps": [{"id": "video", "type": "udp", "src": "h0x0",
                         "dst": "h2x0", "rate": "500kbps", "start": 2, "stop": 10}],
                         "events": [{"t": 5, "action": "link_down", "link": "l1"},
                                    {"t": 7, "action": "link_up", "link": "l1"}]}}
    return p


class ModelTests(unittest.TestCase):
    def test_weighted_spf_prefers_lower_cost(self):
        p = fixture()
        self.assertEqual(validate_project(p), [])
        n = Net(p)
        ok, why, hops, *_ = n.forward("h0x0", n.ip_of("h2x0").ip)
        self.assertTrue(ok, why)
        self.assertIn("r1", [h[0] for h in hops])
        p["links"]["l1"]["up"] = False
        n = Net(p)
        ok, why, hops, *_ = n.forward("h0x0", n.ip_of("h2x0").ip)
        self.assertTrue(ok, why)
        self.assertNotIn("r1", [h[0] for h in hops])

    def test_l1_area_and_level_eligibility(self):
        p = fixture()
        for name in ("r0", "r1", "r2"):
            p["devices"][name]["config"]["isis"]["level"] = "level-1"
        p["devices"]["r0"]["config"]["isis"]["net"] = "49.0002.0000.0000.1234.00"
        n = Net(p)
        self.assertFalse(n.forward("h0x0", n.ip_of("h2x0").ip)[0])
        for name in ("r0", "r1", "r2"):
            p["devices"][name]["config"]["isis"]["level"] = "level-2-only"
        n = Net(p)
        self.assertTrue(n.forward("h0x0", n.ip_of("h2x0").ip)[0])

    def test_passive_interfaces_do_not_form_adjacencies(self):
        p = fixture()
        p["devices"]["r0"]["config"]["isis"]["interfaces"] = {
            ifn: {"passive": True} for ifn in p["devices"]["r0"]["config"]["interfaces"]}
        n = Net(p)
        self.assertFalse(n.forward("h0x0", n.ip_of("h2x0").ip)[0])

    def test_duplicate_system_ids_and_bad_metrics_rejected(self):
        p = fixture()
        p["devices"]["r1"]["config"]["isis"]["net"] = config(p["devices"]["r0"])["net"]
        p["devices"]["r2"]["config"]["isis"]["metric"] = 0
        self.assertGreaterEqual(len(validate_project(p)), 2)

    def test_frr_configuration_and_ipv6_model(self):
        p = fixture()
        text = frr_config(p, "r0")
        self.assertIn("router isis CORE", text)
        self.assertIn("isis metric 50", text)
        self.assertIn("net 49.0001.", text)
        p = add_ipv6(p)
        n = Net(p, ip_version=6)
        self.assertEqual(n.dyn_daemons("r0"), {"isisd"})
        ok, why, hops, *_ = n.forward("h0x0", n.ip_of("h2x0").ip)
        self.assertTrue(ok, why)
        self.assertIn("r1", [h[0] for h in hops])


@unittest.skipUnless(importlib.util.find_spec("ns3") and os.environ.get("NETLAB_SKIP_NS3") != "1", "ns-3 unavailable/skipped")
class PacketTests(unittest.TestCase):
    def test_isis_routes_forward_packets_and_recompute_on_failure(self):
        p = add_ipv6(fixture())
        for version in (4, 6):
            with self.subTest(ip_version=version), tempfile.TemporaryDirectory(prefix="netlab-isis-") as td:
                sc = {**p["scenarios"]["asosiy"], "ip_version": version}
                inp, out = Path(td, "in.json"), Path(td, "out.json")
                inp.write_text(json.dumps({"project": p, "scenario": sc}))
                r = subprocess.run([sys.executable, "-m", "lab.engines.ns3_runner", str(inp), str(out)],
                                   capture_output=True, text=True, timeout=120)
                self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-2000:])
                result = json.loads(out.read_text())
                self.assertGreater(result["flows"]["video"]["summary"]["avg_thr_bps"], 350000)
                self.assertTrue(result["route_events"])


if __name__ == "__main__":
    unittest.main()
