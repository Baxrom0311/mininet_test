"""Actual queued ns-3 campaigns and paired window effects, IPv4 plus IPv6."""

import importlib.util
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from lab.server import create_app
from lab.topogen import generate


@unittest.skipIf(os.environ.get("NETLAB_SKIP_NS3") == "1" or
                 not (importlib.util.find_spec("ns") or importlib.util.find_spec("ns3")), "ns-3 unavailable/disabled")
class PacketCampaigns(unittest.TestCase):
    def test_campaign_bandwidth_effect_both_families(self):
        project = generate("ring", routers=3, hosts=1, flows=1, ipv6=True)
        link = next(lid for lid, l in project["links"].items() if "h0x0" in (l["a"]["dev"], l["b"]["dev"]))
        project["links"][link]["params"]["bw"] = .2
        project["scenarios"] = {f"v{v}": {"duration": 3.5, "sample": .4, "ip_version": v, "events": [],
            "apps": [{"id": "video", "type": "udp", "src": "h0x0", "dst": "h2x0", "rate": "600kbps", "start": .5, "stop": 3.5}]}
            for v in (4, 6)}
        with tempfile.TemporaryDirectory() as td, patch("lab.engines.manager.RESULTS_DIR", td), \
                TestClient(create_app("demo")) as client:
            for version in (4, 6):
                with self.subTest(version=version):
                    response = client.post("/api/campaigns", json={"project": project, "scenario": f"v{version}", "engine": "ns3",
                        "param": {"kind": "link", "link": link, "field": "bw"}, "values": [2], "repetitions": 2, "seed": 42,
                        "start_s": 1.2, "end_s": 3.5})
                    self.assertEqual(response.status_code, 200, response.text)
                    plan = response.json()
                    base = f"/api/campaigns/{project['name']}/{plan['campaign_id']}"
                    deadline = time.monotonic() + 110
                    while time.monotonic() < deadline:
                        response = client.get(base)
                        self.assertEqual(response.status_code, 200, response.text)
                        state = response.json()
                        self.assertNotIn(state["status"], ("failed", "interrupted", "submission_error", "incomplete"), state)
                        if state["complete"]:
                            break
                        time.sleep(.1)
                    else:
                        self.fail("packet campaign timed out")
                    response = client.get(base + "/report.json")
                    self.assertEqual(response.status_code, 200, response.text)
                    report = response.json()
                    self.assertTrue(report["analysis_complete"])
                    comparison = report["comparisons"][0]
                    self.assertIsNone(comparison["error"])
                    row = next(r for r in comparison["result"]["metrics"] if r["metric"] == "avg_thr_bps")
                    self.assertEqual(row["pairs"], 2)
                    self.assertGreater(row["delta_mean"], 200000)
                    self.assertGreater(row["ci95"][0], 0)
                    self.assertEqual(len(report["campaign"]["runs"]), 4)
                    response = client.get(base + "/report.json?compact=true")
                    self.assertEqual(response.status_code, 200, response.text)
                    compact = response.json()
                    self.assertEqual(compact["representation"], "cohort_references")
                    self.assertEqual(compact["comparisons"][0]["result"]["metrics"], comparison["result"]["metrics"])
                    self.assertEqual(compact["comparisons"][0]["result"]["baseline"]["summary_ref"], "/experiments/0/summary")


if __name__ == "__main__":
    unittest.main()
