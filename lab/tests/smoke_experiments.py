"""Actual queued ns-3 replications and exact packet-metric replay, IPv4 + IPv6."""

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
class PacketReplications(unittest.TestCase):
    def test_queued_packet_replay_both_families(self):
        project = generate("ring", routers=3, hosts=1, flows=1, ipv6=True)
        project["scenarios"] = {}
        for version in (4, 6):
            project["scenarios"][f"v{version}"] = {"duration": 3, "ip_version": version, "apps": [
                {"id": "video", "type": "udp", "src": "h0x0", "dst": "h2x0", "rate": "300kbps",
                 "start": .5, "stop": 3}], "events": []}
        with tempfile.TemporaryDirectory() as td, patch("lab.engines.manager.RESULTS_DIR", td), \
                TestClient(create_app("demo")) as client:
            for version in (4, 6):
                with self.subTest(version=version):
                    response = client.post("/api/experiments", json={"project": project, "scenario": f"v{version}",
                                                                   "engine": "ns3", "repetitions": 2, "seed": 42})
                    self.assertEqual(response.status_code, 200, response.text)
                    batch = response.json()
                    base = f"/api/runs/{project['name']}"
                    deadline = time.monotonic() + 90

                    def wait(rid):
                        while time.monotonic() < deadline:
                            run = client.get(f"{base}/{rid}").json()
                            if run["status"] == "done":
                                return run
                            self.assertNotIn(run["status"], ("error", "cancelled", "interrupted"), run)
                            time.sleep(.1)
                        self.fail(f"ns-3 timed out: {rid}")

                    runs = [wait(rid) for rid in batch["run_ids"]]
                    self.assertEqual([r["scenario"]["rng_run"] for r in runs], [1, 2])
                    for run in runs:
                        self.assertGreater(run["flows"]["video"]["summary"]["avg_thr_bps"], 200000)
                    response = client.get(f"/api/experiments/{project['name']}/{batch['experiment_id']}")
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertTrue(response.json()["complete"])
                    first = runs[0]
                    request = {"project": first["reproducibility"]["project"], "scenario": f"v{version}", "engine": "ns3"}
                    replay = wait(client.post("/api/runs", json=request).json()["run_id"])
                    self.assertEqual(first["flows"], replay["flows"])


if __name__ == "__main__":
    unittest.main()
