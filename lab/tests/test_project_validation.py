"""Project structure errors must stay at the admission boundary, without mutation."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from lab.model import empty_project, validate_project
from lab.server import create_app
from lab.topogen import generate


def fixture():
    project = generate("ring", routers=3, hosts=1, flows=0, ipv6=True)
    project["scenarios"] = {"test": {"duration": 3, "apps": [
        {"id": "flow", "type": "udp", "src": "h0x0", "dst": "h2x0", "rate": "1Mbps", "start": 0}
    ], "events": []}}
    return project


class ProjectValidationTests(unittest.TestCase):
    def setUp(self):
        self.project = fixture()
        self.link = next(iter(self.project["links"]))

    def assert_invalid(self, project):
        errors = validate_project(project)
        self.assertIsInstance(errors, list)
        self.assertTrue(errors, "invalid project was accepted")
        self.assertTrue(all(isinstance(error, str) and error for error in errors))

    def device_with(self, **changes):
        project = copy.deepcopy(self.project)
        project["devices"]["r0"].update(changes)
        return project

    def config_with(self, **changes):
        project = copy.deepcopy(self.project)
        project["devices"]["r0"]["config"].update(changes)
        return project

    def test_root_and_required_topology_maps_have_known_shapes(self):
        for project in (None, [], "project", 1, True):
            with self.subTest(project=project):
                self.assert_invalid(project)
        for field in ("devices", "links"):
            missing = copy.deepcopy(self.project)
            missing.pop(field)
            self.assert_invalid(missing)
            for value in (None, [], [1], "map", True, 1):
                with self.subTest(field=field, value=value):
                    self.assert_invalid({**self.project, field: value})

    def test_optional_collection_maps_reject_scalar_and_array_shapes(self):
        for field in ("controllers", "p4_programs", "scenarios", "srlg"):
            for value in ([], [1], "map", True, 1):
                with self.subTest(field=field, value=value):
                    self.assert_invalid({**self.project, field: value})

    def test_device_records_and_config_are_objects(self):
        for value in (None, [], [1], "device", True, 1):
            project = copy.deepcopy(self.project)
            project["devices"]["r0"] = value
            with self.subTest(device=value):
                self.assert_invalid(project)
        for value in ([], [1], "config", True, 1):
            with self.subTest(config=value):
                self.assert_invalid(self.device_with(config=value))
        for value in ([], {}, True, 1):
            with self.subTest(type=value):
                self.assert_invalid(self.device_with(type=value))

    def test_interface_and_route_containers_report_field_errors(self):
        for value in ([], [1], "interfaces", True, 1):
            with self.subTest(interfaces=value):
                self.assert_invalid(self.config_with(interfaces=value))
        for value in (None, [], "interface", True, 1):
            with self.subTest(interface=value):
                self.assert_invalid(self.config_with(interfaces={"r0-eth1": value}))
        for field in ("ip", "ip6"):
            for value in (123, True, [], {}):
                with self.subTest(field=field, value=value):
                    self.assert_invalid(self.config_with(interfaces={"r0-eth1": {field: value}}))
        for value in ({}, "routes", True, [None], [1], [[]]):
            with self.subTest(routes=value):
                self.assert_invalid(self.config_with(routes=value))

    def test_nested_protocol_and_service_shapes_do_not_reach_semantic_parsers(self):
        cases = [
            {"bgp": []}, {"bgp": {"asn": 65001, "neighbors": [None]}},
            {"bgp": {"asn": 65001, "networks": {}}},
            {"dhcp_server": []}, {"dhcp_server": {"pools": [None]}}, {"nat": []},
            {"firewall": []}, {"firewall": {"policy": []}}, {"firewall": {"rules": [None]}},
            {"pim": []}, {"pim": {"interfaces": [{}]}}, {"vrrp": [None]},
            {"bonds": {"bond1": []}}, {"rip_timers": []}, {"ports": {"1": []}},
            {"isis": {"interfaces": {"r0-eth1": []}}}, {"daemons": ["ospfd", []]},
        ]
        for changes in cases:
            with self.subTest(config=changes):
                self.assert_invalid(self.config_with(**changes))

    def test_links_endpoints_and_identifier_shapes_are_safe(self):
        for value in (None, [], "link", True, 1):
            project = copy.deepcopy(self.project)
            project["links"][self.link] = value
            with self.subTest(link=value):
                self.assert_invalid(project)
        for side in ("a", "b"):
            for value in (None, [], "endpoint", True, 1):
                project = copy.deepcopy(self.project)
                project["links"][self.link][side] = value
                with self.subTest(side=side, value=value):
                    self.assert_invalid(project)
            for value in ([], {}, True, 1):
                project = copy.deepcopy(self.project)
                project["links"][self.link][side]["dev"] = value
                with self.subTest(side=side, dev=value):
                    self.assert_invalid(project)

    def test_link_parameters_use_finite_exact_normalization_at_admission(self):
        for params in ([], True, {"bw": 0}, {"bw": float("nan")}, {"loss": float("nan")},
                       {"queue": float("inf")}, {"queue": 1.5}, {"queue": True},
                       {"delay": "9" * 400 + "s"}):
            project = copy.deepcopy(self.project)
            project["links"][self.link]["params"] = params
            with self.subTest(params=params):
                self.assert_invalid(project)

    def test_ids_and_endpoint_ports_reject_booleans_and_fractional_values(self):
        for value in (True, False, 1.5, float("nan"), float("inf")):
            with self.subTest(id=value):
                self.assert_invalid(self.device_with(id=value))
            project = copy.deepcopy(self.project)
            project["links"][self.link]["a"]["port"] = value
            with self.subTest(port=value):
                self.assert_invalid(project)

    def test_controllers_programs_and_shared_risk_groups_have_safe_entries(self):
        for value in (None, [], True, 1):
            with self.subTest(controller=value):
                self.assert_invalid({**self.project, "controllers": {"c1": value}})
        for value in ([], {}, True, 1):
            with self.subTest(program=value):
                self.assert_invalid({**self.project, "p4_programs": {"program": value}})
        for value in ([[]], [{}], [None], [True]):
            with self.subTest(srlg=value):
                self.assert_invalid({**self.project, "srlg": {"shared": value}})

    def test_valid_examples_generators_and_empty_project_remain_unchanged(self):
        self.assertEqual(validate_project(empty_project()), [])
        examples = Path(__file__).resolve().parents[1] / "examples"
        projects = [(path.name, json.loads(path.read_text())) for path in sorted(examples.glob("*.json"))]
        projects.extend((f"generated-{kind}", generate(kind, flows=0)) for kind in ("ring", "campus", "tree"))
        for name, project in projects:
            before = copy.deepcopy(project)
            with self.subTest(project=name):
                self.assertEqual(validate_project(project), [])
                self.assertEqual(project, before)
        bad = self.device_with(id=True)
        before = copy.deepcopy(bad)
        self.assert_invalid(bad)
        self.assertEqual(bad, before)

    def test_unknown_extension_metadata_remains_compatible_and_unmodified(self):
        project = copy.deepcopy(self.project)
        extension = {"opaque": [None, True, {"version": "future"}]}
        project["vendor_extension"] = copy.deepcopy(extension)
        project["devices"]["r0"]["vendor_extension"] = copy.deepcopy(extension)
        project["devices"]["r0"]["config"]["vendor_extension"] = copy.deepcopy(extension)
        before = copy.deepcopy(project)
        self.assertEqual(validate_project(project), [])
        self.assertEqual(project, before)


class ProjectApiValidationTests(unittest.TestCase):
    def test_invalid_project_admission_creates_no_work_or_metadata(self):
        project = fixture()
        link = next(iter(project["links"]))
        malformed = []
        for field, value in (("devices", [1]), ("controllers", {"c1": True})):
            malformed.append({**project, field: value})
        bad = copy.deepcopy(project)
        bad["devices"]["r0"]["config"]["interfaces"] = [1]
        malformed.append(bad)
        bad = copy.deepcopy(project)
        bad["links"][link]["params"]["bw"] = 0
        malformed.append(bad)
        with tempfile.TemporaryDirectory() as directory, patch("lab.engines.manager.RESULTS_DIR", directory), \
                patch("lab.engines.analytic.run") as engine, TestClient(create_app("demo", token="test")) as client:
            client.cookies.set("netlab_token", "test")
            for bad in malformed:
                response = client.post("/api/validate", json=bad)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["errors"])
                response = client.post("/api/net/start", json={"project": bad})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertFalse(client.get("/api/net/status").json()["running"])
                body = {"project": bad, "scenario": "test", "engine": "analytic", "repetitions": 2,
                        "param": {"kind": "link", "link": link, "field": "bw"}, "values": [2, 3]}
                for route in ("/api/runs", "/api/studies", "/api/experiments", "/api/campaigns"):
                    with self.subTest(route=route, project=bad):
                        response = client.post(route, json=body)
                        self.assertEqual(response.status_code, 400, response.text)
                        self.assertFalse(client.app.state.runs.active)
                        self.assertEqual(client.app.state.runs.store.list(project["name"]), [])
                        self.assertEqual(client.app.state.runs.campaigns.store.list(project["name"])["total"], 0)
            engine.assert_not_called()

    def test_invalid_live_projects_and_link_updates_preserve_running_state(self):
        project = fixture()
        link = next(iter(project["links"]))
        malformed = copy.deepcopy(project)
        malformed["devices"]["r0"]["config"]["interfaces"] = [1]
        with tempfile.TemporaryDirectory() as directory, patch("lab.engines.manager.RESULTS_DIR", directory), \
                TestClient(create_app("demo")) as client:
            response = client.post("/api/net/start", json={"project": project})
            self.assertEqual(response.status_code, 200, response.text)
            before = client.get("/api/net/project").json()
            for route, method in (("/api/net/start", "post"), ("/api/net/device/r0", "post"),
                                  ("/api/net/device/r0/config", "put"), (f"/api/net/link/{link}", "post")):
                with self.subTest(route=route):
                    response = getattr(client, method)(route, json={"project": malformed})
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertEqual(client.get("/api/net/project").json(), before)
            for body in ({"params": {"bw": 0}}, {"params": {"bw": 50}, "up": "false"}, {"up": 1}):
                with self.subTest(patch=body):
                    response = client.patch(f"/api/net/link/{link}", json=body)
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertEqual(client.get("/api/net/project").json(), before)

    def test_invalid_drafts_can_still_be_saved_with_warnings(self):
        project = fixture()
        project["devices"]["r0"]["config"]["interfaces"] = [1]
        with tempfile.TemporaryDirectory() as directory, \
                patch("lab.engines.manager.RESULTS_DIR", str(Path(directory, "results"))), \
                patch("lab.server.PROJECTS_DIR", str(Path(directory, "projects"))), \
                TestClient(create_app("demo")) as client:
            response = client.put("/api/projects/draft", json=project)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["ok"])
            self.assertTrue(response.json()["warnings"])
            saved = client.get("/api/projects/draft")
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json(), {**project, "name": "draft"})


if __name__ == "__main__":
    unittest.main()
