"""Native ns-3 regression for valid scalar boundaries and canonical rate units."""

import importlib.util
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from lab.engines.ns3_runner import rate_str
from lab.server import create_app
from lab.topogen import generate


class AdapterRateTests(unittest.TestCase):
    def test_public_rate_spellings_reach_native_as_integer_bits_per_second(self):
        for value, expected in ((1e-6, "1bps"), ("5bit/s", "5bps"), ("500b/s", "500bps"),
                                ("5", "5000000bps"), ("1.5Gbps", "1500000000bps")):
            with self.subTest(value=value):
                self.assertEqual(rate_str(value), expected)
        with self.assertRaises(ValueError):
            rate_str(float("inf"))


@unittest.skipIf(os.environ.get("NETLAB_SKIP_NS3") == "1" or
                 not (importlib.util.find_spec("ns") or importlib.util.find_spec("ns3")),
                 "ns-3 unavailable/disabled")
class PacketInputBoundaries(unittest.TestCase):
    def test_small_payloads_rate_units_clipped_times_and_automatic_model_ids(self):
        project = generate("ring", routers=3, hosts=1, flows=0, ipv6=True)
        project["scenarios"] = {f"v{v}": {
            "duration": 3.5, "sample": .5, "ip_version": v, "events": [], "apps": [
                {"id": "tiny", "type": "udp", "src": "h0x0", "dst": "h2x0", "size": 1,
                 "rate": 1e-5, "start": .5, "stop": 1e308},
                {"id": "units", "type": "udp", "src": "h0x0", "dst": "h2x0", "size": 20,
                 "rate": "800b/s", "start": .5, "stop": 1e308},
                {"id": "echo", "type": "ping", "src": "h2x0", "dst": "h0x0", "size": 1,
                 "interval": 9223372036, "start": 2, "stop": 1e308},
                {"type": "ftp", "src": "h0x0", "dst": "h2x0", "file": "1KB", "start": .5}
            ]} for v in (4, 6)}
        with tempfile.TemporaryDirectory() as directory, patch("lab.engines.manager.RESULTS_DIR", directory), \
                TestClient(create_app("demo")) as client:
            for version in (4, 6):
                with self.subTest(version=version):
                    response = client.post("/api/runs", json={"project": project, "scenario": f"v{version}", "engine": "ns3"})
                    self.assertEqual(response.status_code, 200, response.text)
                    rid = response.json()["run_id"]
                    deadline = time.monotonic() + 90
                    while time.monotonic() < deadline:
                        response = client.get(f"/api/runs/{project['name']}/{rid}")
                        self.assertEqual(response.status_code, 200, response.text)
                        result = response.json()
                        if result["status"] == "done":
                            break
                        self.assertNotIn(result["status"], ("error", "cancelled", "interrupted"), result)
                        time.sleep(.1)
                    else:
                        self.fail(f"ns-3 timed out: {rid}")
                    for aid in ("tiny", "units"):
                        flow = result["flows"][aid]
                        self.assertGreater(flow["summary"]["avg_thr_bps"], 0)
                        self.assertEqual(flow["stop"], 3.5)
                    self.assertTrue(any(p.get("rtt_ms") is not None for p in result["flows"]["echo"]["series"]))
                    self.assertEqual(result["flows"]["a4"]["type"], "ftp")
                    self.assertTrue(result["flows"]["a4"]["summary"]["completed"])


if __name__ == "__main__":
    unittest.main()
