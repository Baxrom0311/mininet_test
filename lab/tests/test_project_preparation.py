"""Execution preparation must preserve drafts and reject unsafe project inputs."""

import copy
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from lab.drivers.base import EventBus
from lab.drivers.demo_driver import DemoDriver
from lab.engines.manager import RunManager
from lab.model import empty_project, validate_project
from lab.project_shape import project_for_execution
from lab.server import create_app
from lab.topogen import generate


def host_project(address=None):
    project = empty_project("preparation")
    project["devices"] = {"h1": {"id": 1, "type": "host", "config": {}}}
    if address is not None:
        project["devices"]["h1"]["config"]["interfaces"] = {"h1-eth1": {"ip": address}}
    return project


def routed_project():
    project = generate("ring", routers=3, hosts=1, flows=0, ipv6=True)
    project["scenarios"] = {"test": {"duration": 3, "ip_version": 6, "events": [], "apps": [
        {"id": "flow", "type": "udp", "src": "h0x0", "dst": "h2x0", "rate": "1Mbps", "start": 0}
    ]}}
    return project


class ProjectPreparationTests(unittest.TestCase):
    def test_healthy_project_and_opaque_metadata_are_copied_without_new_defaults(self):
        project = routed_project()
        project["extension"] = {"interfaces": None, "items": [None, {"config": None}]}
        project["devices"]["r0"]["opaque"] = {"ip": None}
        project["devices"]["r0"]["config"]["extension"] = {"routes": None}
        before = copy.deepcopy(project)
        self.assertEqual(validate_project(project), [])
        prepared = project_for_execution(project)
        self.assertEqual(prepared, before)
        self.assertIsNot(prepared, project)
        self.assertIsNot(prepared["devices"], project["devices"])
        prepared["extension"]["items"].append("changed")
        self.assertEqual(project, before)

    def test_optional_null_top_level_maps_behave_as_omitted(self):
        project = host_project()
        for field in ("controllers", "scenarios", "p4_programs", "srlg"):
            project[field] = None
        before = copy.deepcopy(project)
        self.assertEqual(validate_project(project), [])
        prepared = project_for_execution(project)
        for field in ("controllers", "scenarios", "p4_programs", "srlg"):
            self.assertNotIn(field, prepared)
        self.assertEqual(project, before)

    def test_private_runtime_state_is_not_taken_from_a_requested_project(self):
        project = routed_project()
        project.update(_stp_hold=[{}], _lag_hold={"bad": []}, _vrrp_master=["bad"],
                       _extension={"caller_metadata": None})
        before = copy.deepcopy(project)
        self.assertEqual(validate_project(project), [])
        prepared = project_for_execution(project)
        for field in ("_stp_hold", "_lag_hold", "_vrrp_master"):
            self.assertNotIn(field, prepared)
        self.assertEqual(prepared["_extension"], {"caller_metadata": None})
        self.assertEqual(project, before)

    def test_missing_or_null_config_prepares_an_empty_object(self):
        for missing in (False, True):
            project = host_project()
            if missing:
                project["devices"]["h1"].pop("config")
            else:
                project["devices"]["h1"]["config"] = None
            before = copy.deepcopy(project)
            with self.subTest(missing=missing):
                self.assertEqual(validate_project(project), [])
                prepared = project_for_execution(project)
                self.assertEqual(prepared["devices"]["h1"]["config"], {})
                self.assertEqual(project, before)

    def test_null_interface_fields_daemons_and_positions_use_adapter_defaults(self):
        project = host_project()
        project["meters_per_px"] = None
        project["devices"]["h1"].update(x=None, y=None)
        project["devices"]["h1"]["config"].update(
            daemons=None, routes=None, startup=None,
            interfaces={"h1-eth1": {"ip": None, "ip6": None, "mac": None, "opaque": None}})
        before = copy.deepcopy(project)
        self.assertEqual(validate_project(project), [])
        prepared = project_for_execution(project)
        self.assertNotIn("meters_per_px", prepared)
        device = prepared["devices"]["h1"]
        self.assertNotIn("x", device)
        self.assertNotIn("y", device)
        self.assertEqual(device["config"], {"interfaces": {"h1-eth1": {"opaque": None}}})
        self.assertEqual(project, before)

    def test_null_isis_interface_map_preserves_protocol_defaults(self):
        project = empty_project("preparation")
        project["devices"] = {"r1": {"id": 1, "type": "router", "config": {
            "daemons": ["isisd"], "interfaces": {}, "isis": {"interfaces": None}}}}
        before = copy.deepcopy(project)
        self.assertEqual(validate_project(project), [])
        prepared = project_for_execution(project)
        self.assertEqual(prepared["devices"]["r1"]["config"]["isis"], {})
        self.assertEqual(project, before)

    def test_null_or_blank_link_values_use_defaults_without_changing_requested_document(self):
        project = routed_project()
        link = next(iter(project["links"]))
        fields = ("bw", "delay", "jitter", "loss", "queue", "medium", "qdisc")
        project["links"][link]["params"] = {field: None if i % 2 else "" for i, field in enumerate(fields)}
        project["links"][link]["params"]["extension"] = None
        before = copy.deepcopy(project)
        self.assertEqual(validate_project(project), [])
        prepared = project_for_execution(project)
        self.assertEqual(prepared["links"][link]["params"], {"extension": None})
        self.assertEqual(project, before)

    def test_ipv4_point_to_point_and_host_prefixes_do_not_reject_network_addresses(self):
        for address in ("10.0.0.0/31", "10.0.0.1/31", "10.0.0.0/32", "10.0.0.255/32"):
            with self.subTest(address=address):
                self.assertEqual(validate_project(host_project(address)), [])
        for address in ("10.0.0.0/24", "10.0.0.255/24", "10.0.0.0/30", "10.0.0.3/30"):
            with self.subTest(address=address):
                self.assertTrue(validate_project(host_project(address)))

    def test_long_interface_digits_report_errors_before_integer_parsing(self):
        project = host_project()
        project["devices"]["h1"]["config"]["interfaces"] = {
            "h1-eth" + "9" * 5000: {"ip": "10.0.0.1/24"}}
        errors = validate_project(project)
        self.assertTrue(errors)
        self.assertTrue(all(isinstance(error, str) for error in errors))

    def test_names_with_trailing_newlines_cannot_pass_whole_name_validation(self):
        for field, value in (("devices", {"h1\n": {"id": 1, "type": "host", "config": {}}}),
                             ("controllers", {"c1\n": {"port": 6653, "code": ""}}),
                             ("p4_programs", {"p1\n": ""})):
            project = empty_project("preparation")
            project[field] = value
            with self.subTest(field=field):
                self.assertTrue(validate_project(project))

    def test_controller_processes_cannot_share_a_listen_port(self):
        project = empty_project("preparation")
        project["controllers"] = {"c1": {"port": 6653, "code": ""}, "c2": {"port": 6653, "code": ""}}
        self.assertTrue(validate_project(project))
        project["controllers"]["c2"]["port"] = 6654
        self.assertEqual(validate_project(project), [])

    def test_port_map_keys_use_the_same_spelling_as_runtime_lookup(self):
        for key in ("01", " 1", "１"):
            project = empty_project("preparation")
            project["devices"] = {"s1": {"id": 1, "type": "switch", "config": {
                "ports": {key: {"mode": "access", "vlan": 10}}}}}
            with self.subTest(key=key):
                self.assertTrue(validate_project(project))
        project["devices"]["s1"]["config"]["ports"] = {"1": {"mode": "access", "vlan": 10}}
        self.assertEqual(validate_project(project), [])

    def test_interface_spellings_match_the_adapter_generated_names(self):
        for ifname in ("h1-eth01", "h1-eth1.010"):
            project = host_project()
            project["devices"]["h1"]["config"]["interfaces"] = {ifname: {"ip": "10.0.0.1/24"}}
            with self.subTest(ifname=ifname):
                self.assertTrue(validate_project(project))
        for ifname in ("h1-eth1", "h1-eth1.10", "lo"):
            project = host_project()
            project["devices"]["h1"]["config"]["interfaces"] = {ifname: {"ip": "10.0.0.1/24"}}
            with self.subTest(ifname=ifname):
                self.assertEqual(validate_project(project), [])

    def test_manager_rejects_project_before_scenario_accesses_unsafe_ipv6_config(self):
        project = routed_project()
        project["devices"]["h0x0"]["config"] = ["invalid"]
        with tempfile.TemporaryDirectory() as directory, patch("lab.engines.manager.RESULTS_DIR", directory):
            bus = EventBus()
            manager = RunManager(bus, DemoDriver(bus))
            with patch("lab.engines.manager.validate_scenario", side_effect=AssertionError("unsafe scenario traversal")) as validate:
                with self.assertRaises(ValueError):
                    manager._prepare(project, "test", "analytic")
                validate.assert_not_called()
            self.assertFalse(manager.active)

    def test_manager_returns_an_execution_copy_with_optional_nulls_cleaned(self):
        project = routed_project()
        project["controllers"] = None
        project["devices"]["r0"]["config"]["startup"] = None
        link = next(iter(project["links"]))
        project["links"][link]["params"]["bw"] = None
        before = copy.deepcopy(project)
        with tempfile.TemporaryDirectory() as directory, patch("lab.engines.manager.RESULTS_DIR", directory):
            bus = EventBus()
            manager = RunManager(bus, DemoDriver(bus))
            prepared, scenario, name = manager._prepare(project, "test", "analytic")
        self.assertEqual(name, project["name"])
        self.assertEqual(scenario, prepared["scenarios"]["test"])
        self.assertNotIn("controllers", prepared)
        self.assertNotIn("startup", prepared["devices"]["r0"]["config"])
        self.assertNotIn("bw", prepared["links"][link]["params"])
        self.assertEqual(project, before)


class ScenarioProjectBoundaryTests(unittest.TestCase):
    def test_scenario_validation_endpoint_checks_project_before_scenario_traversal(self):
        project = routed_project()
        scenario = project["scenarios"]["test"]
        bad = copy.deepcopy(project)
        bad["devices"]["h0x0"]["config"] = ["invalid"]
        with tempfile.TemporaryDirectory() as directory, patch("lab.engines.manager.RESULTS_DIR", directory), \
                TestClient(create_app("demo", token="test")) as client:
            client.cookies.set("netlab_token", "test")
            with patch("lab.server.validate_scenario", side_effect=AssertionError("unsafe scenario traversal")) as validate:
                for invalid in (None, {**project, "devices": []}, bad):
                    with self.subTest(project=invalid):
                        response = client.post("/api/scenario/validate", json={"project": invalid, "scenario": scenario})
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertTrue(response.json()["errors"])
                validate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
