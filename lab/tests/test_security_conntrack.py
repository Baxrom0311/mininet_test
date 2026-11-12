"""Unit testlar: Conntrack dvigateli, Stateful Firewall, Hujumlar simulyatori va REST API.

Ushbu test to'plami quyidagilarni to'liq tekshiradi:
- TCP conntrack holatlari mashinasi (SYN_SENT -> SYN_RECV -> ESTABLISHED -> FIN_WAIT -> TIME_WAIT -> CLOSE)
- Qat'iy TCP (Strict TCP) va INVALID holatlar
- UDP va ICMP tracking (UNREPLIED, ASSURED, REPLIED, RELATED)
- NAT tarjimalari (SNAT, DNAT) va Original / Reply tuple'lar
- Conntrack muddati tugashi (expiration) va tozalash
- Linux /proc/net/nf_conntrack parseri va Synthetic generator
- Stateful Firewall qoidalarini conntrack holati bo'yicha baholash
- 4 ta asosiy hujum simulyatori (SYN Flood, UDP Reflection, Slowloris, ARP Spoofing)
- SecurityAlertManager va IDS ogohlantirishlari
- FastAPI serverining 4 ta REST API endpointi (/api/security/*)
"""

import time
import unittest
from fastapi.testclient import TestClient

from lab.security.conntrack_engine import (
    ConntrackEngine,
    ConnectionTuple,
    NATMapping,
    FW_STATE_NEW,
    FW_STATE_ESTABLISHED,
    FW_STATE_RELATED,
    FW_STATE_INVALID,
    TCP_STATE_SYN_SENT,
    TCP_STATE_SYN_RECV,
    TCP_STATE_ESTABLISHED,
    TCP_STATE_FIN_WAIT,
    TCP_STATE_CLOSE_WAIT,
    TCP_STATE_LAST_ACK,
    TCP_STATE_TIME_WAIT,
    TCP_STATE_CLOSE,
)
from lab.security.attack_simulator import (
    AttackSimulator,
    SecurityAlertManager,
    SecurityAlert,
)
from lab.server import create_app


class ConntrackEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = ConntrackEngine()

    def test_tcp_full_handshake_and_teardown(self):
        """TCP to'liq 3 tomonlama handshake va yopilish ketma-ketligi."""
        now = 1000.0

        # 1. Mijozdan serverga SYN paketi (SYN_SENT, NEW)
        syn_pkt = {
            "proto": "tcp",
            "src_ip": "10.0.1.10",
            "src_port": 49152,
            "dst_ip": "10.0.2.20",
            "dst_port": 80,
            "flags": {"syn": True, "ack": False},
            "length": 60,
        }
        ok, entry, fw_st = self.engine.process_packet(syn_pkt, now=now)
        self.assertTrue(ok)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.tcp_state, TCP_STATE_SYN_SENT)
        self.assertEqual(fw_st, FW_STATE_NEW)
        self.assertEqual(entry.packets_orig, 1)

        # Reply tuple to'g'ri o'rnatilganini tekshirish
        self.assertEqual(entry.reply_tuple.src_ip, "10.0.2.20")
        self.assertEqual(entry.reply_tuple.dst_ip, "10.0.1.10")
        self.assertEqual(entry.reply_tuple.src_port, 80)
        self.assertEqual(entry.reply_tuple.dst_port, 49152)

        # 2. Serverdan mijozga SYN+ACK paketi (SYN_RECV, ESTABLISHED)
        syn_ack_pkt = {
            "proto": "tcp",
            "src_ip": "10.0.2.20",
            "src_port": 80,
            "dst_ip": "10.0.1.10",
            "dst_port": 49152,
            "flags": {"syn": True, "ack": True},
            "length": 60,
        }
        ok, entry, fw_st = self.engine.process_packet(syn_ack_pkt, now=now + 0.05)
        self.assertTrue(ok)
        self.assertEqual(entry.tcp_state, TCP_STATE_SYN_RECV)
        self.assertEqual(fw_st, FW_STATE_ESTABLISHED)
        self.assertTrue(entry.seen_reply)
        self.assertEqual(entry.packets_reply, 1)

        # 3. Mijozdan serverga ACK paketi (ESTABLISHED, ASSURED)
        ack_pkt = {
            "proto": "tcp",
            "src_ip": "10.0.1.10",
            "src_port": 49152,
            "dst_ip": "10.0.2.20",
            "dst_port": 80,
            "flags": {"syn": False, "ack": True},
            "length": 52,
        }
        ok, entry, fw_st = self.engine.process_packet(ack_pkt, now=now + 0.1)
        self.assertTrue(ok)
        self.assertEqual(entry.tcp_state, TCP_STATE_ESTABLISHED)
        self.assertEqual(fw_st, FW_STATE_ESTABLISHED)
        self.assertTrue(entry.is_assured)

        # 4. Trafik almashinuvi
        data_pkt = {
            "proto": "tcp",
            "src_ip": "10.0.1.10",
            "src_port": 49152,
            "dst_ip": "10.0.2.20",
            "dst_port": 80,
            "flags": {"psh": True, "ack": True},
            "length": 1024,
        }
        ok, entry, fw_st = self.engine.process_packet(data_pkt, now=now + 0.5)
        self.assertEqual(entry.tcp_state, TCP_STATE_ESTABLISHED)
        self.assertEqual(entry.bytes_orig, 60 + 52 + 1024)

        # 5. Mijoz tomonidan FIN (FIN_WAIT)
        fin_pkt = {
            "proto": "tcp",
            "src_ip": "10.0.1.10",
            "src_port": 49152,
            "dst_ip": "10.0.2.20",
            "dst_port": 80,
            "flags": {"fin": True, "ack": True},
            "length": 52,
        }
        ok, entry, fw_st = self.engine.process_packet(fin_pkt, now=now + 1.0)
        self.assertEqual(entry.tcp_state, TCP_STATE_FIN_WAIT)

        # 6. Server tomonidan FIN (TIME_WAIT)
        srv_fin = {
            "proto": "tcp",
            "src_ip": "10.0.2.20",
            "src_port": 80,
            "dst_ip": "10.0.1.10",
            "dst_port": 49152,
            "flags": {"fin": True, "ack": True},
            "length": 52,
        }
        ok, entry, fw_st = self.engine.process_packet(srv_fin, now=now + 1.1)
        self.assertEqual(entry.tcp_state, TCP_STATE_TIME_WAIT)

    def test_strict_tcp_invalid_initial_packets(self):
        """Strict rejimda birinchi paket SYN bo'lmasa INVALID qaytarish."""
        # Unsolicited ACK paketi yangi ulanish boshlay olmaydi
        stray_ack = {
            "proto": "tcp",
            "src_ip": "1.2.3.4",
            "src_port": 12345,
            "dst_ip": "5.6.7.8",
            "dst_port": 80,
            "flags": {"syn": False, "ack": True},
        }
        ok, entry, fw_st = self.engine.process_packet(stray_ack)
        self.assertFalse(ok)
        self.assertIsNone(entry)
        self.assertEqual(fw_st, FW_STATE_INVALID)

        # Unsolicited FIN paketi
        stray_fin = {
            "proto": "tcp",
            "src_ip": "1.2.3.4",
            "src_port": 12345,
            "dst_ip": "5.6.7.8",
            "dst_port": 80,
            "flags": {"fin": True},
        }
        ok, entry, fw_st = self.engine.process_packet(stray_fin)
        self.assertFalse(ok)
        self.assertEqual(fw_st, FW_STATE_INVALID)

    def test_tcp_rst_closes_connection(self):
        """RST paketi ulanishni darhol CLOSE holatiga o'tkazishi."""
        now = 1000.0
        syn_pkt = {
            "proto": "tcp",
            "src_ip": "10.0.0.1",
            "src_port": 1000,
            "dst_ip": "10.0.0.2",
            "dst_port": 80,
            "flags": {"syn": True},
        }
        self.engine.process_packet(syn_pkt, now=now)

        rst_pkt = {
            "proto": "tcp",
            "src_ip": "10.0.0.2",
            "src_port": 80,
            "dst_ip": "10.0.0.1",
            "dst_port": 1000,
            "flags": {"rst": True},
        }
        ok, entry, fw_st = self.engine.process_packet(rst_pkt, now=now + 0.1)
        self.assertEqual(entry.tcp_state, TCP_STATE_CLOSE)
        self.assertEqual(entry.timeout_s, 10)  # Qisqa timeout

    def test_udp_tracking_and_assured(self):
        """UDP tracking: birinchi paket UNREPLIED/NEW, javob kelganda ASSURED/ESTABLISHED."""
        now = 1000.0
        # DNS query
        query = {
            "proto": "udp",
            "src_ip": "192.168.1.100",
            "src_port": 53530,
            "dst_ip": "8.8.8.8",
            "dst_port": 53,
            "length": 48,
        }
        ok, entry, fw_st = self.engine.process_packet(query, now=now)
        self.assertTrue(ok)
        self.assertEqual(fw_st, FW_STATE_NEW)
        self.assertIn("UNREPLIED", entry.status)
        self.assertFalse(entry.is_assured)

        # DNS javob
        reply = {
            "proto": "udp",
            "src_ip": "8.8.8.8",
            "src_port": 53,
            "dst_ip": "192.168.1.100",
            "dst_port": 53530,
            "length": 128,
        }
        ok, entry, fw_st = self.engine.process_packet(reply, now=now + 0.03)
        self.assertTrue(ok)
        self.assertEqual(fw_st, FW_STATE_ESTABLISHED)
        self.assertTrue(entry.is_assured)
        self.assertNotIn("UNREPLIED", entry.status)

    def test_icmp_tracking_and_related_errors(self):
        """ICMP echo so'rov/javob va ICMP xatolik paketlarini RELATED sifatida aniqlash."""
        now = 1000.0
        # 1. Echo Request
        ping_req = {
            "proto": "icmp",
            "src_ip": "10.0.0.1",
            "dst_ip": "10.0.0.2",
            "icmp_type": 8,
            "icmp_code": 0,
            "icmp_id": 42,
        }
        ok, entry, fw_st = self.engine.process_packet(ping_req, now=now)
        self.assertEqual(fw_st, FW_STATE_NEW)
        self.assertIn("UNREPLIED", entry.status)

        # 2. Echo Reply
        ping_rep = {
            "proto": "icmp",
            "src_ip": "10.0.0.2",
            "dst_ip": "10.0.0.1",
            "icmp_type": 0,
            "icmp_code": 0,
            "icmp_id": 42,
        }
        ok, entry, fw_st = self.engine.process_packet(ping_rep, now=now + 0.01)
        self.assertEqual(fw_st, FW_STATE_ESTABLISHED)
        self.assertIn("REPLIED", entry.status)

        # 3. UDP oqimini ochish va unga ICMP Destination Unreachable kelganda RELATED bo'lishi
        udp_pkt = {
            "proto": "udp",
            "src_ip": "10.0.0.5",
            "src_port": 40000,
            "dst_ip": "10.0.0.6",
            "dst_port": 9999,
        }
        self.engine.process_packet(udp_pkt, now=now + 1.0)

        icmp_unreach = {
            "proto": "icmp",
            "src_ip": "10.0.0.6",
            "dst_ip": "10.0.0.5",
            "icmp_type": 3,
            "icmp_code": 3,  # Port Unreachable
            "inner_pkt": udp_pkt,
        }
        ok, entry, fw_st = self.engine.process_packet(icmp_unreach, now=now + 1.02)
        self.assertTrue(ok)
        self.assertEqual(fw_st, FW_STATE_RELATED)

    def test_nat_mapping_snat_and_dnat(self):
        """SNAT va DNAT tarjimalari reply tuple'da to'g'ri aks etishi."""
        # SNAT: Ichki mijoz 192.168.1.50 -> Internet 203.0.113.1
        snat_pkt = {
            "proto": "tcp",
            "src_ip": "192.168.1.50",
            "src_port": 34567,
            "dst_ip": "93.184.216.34",
            "dst_port": 80,
            "flags": {"syn": True},
            "nat_action": {
                "type": "snat",
                "to_src": "203.0.113.1",
                "to_sport": 40001,
            },
        }
        ok, entry, fw_st = self.engine.process_packet(snat_pkt)
        self.assertTrue(ok)
        self.assertIsNotNone(entry.nat)
        self.assertEqual(entry.nat.nat_type, "snat")
        self.assertEqual(entry.reply_tuple.dst_ip, "203.0.113.1")
        self.assertEqual(entry.reply_tuple.dst_port, 40001)

        # DNAT: Tashqi so'rov 203.0.113.1:8080 -> Ichki server 10.0.0.10:80
        dnat_pkt = {
            "proto": "tcp",
            "src_ip": "5.5.5.5",
            "src_port": 12345,
            "dst_ip": "203.0.113.1",
            "dst_port": 8080,
            "flags": {"syn": True},
            "nat_action": {
                "type": "dnat",
                "to_dst": "10.0.0.10",
                "to_dport": 80,
            },
        }
        ok2, entry2, fw_st2 = self.engine.process_packet(dnat_pkt)
        self.assertTrue(ok2)
        self.assertEqual(entry2.nat.nat_type, "dnat")
        self.assertEqual(entry2.reply_tuple.src_ip, "10.0.0.10")
        self.assertEqual(entry2.reply_tuple.src_port, 80)

    def test_expire_connections(self):
        """Timeout tugagan ulanishlar avtomatik o'chirilishi."""
        now = 1000.0
        pkt = {
            "proto": "udp",
            "src_ip": "1.1.1.1",
            "src_port": 1000,
            "dst_ip": "2.2.2.2",
            "dst_port": 2000,
        }
        self.engine.process_packet(pkt, now=now)
        self.assertEqual(self.engine.count, 1)

        # 10 soniyadan so'ng (timeout 30s) hali o'chmasligi kerak
        deleted = self.engine.expire_connections(now=now + 10.0)
        self.assertEqual(deleted, 0)
        self.assertEqual(self.engine.count, 1)

        # 35 soniyadan so'ng o'chirilishi kerak
        deleted = self.engine.expire_connections(now=now + 35.0)
        self.assertEqual(deleted, 1)
        self.assertEqual(self.engine.count, 0)

    def test_stateful_firewall_evaluation(self):
        """Stateful firewall qoidalari (NEW, ESTABLISHED, RELATED) bo'yicha baholash."""
        rules = [
            # 1. ESTABLISHED,RELATED paketlarga doim ruxsat berish
            {"chain": "forward", "action": "accept", "state": "established"},
            # 2. Yangi ulanishlardan faqat HTTP 80 ga ruxsat berish
            {"chain": "forward", "action": "accept", "proto": "tcp", "dport": 80, "state": "new"},
            # 3. Qolgan hamma yangi ulanishlarni bloklash
            {"chain": "forward", "action": "drop", "state": "new"},
        ]

        # A) HTTP 80 ga yangi SYN paketi -> Qabul qilinishi kerak (accept)
        http_syn = {
            "proto": "tcp",
            "src_ip": "10.0.1.5",
            "src_port": 33333,
            "dst_ip": "10.0.2.10",
            "dst_port": 80,
            "flags": {"syn": True},
        }
        res1 = self.engine.evaluate_stateful_firewall(http_syn, rules, default_policy="drop")
        self.assertEqual(res1["action"], "accept")
        self.assertEqual(res1["fw_state"], FW_STATE_NEW)
        self.assertEqual(res1["matched_index"], 1)

        # B) HTTP 80 ga serverning javobi (SYN+ACK) -> ESTABLISHED qoidasi mos keladi
        http_syn_ack = {
            "proto": "tcp",
            "src_ip": "10.0.2.10",
            "src_port": 80,
            "dst_ip": "10.0.1.5",
            "dst_port": 33333,
            "flags": {"syn": True, "ack": True},
        }
        res2 = self.engine.evaluate_stateful_firewall(http_syn_ack, rules, default_policy="drop")
        self.assertEqual(res2["action"], "accept")
        self.assertEqual(res2["fw_state"], FW_STATE_ESTABLISHED)
        self.assertEqual(res2["matched_index"], 0)

        # C) Telnet 23 portiga yangi SYN paketi -> Bloklanishi kerak (drop)
        telnet_syn = {
            "proto": "tcp",
            "src_ip": "10.0.1.5",
            "src_port": 33334,
            "dst_ip": "10.0.2.10",
            "dst_port": 23,
            "flags": {"syn": True},
        }
        res3 = self.engine.evaluate_stateful_firewall(telnet_syn, rules, default_policy="drop")
        self.assertEqual(res3["action"], "drop")
        self.assertEqual(res3["matched_index"], 2)

    def test_parse_proc_conntrack(self):
        """Linux /proc/net/nf_conntrack parseri."""
        raw_conntrack = """
ipv4     2 tcp      6 431999 ESTABLISHED src=192.168.1.10 dst=93.184.216.34 sport=49152 dport=80 src=93.184.216.34 dst=192.168.1.10 sport=80 dport=49152 [ASSURED] mark=0 use=2
ipv4     2 udp      17 29 src=192.168.1.10 dst=8.8.8.8 sport=5353 dport=53 src=8.8.8.8 dst=192.168.1.10 sport=53 dport=5353 [ASSURED] mark=0 use=2
        """
        entries = ConntrackEngine.parse_proc_conntrack(raw_conntrack)
        self.assertEqual(len(entries), 2)
        e1, e2 = entries[0], entries[1]

        self.assertEqual(e1.proto, "tcp")
        self.assertEqual(e1.tcp_state, "ESTABLISHED")
        self.assertEqual(e1.orig_tuple.src_ip, "192.168.1.10")
        self.assertEqual(e1.orig_tuple.dst_port, 80)
        self.assertTrue(e1.is_assured)

        self.assertEqual(e2.proto, "udp")
        self.assertEqual(e2.orig_tuple.dst_ip, "8.8.8.8")
        self.assertEqual(e2.orig_tuple.dst_port, 53)

    def test_generate_synthetic_entries(self):
        """Sintetik generator turli xil ulanishlar yaratishi."""
        entries = self.engine.generate_synthetic_entries(count=10)
        self.assertGreaterEqual(len(entries), 8)
        stats = self.engine.get_summary_stats()
        self.assertGreater(stats["total_connections"], 0)


class AttackSimulatorTests(unittest.TestCase):
    def setUp(self):
        self.alert_mgr = SecurityAlertManager()
        self.sim = AttackSimulator(alert_manager=self.alert_mgr)

    def test_syn_flood_vulnerable_and_cookies(self):
        """SYN Flood: SYN Cookies yo'qligida backlog to'lishi va SYN Cookies yoqilganda himoyalanish."""
        # 1. Himoyasiz (syn_cookies_enabled=False)
        res_vuln = self.sim.simulate_syn_flood(
            target_ip="10.0.2.10",
            target_port=80,
            pps=10000,
            duration_s=5.0,
            syn_cookies_enabled=False,
            backlog_size=1024,
        )
        self.assertTrue(res_vuln["backlog_exhausted"])
        self.assertGreater(res_vuln["dropped_syns"], 40000)
        self.assertGreater(res_vuln["cpu_impact_pct"], 80.0)
        self.assertEqual(res_vuln["server_availability_pct"], 0.0)
        self.assertFalse(res_vuln["mitigation_stats"]["active"])
        self.assertEqual(res_vuln["alert_generated"]["severity"], "critical")

        # 2. Himoyalangan (syn_cookies_enabled=True)
        res_prot = self.sim.simulate_syn_flood(
            target_ip="10.0.2.10",
            target_port=80,
            pps=10000,
            duration_s=5.0,
            syn_cookies_enabled=True,
            backlog_size=1024,
        )
        self.assertFalse(res_prot["backlog_exhausted"])
        self.assertEqual(res_prot["dropped_syns"], 0)
        self.assertGreaterEqual(res_prot["server_availability_pct"], 95.0)
        self.assertTrue(res_prot["mitigation_stats"]["active"])
        self.assertEqual(res_prot["alert_generated"]["severity"], "medium")

    def test_udp_reflection(self):
        """UDP Amplification: 50x-70x (DNS) va 200x (NTP) kuchaytirish."""
        # DNS reflection (60x)
        res_dns = self.sim.simulate_udp_reflection(
            victim_ip="10.0.2.10",
            reflector_type="dns",
            request_pps=1000,
            duration_s=5.0,
            pipe_capacity_mbps=10.0,
        )
        self.assertAlmostEqual(res_dns["amplification_ratio"], 60.0, delta=10.0)
        self.assertGreater(res_dns["inbound_reflected_mbps"], res_dns["pipe_capacity_mbps"])
        self.assertTrue(res_dns["pipe_saturated"])
        self.assertGreater(res_dns["victim_packet_loss_pct"], 50.0)

        # NTP reflection (200x)
        res_ntp = self.sim.simulate_udp_reflection(
            victim_ip="10.0.2.10",
            reflector_type="ntp",
            request_pps=1000,
            duration_s=5.0,
            pipe_capacity_mbps=50.0,
        )
        self.assertEqual(res_ntp["amplification_ratio"], 200.0)
        self.assertTrue(res_ntp["pipe_saturated"])
        self.assertGreater(res_ntp["inbound_reflected_mbps"], 70.0)

    def test_slowloris(self):
        """Slowloris DoS: worker pool to'lishi va mavjudlikning tushishi."""
        # A) Pool to'lganda (connections >= server_max_connections)
        res_full = self.sim.simulate_slowloris(
            target_ip="10.0.2.10",
            port=80,
            connections=200,
            server_max_connections=150,
        )
        self.assertTrue(res_full["pool_exhausted"])
        self.assertEqual(res_full["server_availability_pct"], 0.0)
        self.assertEqual(res_full["available_slots"], 0)
        self.assertEqual(res_full["response_delay_ms"], 30000.0)

        # B) Qisman band bo'lganda
        res_part = self.sim.simulate_slowloris(
            target_ip="10.0.2.10",
            port=80,
            connections=50,
            server_max_connections=150,
        )
        self.assertFalse(res_part["pool_exhausted"])
        self.assertGreater(res_part["server_availability_pct"], 50.0)
        self.assertEqual(res_part["available_slots"], 100)

    def test_arp_spoofing(self):
        """ARP Spoofing va Dynamic ARP Inspection (DAI)."""
        # A) DAI o'chirilgan
        res_no_dai = self.sim.simulate_arp_spoof(
            target_ip="10.0.1.10",
            gateway_ip="10.0.1.1",
            attacker_ip="10.0.1.50",
            duration_s=5.0,
            dai_enabled=False,
        )
        self.assertTrue(res_no_dai["arp_cache_poisoned"])
        self.assertTrue(res_no_dai["mitm_active"])
        self.assertGreater(res_no_dai["intercepted_packets"], 0)
        self.assertFalse(res_no_dai["dai_alert_generated"])

        # B) DAI yoqilgan
        res_dai = self.sim.simulate_arp_spoof(
            target_ip="10.0.1.10",
            gateway_ip="10.0.1.1",
            attacker_ip="10.0.1.50",
            duration_s=5.0,
            dai_enabled=True,
        )
        self.assertFalse(res_dai["arp_cache_poisoned"])
        self.assertFalse(res_dai["mitm_active"])
        self.assertEqual(res_dai["intercepted_packets"], 0)
        self.assertTrue(res_dai["dai_alert_generated"])
        self.assertIn("DHCP Snooping database mismatch", res_dai["dai_alert_message"])

    def test_security_alert_manager(self):
        """SecurityAlertManager filtrlash va statistikani boshqarish."""
        self.alert_mgr.clear_alerts()
        self.alert_mgr.create_alert("critical", "syn_flood", "src1", "dst1", "desc1", "mit1")
        self.alert_mgr.create_alert("high", "slowloris", "src2", "dst2", "desc2", "mit2")
        self.alert_mgr.create_alert("medium", "arp_spoof", "src3", "dst3", "desc3", "mit3")

        alerts = self.alert_mgr.get_alerts()
        self.assertEqual(len(alerts), 3)

        crit_alerts = self.alert_mgr.get_alerts(severity="critical")
        self.assertEqual(len(crit_alerts), 1)
        self.assertEqual(crit_alerts[0]["attack_type"], "syn_flood")

        stats = self.alert_mgr.get_summary_stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["critical"], 1)
        self.assertEqual(stats["high"], 1)
        self.assertEqual(stats["medium"], 1)


class SecurityServerEndpointsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = create_app("demo")
        cls.client = TestClient(app)

    def test_get_conntrack_endpoint(self):
        """GET /api/security/conntrack endpointini tekshirish."""
        resp = self.client.get("/api/security/conntrack?mode=synthetic&limit=20")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("connections", data)
        self.assertIn("summary", data)
        self.assertGreater(len(data["connections"]), 0)
        summary = data["summary"]
        self.assertIn("total_connections", summary)
        self.assertIn("tcp_established", summary)

    def test_post_attack_simulate_endpoint(self):
        """POST /api/security/attack/simulate endpointini tekshirish."""
        # 1. SYN Flood
        payload_syn = {
            "attack_type": "syn_flood",
            "target_ip": "10.0.2.10",
            "target_port": 80,
            "pps": 5000,
            "duration_s": 3.0,
            "syn_cookies_enabled": False,
        }
        resp1 = self.client.post("/api/security/attack/simulate", json=payload_syn)
        self.assertEqual(resp1.status_code, 200)
        d1 = resp1.json()
        self.assertTrue(d1["ok"])
        self.assertEqual(d1["result"]["attack_type"], "syn_flood")
        self.assertIn("time_series", d1["result"])

        # 2. UDP Reflection
        payload_udp = {
            "attack_type": "udp_reflection",
            "victim_ip": "10.0.2.10",
            "reflector_type": "dns",
            "request_pps": 500,
            "duration_s": 2.0,
        }
        resp2 = self.client.post("/api/security/attack/simulate", json=payload_udp)
        self.assertEqual(resp2.status_code, 200)
        d2 = resp2.json()
        self.assertEqual(d2["result"]["attack_type"], "udp_reflection")

        # 3. Slowloris HTTP
        payload_slow = {
            "attack_type": "slowloris",
            "target_ip": "10.0.2.10",
            "port": 80,
            "connections": 100,
            "server_max_connections": 150,
        }
        resp3 = self.client.post("/api/security/attack/simulate", json=payload_slow)
        self.assertEqual(resp3.status_code, 200)
        d3 = resp3.json()
        self.assertEqual(d3["result"]["attack_type"], "slowloris")

        # 4. ARP Spoofing
        payload_arp = {
            "attack_type": "arp_spoof",
            "target_ip": "10.0.1.10",
            "gateway_ip": "10.0.1.1",
            "attacker_ip": "10.0.1.50",
            "duration_s": 2.0,
            "dai_enabled": True,
        }
        resp4 = self.client.post("/api/security/attack/simulate", json=payload_arp)
        self.assertEqual(resp4.status_code, 200)
        d4 = resp4.json()
        self.assertEqual(d4["result"]["attack_type"], "arp_spoof")
        self.assertTrue(d4["result"]["dai_alert_generated"])

        # 5. Noto'g'ri hujum turi
        resp_err = self.client.post("/api/security/attack/simulate", json={"attack_type": "unknown_attack"})
        self.assertEqual(resp_err.status_code, 400)

    def test_get_alerts_endpoint(self):
        """GET /api/security/alerts endpointini tekshirish."""
        resp = self.client.get("/api/security/alerts?limit=10")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("alerts", data)
        self.assertIn("stats", data)

    def test_post_firewall_rules_endpoint(self):
        """POST /api/security/firewall/rules endpointini tekshirish."""
        # A) Stateful qoidani baholash (evaluate)
        payload = {
            "action": "evaluate",
            "packet": {
                "proto": "tcp",
                "src_ip": "10.0.1.10",
                "src_port": 50000,
                "dst_ip": "10.0.2.20",
                "dst_port": 80,
                "flags": {"syn": True},
            },
            "rules": [
                {"chain": "forward", "action": "accept", "proto": "tcp", "dport": 80, "state": "new"}
            ],
            "default_policy": "drop",
        }
        resp = self.client.post("/api/security/firewall/rules", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["evaluation"]["action"], "accept")
        self.assertEqual(data["evaluation"]["fw_state"], "NEW")

        # B) Qoidalarni tasdiqlash (validate)
        rules_payload = {
            "action": "validate",
            "rules": [
                {"chain": "forward", "action": "accept", "state": "established"},
                {"chain": "forward", "action": "drop", "proto": "tcp", "dport": 23},
            ],
        }
        resp_v = self.client.post("/api/security/firewall/rules", json=rules_payload)
        self.assertEqual(resp_v.status_code, 200)
        self.assertTrue(resp_v.json()["ok"])
        self.assertEqual(resp_v.json()["rules_count"], 2)


if __name__ == "__main__":
    unittest.main()
