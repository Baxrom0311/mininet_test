"""Actual IPv4/IPv6 traffic imported into all four API-generated topologies."""

import importlib.util
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from lab.server import create_app


@unittest.skipIf(os.environ.get("NETLAB_SKIP_NS3") == "1" or
                 not (importlib.util.find_spec("ns") or importlib.util.find_spec("ns3")),
                 "ns-3 unavailable/disabled")
class GeneratedPacketImports(unittest.TestCase):
    def run_family(self, version):
        profiles = {"ring": {"routers": 3}, "star": {"branches": 2},
                    "tree": {"depth": 2}, "campus": {"core": 1, "dist": 1, "access": 2}}
        with tempfile.TemporaryDirectory() as directory, patch("lab.engines.manager.RESULTS_DIR", directory), \
                TestClient(create_app("demo")) as client:
            for kind, dimensions in profiles.items():
                with self.subTest(kind=kind, version=version):
                    request = {"kind": kind, **dimensions, "hosts": 1, "flows": 0, "ipv6": True}
                    preview = client.post("/api/topogen/plan", json=request)
                    self.assertEqual(preview.status_code, 200, preview.text)
                    response = client.post("/api/topogen", json=request)
                    self.assertEqual(response.status_code, 200, response.text)
                    project = response.json()
                    self.assertEqual(len(project["devices"]), preview.json()["counts"]["devices"])
                    self.assertEqual(len(project["links"]), preview.json()["counts"]["links"])
                    hosts = sorted(name for name, device in project["devices"].items() if device["type"] == "host")
                    src, dst = hosts[0], hosts[-1]
                    options = {"project": project, "duration": 3.5, "ip_version": version}
                    if version == 4:
                        options.update(mode="csv", csv=f'src,dst,rate,size\n"{src}","{dst}",300kbps,128\n')
                    else:
                        options.update(mode="gravity", total_mbps=.6, weights={src: 1, dst: 1})
                    response = client.post("/api/traffic/import", json=options)
                    self.assertEqual(response.status_code, 200, response.text)
                    imported = response.json()
                    self.assertEqual(imported["count"], 1 if version == 4 else 2)
                    project["scenarios"] = {"imported": imported["scenario"]}
                    response = client.post("/api/runs", json={"project": project, "scenario": "imported", "engine": "ns3"})
                    self.assertEqual(response.status_code, 200, response.text)
                    rid = response.json()["run_id"]
                    deadline = time.monotonic() + 100
                    while time.monotonic() < deadline:
                        response = client.get(f"/api/runs/{project['name']}/{rid}")
                        self.assertEqual(response.status_code, 200, response.text)
                        result = response.json()
                        if result["status"] == "done":
                            break
                        self.assertNotIn(result["status"], ("error", "cancelled", "interrupted"), result)
                        time.sleep(.1)
                    else:
                        self.fail(f"packet import timed out: {kind}/IPv{version}")
                    self.assertEqual(result["ip_version"], version)
                    self.assertEqual(set(result["flows"]), {app["id"] for app in imported["scenario"]["apps"]})
                    self.assertTrue(all(flow["summary"]["avg_thr_bps"] > 200000 for flow in result["flows"].values()))

    def test_ipv4_csv_imports_on_every_generator(self):
        self.run_family(4)

    def test_ipv6_gravity_imports_on_every_generator(self):
        self.run_family(6)


if __name__ == "__main__":
    unittest.main()
