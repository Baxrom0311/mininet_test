"""Audit and Verification Test Suite for Overlay, Twin & Education Subsystems.

Validates:
1. lab/overlay:
   - VXLAN RFC 7348 encapsulation & BGP EVPN Type-2 (MAC/IP) & Type-5 (IP Prefix) routes.
   - WireGuard site-to-site VPN tunnel configuration, MTU/MSS clamping logic.
   - SD-WAN Engine: Dynamic SLA-based multi-path steering (MPLS vs Internet vs 5G) based on latency, jitter, loss.
2. lab/twin:
   - Digital Twin Topology Graph & Dijkstra Shortest Path First (SPF) solver.
   - What-If Analyzer: Link cut, node failure, traffic surge multiplier (1.5x - 10x), N-1 worst case vulnerability ranking.
3. lab/education:
   - Cisco NetAcad Auto-Grader: Lab rubrics (IP subnetting, OSPF, VLANs, ACLs), automated checklist scoring, remediation feedback.
4. REST endpoints for overlay, twin, and education.
"""

from __future__ import annotations

import unittest
from fastapi.testclient import TestClient

from lab.server import create_app
from lab.overlay.vxlan_evpn import (
    VXLAN_UDP_PORT,
    VXLAN_ENCAP_OVERHEAD,
    EVPNRouteType,
    VXLANHeader,
    EthernetFrame,
    IPv4Packet,
    VXLANPacket,
    EVPNRouteType2,
    EVPNRouteType5,
    VTEP,
    VXLANOverlayEngine,
    LeafSpineVXLANTopology,
    vxlan_engine,
)
from lab.overlay.wireguard_vpn import (
    Curve25519Key,
    WireGuardPeer,
    WireGuardEngine,
    wireguard_engine,
    WIREGUARD_OVERHEAD_BYTES,
)
from lab.overlay.sdwan_manager import (
    TrafficClass,
    SLASpec,
    SDWANTunnel,
    WANPath,
    FailoverEvent,
    SDWANRouter,
    SDWANManager,
    DEFAULT_SLAS,
    sdwan_manager,
)
from lab.twin.what_if_analyzer import (
    FailureType,
    RiskLevel,
    TopologyGraph,
    TopologyLink,
    TopologyNode,
    TrafficFlow,
    WhatIfAnalyzer,
    WhatIfReport,
    what_if_analyzer,
)
from lab.education import (
    AutoGraderEngine,
    GradeReport,
    LAB_RUBRICS,
    NetAcadGrader,
    auto_grader,
    get_lab_template,
    list_available_labs,
)


class TestAuditOverlaySubsystem(unittest.TestCase):
    """Detailed audit verification of Multi-Cloud Overlay (SD-WAN/VXLAN/WireGuard)."""

    def test_vxlan_vni_boundaries_and_rfc7348_flags(self):
        # Valid 24-bit VNI boundaries
        h_min = VXLANHeader(vni=0)
        h_max = VXLANHeader(vni=0xFFFFFF)
        self.assertEqual(len(h_min.encode()), 8)
        self.assertEqual(len(h_max.encode()), 8)

        # Invalid VNI values must raise ValueError
        with self.assertRaises(ValueError):
            VXLANHeader(vni=-1).encode()
        with self.assertRaises(ValueError):
            VXLANHeader(vni=0x1000000).encode()

        # Decoded flags must have I-bit valid
        decoded = VXLANHeader.decode(h_max.encode())
        self.assertEqual(decoded.vni, 0xFFFFFF)
        self.assertTrue(decoded.flags & 0x08)

    def test_vxlan_ecmp_source_port_and_encap_overhead(self):
        inner_ip = IPv4Packet(src_ip="10.0.1.1", dst_ip="10.0.1.2", proto=6, payload=b"DATA")
        inner_eth = EthernetFrame(
            src_mac="00:11:22:33:44:01",
            dst_mac="00:11:22:33:44:02",
            ethertype=0x0800,
            payload=inner_ip.encode(),
        )
        encap_pkt = VXLANPacket.encapsulate(
            src_vtep="192.168.1.1",
            dst_vtep="192.168.1.2",
            vni=20000,
            inner_frame=inner_eth,
        )
        # Overhead: Outer Eth(14) + Outer IP(20) + Outer UDP(8) + VXLAN(8) = 50
        self.assertEqual(len(encap_pkt), len(inner_eth.encode()) + VXLAN_ENCAP_OVERHEAD)

        decap = VXLANPacket.decapsulate(encap_pkt)
        src_port = decap["outer"]["src_port"]
        self.assertGreaterEqual(src_port, 49152)
        self.assertLessEqual(src_port, 65535)
        self.assertEqual(decap["vxlan"]["vni"], 20000)

    def test_evpn_routes_t2_t5_data_structures(self):
        engine = VXLANOverlayEngine()
        t2 = engine.advertise_mac_ip(
            origin_vtep_id="leaf1",
            mac="de:ad:be:ef:00:01",
            vni=100,
            ip="192.168.10.50",
            rd="65001:100",
            rt="target:65000:100",
            l3_vni=1000,
        )
        self.assertEqual(t2.l3_vni, 1000)
        self.assertIn("de:ad:be:ef:00:01", t2.route_key())

        t5 = engine.advertise_prefix(
            origin_vtep_id="leaf1",
            prefix="10.100.0.0/16",
            gw_ip="10.0.0.1",
            vni=1000,
        )
        self.assertEqual(t5.prefix, "10.100.0.0/16")
        self.assertIn("10.100.0.0/16", t5.route_key())

    def test_wireguard_mtu_mss_and_cryptokey_routing(self):
        wg = WireGuardEngine()
        res_v4 = wg.calculate_mtu_mss(underlay_mtu=1500, is_ipv6=False)
        self.assertEqual(res_v4["recommended_wg_mtu"], 1440)
        self.assertEqual(res_v4["recommended_tcp_mss"], 1400)
        self.assertEqual(res_v4["overhead_bytes"], 60)

        res_v6 = wg.calculate_mtu_mss(underlay_mtu=1500, is_ipv6=True)
        self.assertEqual(res_v6["recommended_wg_mtu"], 1420)
        self.assertEqual(res_v6["recommended_tcp_mss"], 1360)
        self.assertEqual(res_v6["overhead_bytes"], 80)

        # Cryptokey routing AllowedIPs lookup
        _, pub = Curve25519Key.generate_keypair()
        wg.add_peer(public_key=pub, endpoint="198.51.100.5:51820", allowed_ips=["10.88.0.0/16"])
        r_ok = wg.route_and_encapsulate("10.88.5.1", 500)
        self.assertTrue(r_ok["ok"])
        self.assertEqual(r_ok["routed_peer"], pub)

        r_drop = wg.route_and_encapsulate("192.168.99.1", 500)
        self.assertFalse(r_drop["ok"])
        self.assertTrue(r_drop["dropped"])

    def test_sdwan_sla_steering_all_classes(self):
        mgr = SDWANManager()
        for tc in TrafficClass:
            dec = mgr.select_path(tc)
            self.assertTrue(dec["sla_met"])
            self.assertIn("tunnel_id", dec["selected_tunnel"])

        # Test brownout degradation causing failover
        imp = mgr.inject_tunnel_impairment("mpls_1", add_latency_ms=250.0, add_loss_pct=5.0)
        self.assertIn(imp["tunnel"]["status"], ("degraded", "down"))
        self.assertGreaterEqual(imp["failovers_triggered"], 1)

        # VoIP must steer away from degraded mpls_1
        new_voip = mgr.select_path(TrafficClass.VOIP)
        self.assertNotEqual(new_voip["selected_tunnel"]["tunnel_id"], "mpls_1")


class TestAuditTwinSubsystem(unittest.TestCase):
    """Detailed audit verification of Digital Twin & Predictive What-If Analyzer."""

    def setUp(self):
        self.twin = WhatIfAnalyzer()

    def test_dijkstra_spf_edge_cases(self):
        # Same source and destination
        self.assertEqual(self.twin.graph.dijkstra_spf("Core-1", "Core-1"), ["Core-1"])

        # Non-existent node
        self.assertIsNone(self.twin.graph.dijkstra_spf("NonExistent", "Core-1"))
        self.assertIsNone(self.twin.graph.dijkstra_spf("Core-1", "NonExistent"))

        # Inactive / Down node
        g = self.twin.graph.clone()
        g.nodes["Dist-1"].status = "down"
        path = g.dijkstra_spf("Core-1", "Dist-1")
        self.assertIsNone(path)

    def test_traffic_multiplier_scaling_1_5_to_10x(self):
        for factor in [1.5, 2.0, 5.0, 10.0]:
            rep = self.twin.predict_traffic_surge(surge_factor=factor)
            self.assertEqual(rep.failure_type, FailureType.TRAFFIC_SURGE)
            self.assertGreater(rep.metrics["max_utilization_pct"], 0)
            if factor >= 5.0:
                self.assertGreater(rep.metrics["total_excess_loss_mbps"], 0)

    def test_n_minus_one_vulnerability_ranking(self):
        rankings = self.twin.rank_worst_case_failures()
        self.assertEqual(len(rankings), len([l for l in self.twin.graph.links.values() if l.status == "up"]))
        self.assertEqual(rankings[0]["rank"], 1)

        # Alias verification
        alias_rankings = self.twin.evaluate_n_minus_one_worst_case()
        self.assertEqual(len(alias_rankings), len(rankings))
        self.assertEqual(alias_rankings[0]["failed_link"], rankings[0]["failed_link"])


class TestAuditEducationSubsystem(unittest.TestCase):
    """Detailed audit verification of NetAcad Auto-Grader."""

    def setUp(self):
        self.grader = NetAcadGrader()

    def test_ccna_enterprise_routing_template_grading(self):
        # Empty submission must not recurse and report 0 score
        rep_empty = self.grader.grade("ccna_enterprise_routing", {})
        self.assertEqual(rep_empty.percentage, 0.0)
        self.assertFalse(rep_empty.passed)
        self.assertEqual(rep_empty.grade_letter, "F")

        # Passing submission matching rubric
        passing_submission = {
            "devices": {
                "R1": {
                    "ip_g0_0": "192.168.1.1/24",
                    "ospf_area": "0.0.0.0",
                    "firewall_default_policy": "drop",
                },
                "R2": {
                    "ip_g0_1": "10.0.0.2/30",
                },
                "SW1": {
                    "vlan_access": 10,
                },
            }
        }
        rep_pass = self.grader.grade("ccna_enterprise_routing", passing_submission)
        self.assertEqual(rep_pass.percentage, 100.0)
        self.assertTrue(rep_pass.passed)
        self.assertEqual(rep_pass.grade_letter, "A")

    def test_lab1_interface_level_gateway_support(self):
        topo = {
            "devices": {
                "h1": {
                    "type": "host",
                    "config": {
                        "interfaces": {
                            "eth0": {"ip": "192.168.1.50/24", "gateway": "192.168.1.1"}
                        }
                    },
                },
                "r1": {
                    "type": "router",
                    "config": {
                        "interfaces": {
                            "eth0": {"ip": "192.168.1.1/24"},
                            "eth1": {"ip": "10.0.0.1/30"},
                        }
                    },
                },
            }
        }
        rep = self.grader.grade("lab1", topo)
        gw_check = next(c for c in rep.checklist if c["item"] == "Default Gateway Configuration")
        self.assertEqual(gw_check["status"], "PASS")
        self.assertEqual(gw_check["awarded_points"], 20.0)

    def test_lab4_firewall_rule_dst_port_handling(self):
        topo = {
            "devices": {
                "r1": {
                    "type": "router",
                    "config": {
                        "firewall": {
                            "default_policy": "drop",
                            "rules": [
                                {"action": "drop", "dst_port": "23", "proto": "tcp"},
                                {"action": "permit", "dst_port": "22", "proto": "tcp"},
                                {"action": "permit", "dst_port": "80", "proto": "tcp"},
                                {"action": "accept", "state": "established"},
                            ],
                        }
                    },
                }
            }
        }
        rep = self.grader.grade("lab4", topo)
        self.assertEqual(rep.percentage, 100.0)
        self.assertTrue(rep.passed)


class TestAuditFastAPIEndpoints(unittest.TestCase):
    """End-to-end audit of all REST endpoints."""

    @classmethod
    def setUpClass(cls):
        app = create_app()
        cls.client = TestClient(app)

    def test_overlay_sdwan_endpoints(self):
        r1 = self.client.get("/api/overlay/sdwan/tunnels")
        self.assertEqual(r1.status_code, 200)

        r2 = self.client.post("/api/overlay/sdwan/steer", json={"app_class": "video"})
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["app_class"], "video")

        r3 = self.client.post("/api/overlay/sdwan/impair", json={"tunnel_id": "lte_backup", "add_latency_ms": 10.0})
        self.assertEqual(r3.status_code, 200)

    def test_twin_endpoints(self):
        r1 = self.client.get("/api/twin/topology")
        self.assertEqual(r1.status_code, 200)
        self.assertIn("node_count", r1.json())

        r2 = self.client.get("/api/twin/what-if/worst-case")
        self.assertEqual(r2.status_code, 200)
        self.assertGreater(len(r2.json()["rankings"]), 0)

    def test_education_endpoints(self):
        r1 = self.client.get("/api/education/labs")
        self.assertEqual(r1.status_code, 200)

        r2 = self.client.get("/api/education/rubrics/ccna_enterprise_routing")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["lab_id"], "ccna_enterprise_routing")

        r3 = self.client.post("/api/education/grade", json={"lab_id": "ccna_enterprise_routing", "submission": {}})
        self.assertEqual(r3.status_code, 200)
        self.assertFalse(r3.json()["report"]["passed"])


if __name__ == "__main__":
    unittest.main()
