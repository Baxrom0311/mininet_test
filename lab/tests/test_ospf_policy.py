"""Synthetic IPv4 OSPF policy regressions; these do not run live FRR."""

import copy
import ipaddress
import json
from pathlib import Path
import unittest

from lab.drivers.demo_driver import Net
from lab.engines import analytic
from lab.model import validate_project
from lab.ospf import UnsupportedOspf
from lab.topogen import generate


def fixture():
    return generate("ring", routers=3, hosts=1, flows=1, ipv6=True)


def iface_to(project, a, b):
    link = next(link for link in project["links"].values()
                if {link["a"]["dev"], link["b"]["dev"]} == {a, b})
    end = next(link[s] for s in ("a", "b") if link[s]["dev"] == a)
    return f"{a}-eth{end['port']}"


def add(project, router, commands):
    project["devices"][router]["config"]["startup"] += "\nrouter ospf\n" + commands + "\n"


def reaches(project, version=4):
    net = Net(project, ip_version=version)
    return net.forward("h0x0", net.ip_of("h2x0").ip)[0]


class OspfPolicyTests(unittest.TestCase):
    def test_bgp_lab_unadvertised_transit_is_not_an_ospf_route(self):
        project = json.loads((Path(__file__).parents[1] / "examples/bgp_lab.json").read_text())
        before = copy.deepcopy(project)
        net = Net(project)
        transit = ipaddress.ip_network("203.0.113.0/30")
        self.assertFalse(any(row[0] == transit for row in net.routes("r1")))
        self.assertTrue(net.forward("pc1", net.ip_of("srv1").ip)[0])
        self.assertTrue(any(str(row[0]) == "10.2.0.0/24" and row[3].startswith("BGP i")
                            for row in net.routes("r1")))
        self.assertEqual(project, before)

    def test_network_selects_interface_address_and_advertises_its_actual_subnet(self):
        project = fixture()
        cfg = project["devices"]["r2"]["config"]
        cfg["startup"] = "router ospf\n network 0.0.0.0/0 area 0\n no network 0.0.0.0/0 area 0\n"
        for ifname, ic in cfg["interfaces"].items():
            ip = ipaddress.ip_interface(ic["ip"])
            cfg["startup"] += f" network {ip.ip}/32 area 0.0.0.0\n"
        self.assertEqual(validate_project(project), [])
        self.assertTrue(reaches(project))
        rows = Net(project).routes("r0")
        self.assertTrue(any(str(row[0]) == "10.0.2.0/24" and row[3].startswith("OSPF") for row in rows))
        self.assertFalse(any(str(row[0]) == "10.0.2.1/32" for row in rows))

    def test_passive_lan_is_still_advertised(self):
        project = fixture()
        cfg = project["devices"]["r2"]["config"]
        lan = next(ifn for ifn, ic in cfg["interfaces"].items() if ic["ip"].endswith("/24"))
        add(project, "r2", f"passive-interface {lan}")
        self.assertTrue(reaches(project))
        net = Net(project)
        self.assertTrue(net.reply("h0x0", net.ip_of("h0x0").ip, "h2x0", net.ip_of("h2x0").ip,
                                  net.forward("h0x0", net.ip_of("h2x0").ip)[2])[0])

    def test_one_sided_passive_transit_forces_alternate_path(self):
        project = fixture()
        dst = Net(project).ip_of("h2x0").ip
        first = Net(project).lookup("r0", dst)
        direct = iface_to(project, "r2", "r0")
        add(project, "r2", f"passive-interface {direct}")
        alternative = Net(project).lookup("r0", dst)
        self.assertNotEqual(first[1], alternative[1])
        self.assertEqual(alternative[3], "OSPF [2]")
        self.assertTrue(reaches(project))
        add(project, "r2", f"no passive-interface {direct}")
        self.assertEqual(Net(project).lookup("r0", dst), first)

    def test_passive_default_and_active_exception(self):
        project = fixture()
        add(project, "r2", "passive-interface default")
        self.assertFalse(reaches(project))
        add(project, "r2", "no passive-interface " + iface_to(project, "r2", "r0"))
        self.assertTrue(reaches(project))
        # The remote side must also permit adjacency.
        add(project, "r0", "passive-interface " + iface_to(project, "r0", "r2"))
        self.assertFalse(reaches(project))

    def test_no_network_removes_advertisement_without_removing_connected_route(self):
        project = fixture()
        add(project, "r2", "no network 10.0.2.0/24 area 0")
        self.assertFalse(reaches(project))
        self.assertTrue(any(str(row[0]) == "10.0.2.0/24" and row[3] == "connected"
                            for row in Net(project).routes("r2")))
        add(project, "r2", "network 10.0.2.0/24 area 0")
        self.assertTrue(reaches(project))

    def test_area_mismatch_blocks_adjacency_and_does_not_leak_stub_prefix(self):
        project = fixture()
        cfg = project["devices"]["r2"]["config"]
        cfg["startup"] = cfg["startup"].replace(" area 0", " area 1")
        self.assertFalse(reaches(project))
        self.assertFalse(any(str(row[0]) == "10.0.2.0/24" for row in Net(project).routes("r0")))

    def test_interface_area_enables_only_selected_interfaces(self):
        project = fixture()
        cfg = project["devices"]["r2"]["config"]
        direct = iface_to(project, "r2", "r0")
        lan = next(ifn for ifn, ic in cfg["interfaces"].items() if ic["ip"].endswith("/24"))
        cfg["startup"] = f"interface {direct}\n ip ospf area 0\nexit\n"
        self.assertFalse(reaches(project))
        cfg["startup"] += f"interface {lan}\n ip ospf area 0\nend\n"
        self.assertTrue(reaches(project))
        cfg["startup"] += f"interface {lan}\n no ip ospf area 0\n"
        self.assertFalse(reaches(project))

    def test_router_block_without_selection_is_strict_and_legacy_daemon_only_is_preserved(self):
        project = fixture()
        project["devices"]["r2"]["config"]["startup"] = "router ospf\n ospf router-id 1.2.3.4\n"
        self.assertFalse(reaches(project))
        for name, dev in project["devices"].items():
            if dev["type"] == "router":
                dev["config"]["startup"] = f"hostname {name}\n!\n"
        self.assertTrue(reaches(project))

    def test_non_ospf_network_commands_do_not_enable_interfaces(self):
        project = fixture()
        add(project, "r2", "no network 10.0.2.0/24 area 0\n!\nrouter bgp 65001\n network 10.0.2.0/24")
        self.assertFalse(reaches(project))

    def test_mixed_daemons_do_not_mislabel_rip_routes_as_ospf(self):
        project = fixture()
        for dev in project["devices"].values():
            if dev["type"] == "router":
                dev["config"]["daemons"].append("ripd")
        add(project, "r2", "no network 10.0.2.0/24 area 0")
        row = Net(project).lookup("r0", Net(project).ip_of("h2x0").ip)
        self.assertTrue(row[3].startswith("RIP"), row)
        self.assertTrue(reaches(project))

    def test_startup_type_errors_are_reported_by_validation(self):
        for value in (True, 42, [], {}):
            project = fixture()
            project["devices"]["r2"]["config"]["startup"] = value
            self.assertTrue(any("OSPF startup must be text" in err for err in validate_project(project)))

    def test_ipv4_selection_does_not_filter_ospfv3_and_other_protocols(self):
        project = fixture()
        add(project, "r2", "passive-interface default")
        self.assertFalse(reaches(project, 4))
        self.assertTrue(reaches(project, 6))
        for dev in project["devices"].values():
            if dev["type"] == "router":
                dev["config"]["daemons"] = ["ripd"]
        self.assertTrue(reaches(project, 4))

    def test_malformed_supported_commands_and_unsupported_real_scope(self):
        for command in ("network invalid area 0", "network 10.0.0.0/8 area -1",
                        "network fd00::/64 area 0", "passive-interface", "network 10.0.0.0/8"):
            project = fixture()
            add(project, "r2", command)
            self.assertTrue(any("r2: OSPF startup line" in err for err in validate_project(project)), command)
            with self.assertRaises(ValueError):
                reaches(project)
        project = fixture()
        project["devices"]["r2"]["config"]["startup"] = "router ospf vrf blue\n network 10.0.0.0/8 area 0\n"
        # A valid real FRR scope may pass global project validation, but must never
        # be flattened into unrestricted model connectivity.
        self.assertEqual(validate_project(project), [])
        with self.assertRaises(UnsupportedOspf):
            reaches(project)

    def test_analytic_forwards_only_when_advertisement_exists(self):
        project = fixture()
        sc = {"duration": 4, "apps": [{"id": "video", "src": "h0x0", "dst": "h2x0", "type": "udp",
              "rate": "500kbps", "start": 1, "stop": 4}], "events": []}
        self.assertGreater(analytic.run(project, sc)["flows"]["video"]["summary"]["avg_thr_bps"], 400000)
        add(project, "r2", "no network 10.0.2.0/24 area 0")
        self.assertEqual(analytic.run(project, sc)["flows"]["video"]["summary"]["avg_thr_bps"], 0)


if __name__ == "__main__":
    unittest.main()
