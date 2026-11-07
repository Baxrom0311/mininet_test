"""Execute project copies with nullable defaults through real IPv4/IPv6 ns-3."""

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
                 not (importlib.util.find_spec("ns") or importlib.util.find_spec("ns3")),
                 "ns-3 unavailable/disabled")
class PacketProjectDefaults(unittest.TestCase):
    def test_nullable_project_defaults_work_for_both_address_families(self):
        project = generate("ring", routers=3, hosts=1, flows=0, ipv6=True)
        project.update(controllers=None, p4_programs=None, srlg=None, meters_per_px=None)
        project["vendor_extension"] = {"opaque": [None, {"future": True}]}
        for device in project["devices"].values():
            config = device["config"]
            config.update(startup=None, ports=None, bonds=None, rip_timers=None,
                          bgp=None, dhcp_server=None, nat=None, firewall=None, pim=None, isis=None, vrrp=None)
            for interface in (config.get("interfaces") or {}).values():
                interface["mac"] = None
            if device["type"] == "host":
                config["daemons"] = None
        project["devices"]["h0x0"].update(x=None, y=None)
        for link in project["links"].values():
            link["params"].update(delay=None, jitter="", loss=None, queue=None, medium=None, qdisc=None)
        project["scenarios"] = {f"v{v}": {"duration": 3.5, "sample": .5, "ip_version": v,
            "events": None, "apps": [{"id": "video", "type": "udp", "src": "h0x0", "dst": "h2x0",
                                     "rate": "300kbps", "start": .5, "stop": 3.5}]}
            for v in (4, 6)}
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
                    self.assertGreater(result["flows"]["video"]["summary"]["avg_thr_bps"], 200000)
                    effective = result["reproducibility"]["project"]
                    self.assertEqual(effective["vendor_extension"], project["vendor_extension"])
                    self.assertNotIn("controllers", effective)
                    self.assertNotIn("delay", next(iter(effective["links"].values()))["params"])
        self.assertIsNone(project["controllers"])
        self.assertIsNone(project["devices"]["h0x0"]["x"])


if __name__ == "__main__":
    unittest.main()
