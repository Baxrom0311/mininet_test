"""Real ns-3 paired treatment comparisons for IPv4/IPv6; no kernel networking."""

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
class PacketComparison(unittest.TestCase):
    def test_paired_bandwidth_treatment_both_families(self):
        project = generate("ring", routers=3, hosts=1, flows=1, ipv6=True)
        project["scenarios"] = {}
        for version in (4, 6):
            project["scenarios"][f"v{version}"] = {"duration": 3.5, "sample": .4, "ip_version": version, "apps": [
                {"id": "video", "type": "udp", "src": "h0x0", "dst": "h2x0", "rate": "600kbps", "start": .5, "stop": 3.5}], "events": []}
        with tempfile.TemporaryDirectory() as td, patch("lab.engines.manager.RESULTS_DIR", td), \
                TestClient(create_app("demo")) as client:
            for version in (4, 6):
                with self.subTest(version=version):
                    ids = []
                    for bandwidth in (.2, 2):
                        for link in project["links"].values():
                            link["params"]["bw"] = bandwidth
                        response = client.post("/api/experiments", json={"project": project, "scenario": f"v{version}",
                                                                       "engine": "ns3", "repetitions": 2, "seed": 42})
                        self.assertEqual(response.status_code, 200, response.text)
                        ids.append(response.json()["experiment_id"])
                    deadline = time.monotonic() + 110
                    for eid in ids:
                        while time.monotonic() < deadline:
                            response = client.get(f"/api/experiments/{project['name']}/{eid}")
                            self.assertEqual(response.status_code, 200, response.text)
                            summary = response.json()
                            self.assertFalse(set(summary["statuses"]) & {"error", "cancelled", "interrupted"}, summary)
                            if summary["complete"]:
                                break
                            time.sleep(.1)
                        else:
                            self.fail("packet experiment timed out")
                    response = client.post("/api/experiments/compare", json={"project": project["name"], "baseline_id": ids[0],
                                            "candidate_id": ids[1], "start_s": 1.2, "end_s": 3.5})
                    self.assertEqual(response.status_code, 200, response.text)
                    result = response.json()
                    self.assertTrue(result["complete"])
                    self.assertEqual(result["completed_pairs"], 2)
                    throughput = next(r for r in result["metrics"] if r["metric"] == "avg_thr_bps")
                    self.assertGreater(throughput["delta_mean"], 200000)
                    self.assertEqual(throughput["missing_pairs"], 0)
                    self.assertGreater(throughput["ci95"][0], 0)
                    self.assertTrue(any(d["path"].endswith("/params/bw") for d in result["design_diff"]["changes"]))


if __name__ == "__main__":
    unittest.main()
