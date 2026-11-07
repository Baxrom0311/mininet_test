"""Project numeric boundaries must precede legacy/native consumers."""

import copy
import json
from pathlib import Path
import unittest

from lab.model import empty_project, validate_project
from lab.project_values import MAX_TX_POWER_DBM, MAX_UINT32, validate_values
from lab.validation_numbers import MAX_POSITION
from lab.wifi import ap_config, max_range_m


def fixture():
    project = empty_project("values")
    project["devices"] = {
        "r1": {"id": 1, "type": "router", "config": {"interfaces": {"r1-eth1": {"ip": "10.0.0.1/24"}}}},
        "s1": {"id": 2, "type": "switch", "config": {}},
    }
    project["links"] = {"l1": {"a": {"dev": "r1", "port": 1}, "b": {"dev": "s1", "port": 1}}}
    return project


class ProjectValuesTests(unittest.TestCase):
    def setUp(self):
        self.project = fixture()

    def configured(self, **cfg):
        project = copy.deepcopy(self.project)
        project["devices"]["r1"]["config"].update(cfg)
        return project

    def assert_invalid(self, project, field):
        errors = validate_values(project)
        self.assertTrue(errors, "invalid scalar was accepted")
        self.assertTrue(any(field in error for error in errors), errors)
        self.assertTrue(all(isinstance(error, str) for error in errors))

    def test_identity_and_topology_ports_preserve_json_integer_contract(self):
        for value in (True, False, 1.0, "1", None, 0, 65001, 10 ** 1000):
            project = copy.deepcopy(self.project)
            project["devices"]["r1"]["id"] = value
            with self.subTest(id=value):
                self.assert_invalid(project, "id")
        for value in (True, False, 1.0, "1", None, 0, 256, float("inf")):
            project = copy.deepcopy(self.project)
            project["links"]["l1"]["a"]["port"] = value
            with self.subTest(port=value):
                self.assert_invalid(project, "port")
        for value in (True, "6653", 6653.0, 1023, 65536, float("nan")):
            project = {**self.project, "controllers": {"c1": {"port": value}}}
            self.assert_invalid(project, "controllers.c1.port")
        for value in (1024, 65535):
            self.assertEqual(validate_values({**self.project, "controllers": {"c1": {"port": value}}}), [])

    def test_vlan_and_stp_integer_boundaries_do_not_truncate(self):
        for value in (True, 1.5, "1.5", 0, 4095, float("inf")):
            self.assert_invalid(self.configured(ports={"1": {"mode": "access", "vlan": value}}), "vlan")
        for value in (True, 46.5, 0, 4095, float("nan")):
            self.assert_invalid(self.configured(ports={"1": {"mode": "trunk", "allowed": [value]}}), "allowed")
        for value in (True, False, 1.5, 4095, float("inf")):
            self.assert_invalid(self.configured(ports={"1": {"mode": "trunk", "native": value}}), "native")
        for value in (0, "", None):
            self.assertEqual(validate_values(self.configured(ports={"1": {"mode": "trunk", "native": value}})), [])
        for value in (True, 1.5, -1, 61441, 1, float("inf")):
            self.assert_invalid(self.configured(stp_priority=value), "stp_priority")
        for value in (True, 1.5, 0, 200000001, float("nan")):
            self.assert_invalid(self.configured(ports={"1": {"stp_cost": value}}), "stp_cost")
        for value in (0, 4096, 61440, "4096", 4096.0):
            self.assertEqual(validate_values(self.configured(stp_priority=value)), [])

    def test_rip_and_bgp_numbers_are_exact_finite_bounded(self):
        for field, maximum in (("update", 3600), ("timeout", 7200), ("garbage", 7200)):
            for value in (True, 1.5, 0, maximum + 1, float("inf"), 10 ** 1000):
                self.assert_invalid(self.configured(rip_timers={field: value}), field)
            self.assertEqual(validate_values(self.configured(rip_timers={field: str(maximum)})), [])
        for field in ("asn", "remote_as"):
            for value in (True, 1.5, 0, MAX_UINT32 + 1, float("inf"), 10 ** 1000):
                bgp = {"asn": 1, "neighbors": [{"ip": "10.0.0.2", "remote_as": 2}]}
                if field == "asn":
                    bgp[field] = value
                else:
                    bgp["neighbors"][0][field] = value
                self.assert_invalid(self.configured(bgp=bgp), field)
        self.assertEqual(validate_values(self.configured(bgp={"asn": str(MAX_UINT32), "neighbors": [{"remote_as": 1.0}]})), [])

    def test_bgp_scalar_and_address_errors_are_reported_together(self):
        path = Path(__file__).resolve().parents[1] / "examples" / "bgp_lab.json"
        project = json.loads(path.read_text())
        project["devices"]["r3"]["config"]["bgp"]["asn"] = 0
        project["devices"]["r2"]["config"]["bgp"]["neighbors"][1]["ip"] = "x"
        for validate in (validate_values, validate_project):
            with self.subTest(validate=validate.__name__):
                errors = validate(project)
                self.assertGreaterEqual(sum("BGP" in error for error in errors), 2, errors)
                self.assertTrue(any("r3.bgp.asn" in error for error in errors), errors)
                self.assertTrue(any("r2.bgp.neighbors[1].ip" in error for error in errors), errors)

    def test_stp_priority_and_mode_errors_are_reported_together(self):
        path = Path(__file__).resolve().parents[1] / "examples" / "stp_lab.json"
        project = json.loads(path.read_text())
        project["devices"]["s1"]["config"]["stp_priority"] = 5000
        project["devices"]["s2"]["config"]["stp_mode"] = "mstp"
        for validate in (validate_values, validate_project):
            with self.subTest(validate=validate.__name__):
                errors = validate(project)
                self.assertEqual(len(errors), 2, errors)
                self.assertTrue(any("stp_priority" in error for error in errors), errors)
                self.assertTrue(any("stp_mode" in error for error in errors), errors)
        project["devices"]["s1"]["config"]["stp_priority"] = 4096
        project["devices"]["s2"]["config"]["stp_mode"] = "RSTP"
        self.assertEqual(validate_project(project), [])

    def test_vrrp_scalar_and_subnet_errors_are_reported_together(self):
        path = Path(__file__).resolve().parents[1] / "examples" / "vrrp_lab.json"
        project = json.loads(path.read_text())
        project["devices"]["r1"]["config"]["vrrp"][0]["vrid"] = 300
        project["devices"]["r2"]["config"]["vrrp"][0]["vip"] = "10.9.9.9/24"
        project["devices"]["r2"]["config"]["vrrp"].append(
            {"iface": "r2-eth1", "vrid": 2, "vip": "10.0.1.253/24", "priority": 255})
        for validate in (validate_values, validate_project):
            with self.subTest(validate=validate.__name__):
                errors = validate(project)
                self.assertGreaterEqual(sum("VRRP" in error for error in errors), 3, errors)
                self.assertTrue(any(".vrid" in error for error in errors), errors)
                self.assertTrue(any(".vip" in error for error in errors), errors)
                self.assertTrue(any(".priority" in error for error in errors), errors)

    def test_dhcp_leases_are_positive_exact_uint32(self):
        pool = {"iface": "r1-eth1", "start": "10.0.0.100", "end": "10.0.0.200"}
        for value in (True, 1.5, "1.5", 0, -1, None, MAX_UINT32 + 1, float("inf")):
            self.assert_invalid(self.configured(dhcp_server={"pools": [{**pool, "lease": value}]}), "lease")
        for value in (1, str(MAX_UINT32), 3600.0):
            self.assertEqual(validate_values(self.configured(dhcp_server={"pools": [{**pool, "lease": value}]})), [])
        self.assertEqual(validate_values(self.configured(dhcp_server={"pools": [pool]})), [])

    def test_dhcp_requires_ipv4_even_when_ipv6_strings_parse(self):
        pool = {"iface": "r1-eth1", "start": "10.0.0.100", "end": "10.0.0.200"}
        for field in ("start", "end", "gateway"):
            for value in ("fd00::1", 167772161, True, []):
                self.assert_invalid(self.configured(dhcp_server={"pools": [{**pool, field: value}]}), field)
        project = self.configured(dhcp_server={"pools": [pool]})
        project["devices"]["r1"]["config"]["interfaces"]["r1-eth1"]["ip"] = "fd00::1/64"
        self.assert_invalid(project, "interface")

    def test_vrrp_bond_and_firewall_ports_are_exact_before_peer_validation(self):
        for field, low, high in (("vrid", 1, 255), ("priority", 1, 254), ("advert_ms", 10, 40950)):
            for value in (True, 1.5, low - 1, high + 1, float("inf")):
                entry = {"vrid": 1, "priority": 100, "advert_ms": 1000, field: value}
                self.assert_invalid(self.configured(vrrp=[entry]), field)
            self.assertEqual(validate_values(self.configured(vrrp=[{"vrid": 1, field: str(high)}])), [])
        for value in (True, 1.5, 0, 256, float("inf")):
            self.assert_invalid(self.configured(bonds={"bond1": {"ports": [1, value]}}), "ports")
        for field in ("sport", "dport"):
            for value in (True, 1.5, 0, 65536, float("inf")):
                self.assert_invalid(self.configured(firewall={"rules": [{field: value}]}), field)

    def test_wifi_finite_values_preserve_fallbacks_and_prevent_power_overflow(self):
        project = copy.deepcopy(self.project)
        project["devices"]["r1"]["type"] = "wifiap"
        cfg = project["devices"]["r1"]["config"]
        for field in ("band", "tx_power", "width", "channel"):
            for value in (True, float("inf"), float("nan"), 1.5 if field in ("width", "channel") else 10 ** 1000):
                changed = copy.deepcopy(project)
                changed["devices"]["r1"]["config"][field] = value
                self.assert_invalid(changed, field)
        for value in (-(MAX_TX_POWER_DBM + 1), MAX_TX_POWER_DBM + 1, 1e308):
            self.assert_invalid({**project, "devices": {"r1": {**project["devices"]["r1"], "config": {"tx_power": value}}}}, "tx_power")
        for power in (-MAX_TX_POWER_DBM, 0, MAX_TX_POWER_DBM):
            cfg.update(width=32, channel=-1, tx_power=power)
            self.assertEqual(validate_values(project), [])
            normalized = ap_config(project["devices"]["r1"])
            self.assertEqual(normalized["width"], 20)
            self.assertEqual(normalized["channel"], 36)
            self.assertGreater(max_range_m(normalized), 0)
        cfg["standard"] = "unsupported"
        self.assert_invalid(project, "standard")

    def test_geometry_defaults_negative_scales_and_coordinate_boundaries(self):
        for scale in (None, .25, 0, -1, "0.25"):
            project = copy.deepcopy(self.project)
            project["meters_per_px"] = scale
            project["devices"]["r1"].update(x=None, y=-MAX_POSITION)
            project["devices"]["s1"].update(x=MAX_POSITION, y=None)
            before = copy.deepcopy(project)
            with self.subTest(scale=scale):
                self.assertEqual(validate_values(project), [])
                self.assertEqual(project, before)
        project = copy.deepcopy(self.project)
        project["meters_per_px"] = 10
        project["devices"]["s1"].update(x=MAX_POSITION / 10, y=-MAX_POSITION / 10)
        self.assertEqual(validate_values(project), [])
        project["devices"]["s1"]["x"] = MAX_POSITION
        self.assert_invalid(project, "devices.s1.x")

    def test_geometry_rejects_nonfinite_boolean_overflow_and_native_unsafe_scale(self):
        for scale in (True, False, float("nan"), float("inf"), 10 ** 1000, "nan", "inf"):
            self.assert_invalid({**self.project, "meters_per_px": scale}, "meters_per_px")
        for field in ("x", "y"):
            for value in (True, False, float("nan"), float("inf"), 10 ** 1000,
                          "0", [], {}, MAX_POSITION * 2):
                project = copy.deepcopy(self.project)
                project["devices"]["s1"][field] = value
                self.assert_invalid(project, f"devices.s1.{field}")
        project = copy.deepcopy(self.project)
        project["meters_per_px"] = 1e308
        project["devices"]["s1"]["x"] = 1
        self.assert_invalid(project, "devices.s1.x")

    def test_examples_extension_fields_and_optional_defaults_are_not_rewritten(self):
        root = Path(__file__).resolve().parents[1] / "examples"
        for path in root.glob("*.json"):
            project = json.loads(path.read_text())
            original = copy.deepcopy(project)
            with self.subTest(example=path.name):
                self.assertEqual(validate_values(project), [])
                self.assertEqual(project, original)
        project = self.configured(stp_priority=None, ports={"1": {"stp_cost": ""}}, bgp=None,
                                  vendor_extension={"anything": [True, 1.5]})
        original = copy.deepcopy(project)
        self.assertEqual(validate_values(project), [])
        self.assertEqual(project, original)

    def test_direct_calls_tolerate_malformed_structures_without_exceptions(self):
        self.assertTrue(validate_values(None))
        for value in (None, [], [True], True, 1, "map"):
            validate_values({"devices": value, "links": value, "controllers": value})
            validate_values({"devices": {"bad": {"id": 1, "config": value}}})


if __name__ == "__main__":
    unittest.main()
