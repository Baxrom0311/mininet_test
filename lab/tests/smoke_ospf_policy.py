"""ns-3 packet regressions for OSPF startup interface policy (not live OSPF)."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from lab.tests.test_ospf_policy import add, fixture, iface_to


def scenarios():
    def app(fid, src, dst):
        return {"id": fid, "src": src, "dst": dst, "type": "udp", "rate": "500kbps", "start": 2, "stop": 12}
    project = fixture()
    add(project, "r2", "no network 10.0.2.0/24 area 0")
    yield "unadvertised", project, {"duration": 12, "apps": [app("bad", "h0x0", "h2x0"),
                                   app("good", "h0x0", "h1x0")], "events": []}
    project = fixture()
    add(project, "r2", "passive-interface " + iface_to(project, "r2", "r0"))
    cut = next(lid for lid, link in project["links"].items()
               if {link["a"]["dev"], link["b"]["dev"]} == {"r1", "r2"})
    yield "passive", project, {"duration": 12, "apps": [app("video", "h0x0", "h2x0")],
                               "events": [{"t": 5, "action": "link_down", "link": cut},
                                          {"t": 8, "action": "link_up", "link": cut}]}
    project = json.loads((Path(__file__).parents[1] / "examples/bgp_lab.json").read_text())
    yield "bgp", project, {"duration": 12, "apps": [app("good", "pc1", "srv1"),
                          {**app("bad", "pc1", "r3"), "dst_ip": "203.0.113.2"}], "events": []}


@unittest.skipUnless(importlib.util.find_spec("ns3") and os.environ.get("NETLAB_SKIP_NS3") != "1",
                     "ns-3 unavailable or explicitly skipped")
class PacketTests(unittest.TestCase):
    def test_network_exclusion_passive_failover_and_bgp_next_hop(self):
        for label, project, sc in scenarios():
            with self.subTest(case=label), tempfile.TemporaryDirectory(prefix="netlab-ospf-policy-") as td:
                inp, out = Path(td, "in.json"), Path(td, "out.json")
                inp.write_text(json.dumps({"project": project, "scenario": sc}))
                run = subprocess.run([sys.executable, "-m", "lab.engines.ns3_runner", str(inp), str(out)],
                                     capture_output=True, text=True, timeout=120)
                self.assertEqual(run.returncode, 0, run.stdout[-1500:] + run.stderr[:2000])
                result = json.loads(out.read_text())
                if label == "passive":
                    pts = {p["t"]: p["thr_bps"] for p in result["flows"]["video"]["series"]}
                    self.assertGreater(pts[4], 400000)
                    self.assertEqual(pts[7], 0)
                    self.assertGreater(pts[11], 400000)
                    self.assertTrue(result["route_events"])
                else:
                    self.assertGreater(result["flows"]["good"]["summary"]["avg_thr_bps"], 400000)
                    self.assertGreater(result["flows"]["bad"]["summary"]["tx_pkts"], 0)
                    self.assertEqual(result["flows"]["bad"]["summary"]["rx_pkts"], 0)


if __name__ == "__main__":
    unittest.main()
