"""Unit tests for Faza 5 FRRouting (zebra, ospfd, bgpd, isisd), Linux FIB route
installation, and routing convergence verification helpers."""

import json
import os
import unittest
from unittest.mock import MagicMock, patch

from lab.ospf import frr_config as ospf_frr_config
from network_build import (
    FRRRouter,
    build_topology,
    generate_frr_conf,
    get_frr_neighbors,
    get_linux_fib_routes,
    measure_routing_convergence,
    start_frr_router,
    stop_frr_router,
    topo_has_frr,
    verify_all_fib_routes,
    verify_fib_route,
    wait_for_fib_route,
    wait_for_frr_convergence,
)


class TestFRRConfGeneration(unittest.TestCase):
    def test_generate_frr_conf_header_and_forwarding(self):
        conf = generate_frr_conf("r1", {})
        self.assertIn("hostname r1", conf)
        self.assertIn("ip forwarding", conf)
        self.assertIn("ipv6 forwarding", conf)
        self.assertIn("service integrated-vtysh-config", conf)

    def test_generate_frr_conf_ospf(self):
        cfg = {
            "daemons": ["zebra", "ospfd"],
            "ospf": {
                "router_id": "1.1.1.1",
                "networks": [
                    {"network": "10.0.1.0/24", "area": 0},
                    "10.0.12.0/30 area 0",
                ],
                "redistribute": ["connected", "bgp"],
                "passive_interfaces": ["r1-eth1"],
            },
            "interfaces": {
                "r1-eth1": {"ip": "10.0.1.1/24"},
                "r1-eth2": {
                    "ip": "10.0.12.1/30",
                    "ospf_p2p": True,
                    "ospf_hello": 1,
                    "ospf_dead": 4,
                    "ospf_cost": 10,
                },
            },
            "routes": [{"prefix": "192.168.0.0/16", "via": "10.0.12.2"}],
        }
        conf = generate_frr_conf("r1", cfg)
        self.assertIn("router ospf", conf)
        self.assertIn("ospf router-id 1.1.1.1", conf)
        self.assertIn("network 10.0.1.0/24 area 0", conf)
        self.assertIn("network 10.0.12.0/30 area 0", conf)
        self.assertIn("redistribute connected", conf)
        self.assertIn("redistribute bgp", conf)
        self.assertIn("passive-interface r1-eth1", conf)
        self.assertIn("interface r1-eth2", conf)
        self.assertIn("ip ospf network point-to-point", conf)
        self.assertIn("ip ospf hello-interval 1", conf)
        self.assertIn("ip ospf dead-interval 4", conf)
        self.assertIn("ip ospf cost 10", conf)
        self.assertIn("ip route 192.168.0.0/16 10.0.12.2", conf)

    def test_generate_frr_conf_bgp(self):
        cfg = {
            "daemons": ["zebra", "bgpd"],
            "bgp": {
                "asn": 65001,
                "router_id": "1.1.1.1",
                "neighbors": [
                    {"ip": "10.0.12.2", "remote_as": 65002, "next_hop_self": True}
                ],
                "networks": ["10.1.0.0/24"],
                "redistribute": ["connected", "ospf"],
            },
        }
        conf = generate_frr_conf("r1", cfg)
        self.assertIn("router bgp 65001", conf)
        self.assertIn("bgp router-id 1.1.1.1", conf)
        self.assertIn("no bgp ebgp-requires-policy", conf)
        self.assertIn("neighbor 10.0.12.2 remote-as 65002", conf)
        self.assertIn("address-family ipv4 unicast", conf)
        self.assertIn("network 10.1.0.0/24", conf)
        self.assertIn("redistribute ospf", conf)
        self.assertIn("neighbor 10.0.12.2 activate", conf)
        self.assertIn("neighbor 10.0.12.2 next-hop-self", conf)

    def test_generate_frr_conf_isis(self):
        cfg = {
            "daemons": ["zebra", "isisd"],
            "isis": {
                "tag": "CORE",
                "net": "49.0001.0000.0000.0001.00",
                "level": "level-2-only",
                "metric_style": "wide",
            },
            "interfaces": {
                "r1-eth1": {
                    "ip": "10.0.1.1/24",
                    "isis_tag": "CORE",
                    "isis_p2p": True,
                    "isis_metric": 20,
                }
            },
        }
        conf = generate_frr_conf("r1", cfg)
        self.assertIn("router isis CORE", conf)
        self.assertIn("net 49.0001.0000.0000.0001.00", conf)
        self.assertIn("is-type level-2-only", conf)
        self.assertIn("metric-style wide", conf)
        self.assertIn("ip router isis CORE", conf)
        self.assertIn("isis network point-to-point", conf)
        self.assertIn("isis metric 20", conf)

    def test_lab_ospf_frr_config(self):
        dev = {
            "id": 2,
            "type": "router",
            "config": {
                "interfaces": {
                    "r2-eth1": {"ip": "10.0.12.2/30"},
                    "r2-eth2": {"ip": "10.0.2.1/24"},
                },
                "ospf": {
                    "router_id": "2.2.2.2",
                    "passive_interfaces": ["r2-eth2"],
                },
            },
        }
        conf = ospf_frr_config(dev)
        self.assertIn("router ospf", conf)
        self.assertIn("ospf router-id 2.2.2.2", conf)
        self.assertIn("network 10.0.12.0/30 area 0", conf)
        self.assertIn("network 10.0.2.0/24 area 0", conf)
        self.assertIn("passive-interface r2-eth2", conf)
        self.assertIn("interface r2-eth1", conf)
        self.assertIn("ip ospf network point-to-point", conf)


class TestFIBRouteVerification(unittest.TestCase):
    def test_get_linux_fib_routes_json(self):
        mock_node = MagicMock()
        mock_node.cmd.return_value = json.dumps([
            {"dst": "10.0.1.0/24", "dev": "r1-eth1", "protocol": "kernel", "prefsrc": "10.0.1.1"},
            {"dst": "10.0.2.0/24", "gateway": "10.0.12.2", "dev": "r1-eth2", "protocol": "ospf", "metric": 20},
            {"dst": "default", "gateway": "192.168.1.1", "dev": "eth0", "protocol": "zebra"},
        ])
        routes = get_linux_fib_routes(mock_node)
        self.assertEqual(len(routes), 3)
        self.assertEqual(routes[0]["prefix"], "10.0.1.0/24")
        self.assertEqual(routes[1]["gateway"], "10.0.12.2")
        self.assertEqual(routes[1]["proto"], "ospf")
        self.assertEqual(routes[2]["prefix"], "default")

    def test_get_linux_fib_routes_text_fallback(self):
        mock_node = MagicMock()
        # First call fails json, second returns text
        mock_node.cmd.side_effect = [
            "command not supported",
            "10.0.1.0/24 dev r1-eth1 proto kernel scope link src 10.0.1.1\n"
            "10.0.2.0/24 via 10.0.12.2 dev r1-eth2 proto ospf metric 20\n"
            "default via 192.168.1.1 dev eth0 proto zebra\n",
        ]
        routes = get_linux_fib_routes(mock_node)
        self.assertEqual(len(routes), 3)
        self.assertEqual(routes[1]["prefix"], "10.0.2.0/24")
        self.assertEqual(routes[1]["gateway"], "10.0.12.2")
        self.assertEqual(routes[1]["dev"], "r1-eth2")
        self.assertEqual(routes[1]["proto"], "ospf")
        self.assertEqual(routes[1]["metric"], 20)

    def test_verify_fib_route(self):
        mock_node = MagicMock()
        mock_node.cmd.return_value = json.dumps([
            {"dst": "10.0.2.0/24", "gateway": "10.0.12.2", "dev": "r1-eth2", "protocol": "ospf"},
        ])
        ok, r, all_r = verify_fib_route(mock_node, "10.0.2.0/24", expected_via="10.0.12.2", expected_proto="ospf")
        self.assertTrue(ok)
        self.assertEqual(r["gateway"], "10.0.12.2")

        # Wrong gateway
        ok, _, _ = verify_fib_route(mock_node, "10.0.2.0/24", expected_via="10.0.99.1")
        self.assertFalse(ok)

        # Missing prefix
        ok, _, _ = verify_fib_route(mock_node, "10.0.3.0/24")
        self.assertFalse(ok)

    def test_verify_all_fib_routes(self):
        mock_net = MagicMock()
        r1_node = MagicMock()
        r1_node.cmd.return_value = json.dumps([
            {"dst": "10.0.2.0/24", "gateway": "10.0.12.2", "dev": "r1-eth2", "protocol": "ospf"},
            {"dst": "10.0.3.0/24", "gateway": "10.0.13.2", "dev": "r1-eth3", "protocol": "ospf"},
        ])
        r2_node = MagicMock()
        r2_node.cmd.return_value = json.dumps([
            {"dst": "10.0.1.0/24", "gateway": "10.0.12.1", "dev": "r2-eth1", "protocol": "ospf"},
        ])
        mock_net.get.side_effect = lambda n: r1_node if n == "r1" else r2_node

        spec = {
            "r1": ["10.0.2.0/24", "10.0.3.0/24"],
            "r2": {"10.0.1.0/24": {"via": "10.0.12.1", "proto": "ospf"}},
        }
        res = verify_all_fib_routes(mock_net, spec)
        self.assertTrue(res["all_passed"])
        self.assertTrue(res["details"]["r1"]["passed"])
        self.assertTrue(res["details"]["r2"]["passed"])


class TestRoutingNeighborsAndConvergence(unittest.TestCase):
    def test_get_frr_neighbors_ospf_json(self):
        mock_node = MagicMock()
        mock_node.name = "r1"
        mock_node.cmd.return_value = json.dumps({
            "neighbors": {
                "2.2.2.2": [
                    {
                        "priority": 1,
                        "state": "Full/DR",
                        "address": "10.0.12.2",
                        "ifaceName": "r1-eth2",
                    }
                ]
            }
        })
        nbrs = get_frr_neighbors(mock_node, proto="ospf")
        self.assertEqual(len(nbrs), 1)
        self.assertEqual(nbrs[0]["neighbor_id"], "2.2.2.2")
        self.assertEqual(nbrs[0]["state"], "Full")
        self.assertTrue(nbrs[0]["converged"])

    def test_get_frr_neighbors_ospf_text_fallback(self):
        mock_node = MagicMock()
        mock_node.name = "r1"
        mock_node.cmd.side_effect = [
            "invalid json",
            "Neighbor ID     Pri State           Dead Time Address         Interface\n"
            "2.2.2.2           1 Full/DR           39.200s 10.0.12.2       r1-eth2:10.0.12.1\n",
        ]
        nbrs = get_frr_neighbors(mock_node, proto="ospf")
        self.assertEqual(len(nbrs), 1)
        self.assertEqual(nbrs[0]["neighbor_id"], "2.2.2.2")
        self.assertEqual(nbrs[0]["state"], "Full")
        self.assertTrue(nbrs[0]["converged"])

    def test_get_frr_neighbors_bgp(self):
        mock_node = MagicMock()
        mock_node.name = "r1"
        mock_node.cmd.return_value = json.dumps({
            "ipv4Unicast": {
                "peers": {
                    "10.0.12.2": {
                        "remoteAs": 65002,
                        "state": "Established",
                        "pfxRcd": 5,
                    }
                }
            }
        })
        nbrs = get_frr_neighbors(mock_node, proto="bgp")
        self.assertEqual(len(nbrs), 1)
        self.assertEqual(nbrs[0]["neighbor"], "10.0.12.2")
        self.assertEqual(nbrs[0]["remote_as"], 65002)
        self.assertEqual(nbrs[0]["state"], "Established")
        self.assertTrue(nbrs[0]["converged"])

    def test_wait_for_fib_route(self):
        mock_node = MagicMock()
        mock_node.cmd.return_value = json.dumps([
            {"dst": "10.0.2.0/24", "gateway": "10.0.12.2", "dev": "r1-eth2", "protocol": "ospf"},
        ])
        elapsed = wait_for_fib_route(mock_node, "10.0.2.0/24", timeout=1.0, interval=0.01)
        self.assertIsNotNone(elapsed)
        self.assertGreaterEqual(elapsed, 0.0)

    def test_wait_for_frr_convergence(self):
        r1 = MagicMock()
        r1.name = "r1"
        r1.cmd.return_value = json.dumps({
            "neighbors": {
                "2.2.2.2": [{"priority": 1, "state": "Full/DR", "address": "10.0.12.2", "ifaceName": "r1-eth2"}]
            }
        })
        res = wait_for_frr_convergence([r1], proto="ospf", timeout=1.0, interval=0.01)
        self.assertTrue(res["converged"])
        self.assertIn("r1", res["details"])

    def test_measure_routing_convergence(self):
        flag = [False]

        def trigger():
            flag[0] = True

        def verify():
            return flag[0]

        dur = measure_routing_convergence(trigger, verify, timeout=1.0, interval=0.01)
        self.assertIsNotNone(dur)
        self.assertGreaterEqual(dur, 0.0)


class TestRouterLifecycle(unittest.TestCase):
    @patch("mininet.node.Host.__init__", return_value=None)
    def test_frr_router_forwarding_config(self, mock_init):
        router = FRRRouter("r1")
        router.cmd = MagicMock()
        # Mock defaultIntf so Host.config() does not fail
        mock_intf = MagicMock()
        mock_intf.name = "r1-eth0"
        router.defaultIntf = MagicMock(return_value=mock_intf)
        router.nameToIntf = {"r1-eth0": mock_intf}
        router.waiting = False
        router.inNamespace = True
        router.config()
        self.assertTrue(router.cmd.called)
        cmd_args = " ".join(str(c[0][0]) for c in router.cmd.call_args_list)
        self.assertIn("net.ipv4.ip_forward=1", cmd_args)

    @patch("time.sleep", return_value=None)
    @patch("shutil.chown")
    @patch("os.makedirs")
    @patch("builtins.open")
    def test_start_frr_router(self, mock_open, mock_dirs, mock_chown, mock_sleep):
        mock_node = MagicMock()
        mock_node.name = "r1"
        mock_node.cmd.return_value = ""

        with patch("os.path.exists", return_value=True):
            res = start_frr_router(mock_node, {
                "daemons": ["ospfd"],
                "ospf": {"router_id": "1.1.1.1"},
            }, run_dir="/tmp/netlab_test/run", etc_dir="/tmp/netlab_test/etc")

        self.assertEqual(res["router"], "r1")
        self.assertIn("zebra", res["daemons"])
        self.assertIn("ospfd", res["daemons"])
        self.assertIn("staticd", res["daemons"])
        cmd_calls = [c[0][0] for c in mock_node.cmd.call_args_list]
        self.assertTrue(any("net.ipv4.ip_forward=1" in c for c in cmd_calls))

    @patch("os.listdir", return_value=["zebra.pid", "ospfd.pid"])
    @patch("os.path.isdir", return_value=True)
    @patch("os.kill")
    @patch("builtins.open", unittest.mock.mock_open(read_data="1234\n"))
    def test_stop_frr_router(self, mock_kill, mock_isdir, mock_listdir):
        mock_node = MagicMock()
        mock_node.name = "r1"
        res = stop_frr_router(mock_node, run_dir="/tmp/test_run")
        self.assertEqual(res["router"], "r1")
        self.assertIn(1234, res["stopped_pids"])
        mock_node.cmd.assert_called_with("killall -q zebra ospfd bgpd isisd staticd mgmtd 2>/dev/null")

    @patch("network_build.warn_external_controller_telemetry")
    @patch("mininet.net.Mininet")
    def test_topology_with_routers(self, mock_mn, mock_warn):
        topo = {
            "switches": ["s1"],
            "hosts": {"h1": {"ip": "10.0.1.10/24", "switch": "s1"}},
            "routers": {
                "r1": {
                    "ip": "10.0.1.1/24",
                    "switch": "s1",
                    "daemons": ["zebra", "ospfd"],
                }
            },
            "links": {},
            "router_links": {},
        }
        self.assertTrue(topo_has_frr(topo))
        net_instance = MagicMock()
        mock_mn.return_value = net_instance
        net = build_topology(topo)
        hosts_added = [call[0][0] for call in net_instance.addHost.call_args_list]
        self.assertIn("h1", hosts_added)
        self.assertIn("r1", hosts_added)



if __name__ == "__main__":
    unittest.main()
