"""Real ns-3 packets with a modeled OSPF/OSPFv3 forwarding-table hold."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from lab.tests.test_ospf_convergence import fixture


@unittest.skipUnless(importlib.util.find_spec("ns3") and os.environ.get("NETLAB_SKIP_NS3") != "1",
                     "ns-3 unavailable or explicitly skipped")
class PacketTests(unittest.TestCase):
    def test_subsample_hold_and_recovery_ipv4_ipv6(self):
        for version in (4, 6):
            with self.subTest(ip_version=version), tempfile.TemporaryDirectory(prefix="netlab-ospf-") as td:
                p, sc, cut = fixture(version, 2.125)
                # Neither the deadline nor the first change aligns with the sample grid.
                sc.update(sample=.5, events=[{"t": 4.2500001, "action": "link_down", "link": cut},
                                            {"t": 4.75, "action": "link_down", "link": cut},
                                            {"t": 10, "action": "link_up", "link": cut}])
                inp, out = Path(td, "input.json"), Path(td, "output.json")
                inp.write_text(json.dumps({"project": p, "scenario": sc}))
                run = subprocess.run([sys.executable, "-m", "lab.engines.ns3_runner", str(inp), str(out)],
                                     capture_output=True, text=True, timeout=120)
                self.assertEqual(run.returncode, 0, run.stdout[-1500:] + run.stderr[:2000])
                result = json.loads(out.read_text())
                pts = {p["t"]: p for p in result["flows"]["video"]["series"]}
                self.assertGreater(pts[4]["thr_bps"], 400000)
                for t in (5, 5.5, 6):
                    self.assertEqual(pts[t]["thr_bps"], 0)
                # 4.2500001 + 2.125 = 6.3750001: recover inside the 6..6.5 bin.
                # Rounding the event to microseconds would apply it a sample late.
                self.assertGreater(pts[6.5]["thr_bps"], 0)
                self.assertLess(pts[6.5]["thr_bps"], 300000)
                self.assertGreater(pts[7]["thr_bps"], 400000)
                self.assertGreater(pts[11]["thr_bps"], 400000)
                self.assertGreater(pts[13.5]["thr_bps"], 400000)
                self.assertTrue(any(e["dev"] == "r0" and 6.5 <= e["t"] <= 7
                                    for e in result["route_events"]))
                self.assertTrue(any("ospf_convergence_s=2.125" in note for note in result["notes"]))
                self.assertTrue(all(b["t_end"] - b["t_start"] == .5
                                    for b in result["flows"]["video"]["series"]))


if __name__ == "__main__":
    unittest.main()
