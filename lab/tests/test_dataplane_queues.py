"""Integration and unit test suite for Faza 5:
FRRouting data-plane, Linux FIB route verification, and Queue telemetry/bufferbloat tracking.

Covers:
1. FRR daemon config generation (OSPF, BGP, IS-IS, static routes) and Linux FIB route checks.
2. QueueTracker output parsing across FIFO, RED, CoDel, fq_codel, and TBF qdiscs.
3. FastAPI endpoints for queue telemetry, bufferbloat reports, and data-plane health.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from lab.ospf import frr_config as ospf_frr_config
from lab.server import create_app
from lab.telemetry.manager import TelemetryManager
from lab.telemetry.queue_tracker import (
    BufferbloatAssessment,
    QueueStats,
    QueueTracker,
    parse_rate_bps,
    parse_size_bytes,
    parse_time_ms,
)
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


class TestFRRDataPlaneAndFIB(unittest.TestCase):
    """FRRouting konfiguratsiyasi, daemonlar va Linux FIB marshrutlari tekshiruvi."""

    def test_frr_conf_generation_ospf(self):
        """OSPF protokoli uchun to'liq frr.conf generatsiyasi tekshiruvi."""
        cfg = {
            "daemons": ["zebra", "ospfd"],
            "ospf": {
                "router_id": "10.0.0.1",
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
                    "ospf_cost": 15,
                },
            },
            "routes": [{"prefix": "172.16.0.0/12", "via": "10.0.12.2"}],
        }
        conf = generate_frr_conf("r1", cfg)
        self.assertIn("hostname r1", conf)
        self.assertIn("ip forwarding", conf)
        self.assertIn("ipv6 forwarding", conf)
        self.assertIn("service integrated-vtysh-config", conf)
        self.assertIn("router ospf", conf)
        self.assertIn("ospf router-id 10.0.0.1", conf)
        self.assertIn("network 10.0.1.0/24 area 0", conf)
        self.assertIn("network 10.0.12.0/30 area 0", conf)
        self.assertIn("redistribute connected", conf)
        self.assertIn("redistribute bgp", conf)
        self.assertIn("passive-interface r1-eth1", conf)
        self.assertIn("interface r1-eth2", conf)
        self.assertIn("ip ospf network point-to-point", conf)
        self.assertIn("ip ospf hello-interval 1", conf)
        self.assertIn("ip ospf dead-interval 4", conf)
        self.assertIn("ip ospf cost 15", conf)
        self.assertIn("ip route 172.16.0.0/12 10.0.12.2", conf)

    def test_frr_conf_generation_bgp(self):
        """BGP peering va eBGP konfiguratsiyasi tekshiruvi."""
        cfg = {
            "daemons": ["zebra", "bgpd"],
            "bgp": {
                "asn": 65001,
                "router_id": "1.1.1.1",
                "neighbors": [
                    {"ip": "10.0.12.2", "remote_as": 65002, "next_hop_self": True}
                ],
                "networks": ["10.10.0.0/16"],
                "redistribute": ["connected", "ospf"],
            },
        }
        conf = generate_frr_conf("r1", cfg)
        self.assertIn("router bgp 65001", conf)
        self.assertIn("bgp router-id 1.1.1.1", conf)
        self.assertIn("no bgp ebgp-requires-policy", conf)
        self.assertIn("neighbor 10.0.12.2 remote-as 65002", conf)
        self.assertIn("address-family ipv4 unicast", conf)
        self.assertIn("network 10.10.0.0/16", conf)
        self.assertIn("redistribute ospf", conf)
        self.assertIn("neighbor 10.0.12.2 activate", conf)
        self.assertIn("neighbor 10.0.12.2 next-hop-self", conf)

    def test_frr_conf_generation_isis(self):
        """IS-IS (Intermediate System to Intermediate System) konfiguratsiyasi tekshiruvi."""
        cfg = {
            "daemons": ["zebra", "isisd"],
            "isis": {
                "tag": "ISP-BACKBONE",
                "net": "49.0001.0000.0000.0001.00",
                "level": "level-2-only",
                "metric_style": "wide",
            },
            "interfaces": {
                "r1-eth1": {
                    "ip": "10.0.1.1/24",
                    "isis_tag": "ISP-BACKBONE",
                    "isis_p2p": True,
                    "isis_metric": 50,
                }
            },
        }
        conf = generate_frr_conf("r1", cfg)
        self.assertIn("router isis ISP-BACKBONE", conf)
        self.assertIn("net 49.0001.0000.0000.0001.00", conf)
        self.assertIn("is-type level-2-only", conf)
        self.assertIn("metric-style wide", conf)
        self.assertIn("interface r1-eth1", conf)
        self.assertIn("ip router isis ISP-BACKBONE", conf)
        self.assertIn("isis network point-to-point", conf)
        self.assertIn("isis metric 50", conf)

    def test_lab_ospf_frr_config_helper(self):
        """lab.ospf.frr_config helper funksiyasi tekshiruvi."""
        dev = {
            "id": 1,
            "type": "router",
            "config": {
                "interfaces": {
                    "r1-eth1": {"ip": "10.0.1.1/24"},
                    "r1-eth2": {"ip": "10.0.12.1/30"},
                },
                "ospf": {
                    "router_id": "1.1.1.1",
                    "passive_interfaces": ["r1-eth1"],
                },
            },
        }
        conf = ospf_frr_config(dev)
        self.assertIn("router ospf", conf)
        self.assertIn("ospf router-id 1.1.1.1", conf)
        self.assertIn("network 10.0.1.0/24 area 0", conf)
        self.assertIn("network 10.0.12.0/30 area 0", conf)
        self.assertIn("passive-interface r1-eth1", conf)
        self.assertIn("interface r1-eth2", conf)
        self.assertIn("ip ospf network point-to-point", conf)

    def test_get_linux_fib_routes_json_and_fallback(self):
        """Linux FIB marshrutlarini JSON va matnli fallback orqali ajratib olish."""
        # JSON format
        mock_node = MagicMock()
        mock_node.cmd.return_value = json.dumps([
            {"dst": "10.0.1.0/24", "dev": "r1-eth1", "protocol": "kernel", "prefsrc": "10.0.1.1"},
            {"dst": "10.0.2.0/24", "gateway": "10.0.12.2", "dev": "r1-eth2", "protocol": "ospf", "metric": 20},
            {"dst": "default", "gateway": "192.168.100.1", "dev": "eth0", "protocol": "zebra"},
        ])
        routes = get_linux_fib_routes(mock_node)
        self.assertEqual(len(routes), 3)
        self.assertEqual(routes[0]["prefix"], "10.0.1.0/24")
        self.assertEqual(routes[1]["prefix"], "10.0.2.0/24")
        self.assertEqual(routes[1]["gateway"], "10.0.12.2")
        self.assertEqual(routes[1]["proto"], "ospf")
        self.assertEqual(routes[1]["metric"], 20)
        self.assertEqual(routes[2]["prefix"], "default")

        # Text fallback
        mock_node_text = MagicMock()
        mock_node_text.cmd.side_effect = [
            "invalid json",
            "10.0.1.0/24 dev r1-eth1 proto kernel scope link src 10.0.1.1\n"
            "10.0.2.0/24 via 10.0.12.2 dev r1-eth2 proto ospf metric 20\n"
            "default via 192.168.100.1 dev eth0 proto zebra\n",
        ]
        routes_text = get_linux_fib_routes(mock_node_text)
        self.assertEqual(len(routes_text), 3)
        self.assertEqual(routes_text[1]["prefix"], "10.0.2.0/24")
        self.assertEqual(routes_text[1]["gateway"], "10.0.12.2")
        self.assertEqual(routes_text[1]["dev"], "r1-eth2")
        self.assertEqual(routes_text[1]["proto"], "ospf")

    def test_verify_fib_route_matching_and_negative(self):
        """verify_fib_route funksiyasining mos kelish va xatolik holatlari."""
        mock_node = MagicMock()
        mock_node.cmd.return_value = json.dumps([
            {"dst": "10.0.2.0/24", "gateway": "10.0.12.2", "dev": "r1-eth2", "protocol": "ospf"},
            {"dst": "default", "gateway": "10.0.0.1", "dev": "eth0", "protocol": "zebra"},
        ])

        # To'g'ri marshrut
        ok, r, _ = verify_fib_route(mock_node, "10.0.2.0/24", expected_via="10.0.12.2", expected_proto="ospf")
        self.assertTrue(ok)
        self.assertEqual(r["dev"], "r1-eth2")

        # Default route alias (0.0.0.0/0 matches default)
        ok_def, _, _ = verify_fib_route(mock_node, "0.0.0.0/0", expected_via="10.0.0.1")
        self.assertTrue(ok_def)

        # Noto'g'ri gateway
        ok_bad_gw, _, _ = verify_fib_route(mock_node, "10.0.2.0/24", expected_via="10.0.99.1")
        self.assertFalse(ok_bad_gw)

        # Noto'g'ri interfeys
        ok_bad_dev, _, _ = verify_fib_route(mock_node, "10.0.2.0/24", expected_dev="r1-eth9")
        self.assertFalse(ok_bad_dev)

        # Mavjud bo'lmagan prefiks
        ok_missing, _, _ = verify_fib_route(mock_node, "10.99.0.0/16")
        self.assertFalse(ok_missing)

    def test_verify_all_fib_routes(self):
        """Bir nechta routerlar bo'yicha FIB tekshiruvi (verify_all_fib_routes)."""
        mock_net = MagicMock()
        r1 = MagicMock()
        r1.cmd.return_value = json.dumps([
            {"dst": "10.0.2.0/24", "gateway": "10.0.12.2", "dev": "r1-eth2", "protocol": "ospf"},
            {"dst": "10.0.3.0/24", "gateway": "10.0.13.2", "dev": "r1-eth3", "protocol": "ospf"},
        ])
        r2 = MagicMock()
        r2.cmd.return_value = json.dumps([
            {"dst": "10.0.1.0/24", "gateway": "10.0.12.1", "dev": "r2-eth1", "protocol": "ospf"},
        ])
        mock_net.get.side_effect = lambda name: r1 if name == "r1" else (r2 if name == "r2" else None)

        spec = {
            "r1": ["10.0.2.0/24", "10.0.3.0/24"],
            "r2": {"10.0.1.0/24": {"via": "10.0.12.1", "proto": "ospf"}},
        }
        res = verify_all_fib_routes(mock_net, spec)
        self.assertTrue(res["all_passed"])
        self.assertTrue(res["details"]["r1"]["passed"])
        self.assertTrue(res["details"]["r2"]["passed"])

        # Missing node case
        spec_fail = {"r99": ["10.0.1.0/24"]}
        res_fail = verify_all_fib_routes(mock_net, spec_fail)
        self.assertFalse(res_fail["all_passed"])
        self.assertFalse(res_fail["details"]["r99"]["passed"])

    def test_convergence_and_neighbors_helpers(self):
        """get_frr_neighbors, wait_for_frr_convergence va measure_routing_convergence tekshiruvi."""
        node = MagicMock()
        node.name = "r1"
        node.cmd.return_value = json.dumps({
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
        nbrs = get_frr_neighbors(node, proto="ospf")
        self.assertEqual(len(nbrs), 1)
        self.assertEqual(nbrs[0]["neighbor_id"], "2.2.2.2")
        self.assertEqual(nbrs[0]["state"], "Full")
        self.assertTrue(nbrs[0]["converged"])

        res = wait_for_frr_convergence([node], proto="ospf", timeout=1.0, interval=0.01)
        self.assertTrue(res["converged"])
        self.assertIn("r1", res["details"])

        # Convergence measurement
        flag = [False]
        dur = measure_routing_convergence(
            trigger_fn=lambda: flag.__setitem__(0, True),
            verify_fn=lambda: flag[0],
            timeout=1.0,
            interval=0.01,
        )
        self.assertIsNotNone(dur)
        self.assertGreaterEqual(dur, 0.0)


class TestQueueTrackerOutputParsing(unittest.TestCase):
    """QueueTracker: FIFO, RED, CoDel, fq_codel, TBF qdisclari va Bufferbloat tahlili."""

    def setUp(self):
        self.tracker = QueueTracker()

    def test_unit_parsing(self):
        """Tezlik (bps), hajm (bayt/paket) va vaqt (ms) o'lchov birliklari tahlili."""
        self.assertEqual(parse_rate_bps("10Mbit"), 10_000_000.0)
        self.assertEqual(parse_rate_bps("100Kbit"), 100_000.0)
        self.assertEqual(parse_rate_bps("1Gbit"), 1_000_000_000.0)
        self.assertEqual(parse_rate_bps("10Mbps"), 80_000_000.0)

        self.assertEqual(parse_size_bytes("1500b"), 1500)
        self.assertEqual(parse_size_bytes("60Kb"), 61440)
        self.assertEqual(parse_size_bytes("32Mb"), 32 * 1024 * 1024)
        self.assertEqual(parse_size_bytes("1000p"), 1000)

        self.assertEqual(parse_time_ms("5ms"), 5.0)
        self.assertEqual(parse_time_ms("100us"), 0.1)
        self.assertEqual(parse_time_ms("1.5s"), 1500.0)

    def test_fifo_qdisc_parsing(self):
        """pfifo va bfifo qdisc statistikasini to'g'ri o'qish va tahlil qilish."""
        # pfifo
        pfifo_out = """
qdisc pfifo 1: dev r1-eth1 root refcnt 2 limit 1000p
 Sent 2097152 bytes 2048 pkt (dropped 25, overlimits 0 requeues 0) 
 backlog 29200b 20p requeues 0
        """
        stats_p = self.tracker.parse_tc_qdisc_output(pfifo_out, dev="r1-eth1")
        self.assertEqual(len(stats_p), 1)
        qp = stats_p[0]
        self.assertEqual(qp.kind, "pfifo")
        self.assertEqual(qp.handle, "1:")
        self.assertEqual(qp.limit, 1000)
        self.assertEqual(qp.limit_unit, "p")
        self.assertEqual(qp.bytes_sent, 2097152)
        self.assertEqual(qp.packets_sent, 2048)
        self.assertEqual(qp.dropped, 25)
        self.assertEqual(qp.backlog_bytes, 29200)
        self.assertEqual(qp.backlog_packets, 20)
        self.assertAlmostEqual(qp.occupancy_pct, 2.0)  # 20 / 1000 * 100%

        # bfifo
        bfifo_out = """
qdisc bfifo 1: dev r1-eth2 root refcnt 2 limit 100000b
 Sent 1048576 bytes 1024 pkt (dropped 5, overlimits 0 requeues 0) 
 backlog 25000b 17p requeues 0
        """
        stats_b = self.tracker.parse_tc_qdisc_output(bfifo_out, dev="r1-eth2")
        self.assertEqual(len(stats_b), 1)
        qb = stats_b[0]
        self.assertEqual(qb.kind, "bfifo")
        self.assertEqual(qb.limit, 100000)
        self.assertEqual(qb.limit_unit, "b")
        self.assertEqual(qb.backlog_bytes, 25000)
        self.assertAlmostEqual(qb.occupancy_pct, 25.0)  # 25000 / 100000 * 100%

    def test_red_qdisc_parsing(self):
        """RED (Random Early Detection) qdisc va uning ECN/early drop statistikasini tekshirish."""
        red_out = """
qdisc red 10: dev r1-eth1 root refcnt 2 limit 60Kb min 15Kb max 45Kb avpkt 1000 burst 20 ecn probability 0.05
 Sent 10485760 bytes 7000 pkt (dropped 80, overlimits 250 requeues 0) 
 backlog 30000b 20p requeues 0
  marked 35 early 60 pdrop 20 other 0
        """
        stats_red = self.tracker.parse_tc_qdisc_output(red_out, dev="r1-eth1")
        self.assertEqual(len(stats_red), 1)
        qr = stats_red[0]
        self.assertEqual(qr.kind, "red")
        self.assertEqual(qr.handle, "10:")
        self.assertTrue(qr.ecn)
        self.assertEqual(qr.params.get("min_bytes"), 15 * 1024)
        self.assertEqual(qr.params.get("max_bytes"), 45 * 1024)
        self.assertAlmostEqual(qr.params.get("probability"), 0.05)
        self.assertEqual(qr.dropped, 80)
        self.assertEqual(qr.overlimits, 250)
        self.assertEqual(qr.ecn_marks, 35)
        self.assertEqual(qr.early_drops, 60)
        self.assertEqual(qr.extra_stats.get("pdrop"), 20)

    def test_codel_and_fq_codel_parsing(self):
        """CoDel va fq_codel qdisc parametrlarini (ldelay, target, interval, ecn) tekshirish."""
        codel_out = """
qdisc codel 1: dev r1-eth1 root refcnt 2 limit 1000p target 5.0ms interval 100.0ms ecn
 Sent 3145728 bytes 2200 pkt (dropped 18, overlimits 0 requeues 0) 
 backlog 6000b 4p requeues 0
  count 4 lastcount 2 ldelay 3.8ms dropping drop_next 45ms
  maxpacket 1500 ecn_mark 12 drop_overlimit 0
        """
        stats_codel = self.tracker.parse_tc_qdisc_output(codel_out, dev="r1-eth1")
        self.assertEqual(len(stats_codel), 1)
        qc = stats_codel[0]
        self.assertEqual(qc.kind, "codel")
        self.assertEqual(qc.target_ms, 5.0)
        self.assertEqual(qc.interval_ms, 100.0)
        self.assertTrue(qc.ecn)
        self.assertTrue(qc.dropping)
        self.assertAlmostEqual(qc.ldelay_ms, 3.8)
        self.assertAlmostEqual(qc.estimated_queue_delay_ms, 3.8)
        self.assertEqual(qc.ecn_marks, 12)
        self.assertEqual(qc.extra_stats.get("count"), 4)

        # fq_codel
        fq_out = """
qdisc fq_codel 8001: dev r1-eth2 root refcnt 2 limit 10240p flows 1024 quantum 1514 target 5ms interval 100ms memory_limit 32Mb ecn
 Sent 45000000 bytes 310000 pkt (dropped 15, overlimits 0 requeues 0) 
 backlog 1500b 1p requeues 0
  maxpacket 1500 drop_overlimit 0 new_flow_count 520 ecn_mark 6
  new_flows_len 1 old_flows_len 3
        """
        stats_fq = self.tracker.parse_tc_qdisc_output(fq_out, dev="r1-eth2")
        self.assertEqual(len(stats_fq), 1)
        qf = stats_fq[0]
        self.assertEqual(qf.kind, "fq_codel")
        self.assertEqual(qf.params.get("flows"), 1024)
        self.assertEqual(qf.params.get("quantum"), 1514)
        self.assertEqual(qf.target_ms, 5.0)
        self.assertEqual(qf.ecn_marks, 6)

    def test_bufferbloat_assessment(self):
        """Bufferbloat aniqlanishi, og'irlik darajasi (severity) va tavsiyalar tekshiruvi."""
        # 1. Og'ir bufferbloat (katta navbat va uzoq kechikish bilan FIFO)
        bloated = self.tracker.simulate_sample(
            dev="eth-bottleneck",
            kind="pfifo",
            backlog_packets=85,
            backlog_bytes=124100,
            limit=100,
            rate_bps=10_000_000.0,
            dropped=15,
        )
        assessment = self.tracker.assess_bufferbloat(bloated)
        self.assertTrue(assessment.detected)
        self.assertIn(assessment.severity, ("high", "critical"))
        self.assertGreater(assessment.score, 60.0)
        self.assertGreater(assessment.queue_delay_ms, 50.0)
        self.assertIn("fq_codel yoki CoDel", assessment.recommendation)

        # 2. Optimal navbat (AQM orqali kechikish nazorat qilingan)
        healthy = self.tracker.simulate_sample(
            dev="eth-healthy",
            kind="fq_codel",
            backlog_packets=2,
            backlog_bytes=3000,
            limit=10240,
            ldelay_ms=3.2,
            dropping=False,
        )
        assessment_ok = self.tracker.assess_bufferbloat(healthy)
        self.assertFalse(assessment_ok.detected)
        self.assertEqual(assessment_ok.severity, "normal")
        self.assertLess(assessment_ok.score, 25.0)


class TestQueuesAndDataPlaneAPI(unittest.TestCase):
    """FastAPI orqali navbat telemetriyasi va data-plane health API endpointlari tekshiruvi."""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app("demo")
        cls.client = TestClient(cls.app)

    def test_dataplane_health_endpoint(self):
        """GET /api/dataplane/health va /api/net/dataplane/health endpointlari tekshiruvi."""
        for path in ("/api/dataplane/health", "/api/net/dataplane/health"):
            with self.subTest(path=path):
                res = self.client.get(path)
                self.assertEqual(res.status_code, 200)
                data = res.json()
                self.assertIn("status", data)
                self.assertIn("healthy", data)
                self.assertIn("driver", data)
                self.assertIn("queues", data)
                self.assertIn("telemetry", data)
                self.assertTrue(data["healthy"])

    def test_net_status_endpoint(self):
        """GET /api/net/status orqali tarmoq driver holatini tekshirish."""
        res = self.client.get("/api/net/status")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("driver", data)
        self.assertIn("running", data)
        self.assertIn("capture", data)

    def test_telemetry_l4_queues_and_bufferbloat(self):
        """GET /api/telemetry/l4/queues va bufferbloat hisobotlari tekshiruvi."""
        # 1. Simulyatsiya orqali navbat telemetriyasini to'ldirish
        sim_res = self.client.post("/api/telemetry/simulate", json={"steps": 3})
        self.assertEqual(sim_res.status_code, 200)

        # 2. Barcha navbatlar ro'yxati
        res = self.client.get("/api/telemetry/l4/queues")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("queues", data)
        self.assertIn("bufferbloat", data)
        self.assertIn("summary", data)
        self.assertGreater(len(data["queues"]), 0)

        # 3. Muayyan dev bo'yicha filter
        first_dev = data["queues"][0]["dev"]
        res_dev = self.client.get(f"/api/telemetry/l4/queues?dev={first_dev}")
        self.assertEqual(res_dev.status_code, 200)
        dev_queues = res_dev.json()["queues"]
        self.assertTrue(all(q["dev"] == first_dev for q in dev_queues))

        # 4. Muayyan dev batafsil tarixi (/api/telemetry/l4/queues/{dev})
        res_detail = self.client.get(f"/api/telemetry/l4/queues/{first_dev}")
        self.assertEqual(res_detail.status_code, 200)
        det = res_detail.json()
        self.assertEqual(det["dev"], first_dev)
        self.assertIn("latest", det)
        self.assertIn("history", det)

        # 5. Bufferbloat hisoboti (/api/telemetry/l4/bufferbloat)
        res_bloat = self.client.get("/api/telemetry/l4/bufferbloat")
        self.assertEqual(res_bloat.status_code, 200)
        bloat_data = res_bloat.json()
        self.assertIn("summary", bloat_data)
        self.assertIn("assessments", bloat_data)

    def test_telemetry_l4_stats_includes_queues(self):
        """GET /api/telemetry/l4/stats telemetriyasida navbat ko'rsatkichlari borligini tekshirish."""
        res = self.client.get("/api/telemetry/l4/stats")
        self.assertEqual(res.status_code, 200)
        stats = res.json()
        self.assertIn("total_queues_count", stats)
        self.assertIn("bufferbloat_detected_count", stats)
        self.assertIn("recent_drops_count", stats)


if __name__ == "__main__":
    unittest.main()
