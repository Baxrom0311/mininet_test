"""Generation/import requests must validate before allocating or executing work."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from lab.engines import analytic
from lab.model import validate_project, validate_scenario
from lab.server import create_app
from lab.topogen import generate


class _ApiTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.projects = root / "projects"
        for target, directory in (("lab.server.PROJECTS_DIR", self.projects),
                                  ("lab.engines.manager.RESULTS_DIR", root / "results")):
            setting = patch(target, str(directory))
            setting.start()
            self.addCleanup(setting.stop)
        self.client = TestClient(create_app("demo", token="test"))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.client.cookies.set("netlab_token", "test")

    def post(self, route, body):
        # Exercise the decoder's nonstandard NaN/Infinity path as well.
        return self.client.post(route, content=json.dumps(body, allow_nan=True),
                                headers={"Content-Type": "application/json"})

    def assert_no_work(self, project=None):
        manager = self.client.app.state.runs
        self.assertFalse(manager.active)
        self.assertFalse(self.client.get("/api/net/status").json()["running"])
        self.assertEqual(list(self.projects.glob("*.json")), [])
        if project is not None:
            self.assertEqual(manager.store.list(project["name"]), [])
            self.assertEqual(manager.campaigns.store.list(project["name"])["total"], 0)


class TopologyGenerationApiTests(_ApiTest):
    def test_preview_matches_generated_graphs_without_constructing_a_builder(self):
        profiles = [
            {"kind": "ring", "routers": 3, "hosts": 2, "flows": 3, "core_bw": 11, "lan_bw": 7},
            {"kind": "star", "branches": 3, "hosts": 1, "flows": 2},
            {"kind": "tree", "depth": 3, "hosts": 1, "flows": 2},
            {"kind": "campus", "core": 2, "dist": 2, "access": 3, "hosts": 1,
             "flows": 2, "core_bw": 17, "dist_bw": 13, "lan_bw": 7},
        ]
        with patch("lab.engines.analytic.run") as engine:
            for body in profiles:
                with self.subTest(kind=body["kind"]):
                    with patch("lab.topogen._B", side_effect=AssertionError("preview constructed a topology")):
                        response = self.post("/api/topogen/plan", body)
                    self.assertEqual(response.status_code, 200, response.text)
                    plan = response.json()
                    self.assertEqual(plan["kind"], body["kind"])
                    self.assertFalse(plan["ipv6"])
                    self.assertIsInstance(plan["options"], dict)
                    self.assertIsInstance(plan["limits"], dict)
                    response = self.post("/api/topogen", body)
                    self.assertEqual(response.status_code, 200, response.text)
                    project = response.json()
                    self.assertEqual(validate_project(project), [])
                    devices, links = project["devices"], project["links"]
                    counts = {
                        "routers": sum(d["type"] == "router" for d in devices.values()),
                        "switches": sum(d["type"] == "switch" for d in devices.values()),
                        "hosts": sum(d["type"] in ("host", "server") for d in devices.values()),
                        "devices": len(devices), "links": len(links),
                        "transit_links": sum(all(devices[link[end]["dev"]]["type"] == "router"
                                                 for end in ("a", "b")) for link in links.values()),
                        "flows": len(project.get("scenarios", {}).get("asosiy", {}).get("apps", [])),
                    }
                    self.assertEqual(plan["counts"], counts)
                    self.assert_no_work(project)
            engine.assert_not_called()

    def test_ipv6_and_bandwidth_settings_reach_a_usable_generated_project(self):
        body = {"kind": "ring", "routers": 3, "hosts": 1, "flows": 1,
                "name": "api_dual", "ipv6": True, "core_bw": 12.5, "lan_bw": 7}
        response = self.post("/api/topogen/plan", body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ipv6"])
        response = self.post("/api/topogen", body)
        self.assertEqual(response.status_code, 200, response.text)
        project = response.json()
        self.assertEqual(validate_project(project), [])
        for device in project["devices"].values():
            config = device.get("config") or {}
            for interface in (config.get("interfaces") or {}).values():
                self.assertIn("ip6", interface)
            if config.get("gateway"):
                self.assertIn("gateway6", config)
        for link in project["links"].values():
            transit = all(project["devices"][link[end]["dev"]]["type"] == "router" for end in ("a", "b"))
            self.assertEqual(link["params"]["bw"], 12.5 if transit else 7)
        scenario = project["scenarios"]["asosiy_ipv6"]
        self.assertEqual(scenario["ip_version"], 6)
        self.assertEqual(validate_scenario(project, scenario), [])
        before = copy.deepcopy(project)
        result = analytic.run(project, scenario)
        self.assertTrue(result["flows"])
        self.assertTrue(all(flow["summary"]["avg_thr_bps"] > 0 for flow in result["flows"].values()))
        self.assertEqual(project, before)
        self.assert_no_work(project)

    def test_null_or_blank_known_options_retain_api_defaults(self):
        baseline = self.post("/api/topogen/plan", {"kind": "ring", "flows": 0})
        changed = self.post("/api/topogen/plan", {"kind": "ring", "routers": None,
                                                 "hosts": "", "flows": 0})
        self.assertEqual(baseline.status_code, 200, baseline.text)
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json(), baseline.json())
        self.assert_no_work()

    def test_malformed_options_are_400_before_any_builder_allocation(self):
        cases = [
            {}, {"kind": None}, {"kind": []}, {"kind": {}}, {"kind": True}, {"kind": "unknown"},
            {"kind": "ring", "depth": 2}, {"kind": "ring", "dist_bw": 10},
            {"kind": "ring", "unknown_option": "value"}, {"kind": "ring", "name": []},
            {"kind": "ring", "routers": True}, {"kind": "ring", "routers": 3.5},
            {"kind": "ring", "hosts": float("nan")}, {"kind": "ring", "flows": float("inf")},
            {"kind": "ring", "core_bw": False}, {"kind": "ring", "lan_bw": 0},
            {"kind": "campus", "dist": 0}, {"kind": "ring", "ipv6": "false"},
            {"kind": "ring", "ipv6": 1}, {"kind": "ring", "ipv6": []},
        ]
        with patch("lab.topogen._B", side_effect=AssertionError("invalid input reached construction")), \
                patch("lab.engines.analytic.run") as engine:
            for body in cases:
                for route in ("/api/topogen", "/api/topogen/plan"):
                    with self.subTest(route=route, body=body):
                        response = self.post(route, body)
                        self.assertEqual(response.status_code, 400, response.text)
                        self.assertIsInstance(response.json().get("error"), str)
            engine.assert_not_called()
        self.assert_no_work()

    def test_resource_limits_are_checked_before_work_not_after_construction(self):
        cases = [
            {"kind": "ring", "routers": 10 ** 1000},
            {"kind": "ring", "routers": 1501, "hosts": 0, "flows": 0},
            {"kind": "ring", "hosts": 246}, {"kind": "ring", "flows": 10001},
            {"kind": "tree", "depth": 10 ** 1000},
            {"kind": "star", "branches": 256, "hosts": 0, "flows": 0},
            {"kind": "campus", "core": 180, "dist": 1, "access": 1, "hosts": 0, "flows": 0},
        ]
        with patch("lab.topogen._B", side_effect=AssertionError("oversized request allocated a builder")):
            for body in cases:
                for route in ("/api/topogen", "/api/topogen/plan"):
                    with self.subTest(route=route, body=body):
                        response = self.post(route, body)
                        self.assertEqual(response.status_code, 400, response.text)
        self.assert_no_work()


class TrafficImportApiTests(_ApiTest):
    def setUp(self):
        super().setUp()
        self.project = generate("ring", routers=3, hosts=1, flows=0, ipv6=True)

    def test_parser_and_numeric_failures_return_400_without_starting_partial_work(self):
        csv_text = "src,dst,rate\nh0x0,h2x0,1Mbps\n"
        cases = [
            {"csv": "src,dst,rate,id\nh0x0,h2x0,1Mbps," + "x" * 131073 + "\n"},
            {"csv": 'src,dst,rate\nh0x0,h2x0,"1Mbps'},
            {"csv": csv_text + 'h0x0,h2x0,"1Mbps'},
            {"csv": 'src,dst,rate\nh0x0,h2x0,"1Mbps"junk\n'},
            {"csv": "src,dst,rate\nh0x0,h2x0\n"},
            {"csv": "src,dst,rate,rate\nh0x0,h2x0,1Mbps,1Mbps\n"},
            {"csv": None}, {"csv": False}, {"csv": "\ud800"},
            {"csv": csv_text, "duration": 10 ** 1000},
            {"csv": csv_text, "duration": True},
            {"csv": csv_text, "duration": float("nan")},
            {"mode": "gravity", "weights": {"h0x0": 10 ** 1000, "h2x0": 1}},
            {"mode": "gravity", "weights": {"h0x0": True, "h2x0": 1}},
            {"mode": "gravity", "weights": {"h0x0": float("inf"), "h2x0": 1}},
            {"mode": "gravity", "total_mbps": True},
            {"mode": "gravity", "total_mbps": 1e-9},
        ]
        before = copy.deepcopy(self.project)
        with patch("lab.engines.analytic.run") as engine:
            for options in cases:
                with self.subTest(options=options):
                    response = self.post("/api/traffic/import", {"project": self.project, **options})
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertIsInstance(response.json().get("error"), str)
                    self.assert_no_work(self.project)
            engine.assert_not_called()
        self.assertEqual(self.project, before)

    def test_quoted_bom_csv_import_is_usable_for_both_address_families(self):
        text = '\ufeffsrc,dst,rate,id,dscp,size\n"h0x0","h2x0","500kbps","download,1",46,1000\n'
        before = copy.deepcopy(self.project)
        for version in (4, 6):
            with self.subTest(version=version):
                response = self.post("/api/traffic/import", {"project": self.project, "csv": text,
                                                           "duration": 2, "ip_version": version})
                self.assertEqual(response.status_code, 200, response.text)
                matrix = response.json()
                self.assertEqual(matrix["count"], 1)
                self.assertEqual(matrix["offered_bps"], 500000)
                app = matrix["scenario"]["apps"][0]
                self.assertEqual((app["id"], app["dscp"], app["size"]), ("download,1", 46, 1000))
                self.assertEqual(validate_scenario(self.project, matrix["scenario"]), [])
                result = analytic.run(self.project, matrix["scenario"])
                self.assertGreater(result["flows"]["download,1"]["summary"]["avg_thr_bps"], 490000)
                self.assert_no_work(self.project)
        self.assertEqual(self.project, before)


if __name__ == "__main__":
    unittest.main()
