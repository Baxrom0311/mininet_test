"""Unit testlar: Stateful Firewall Generator (iptables & nftables), DMZ / NAT,
hamda Intrusion Detection System (IDS - Snort / Suricata) qoidalar dvigateli.
"""

import unittest
from fastapi.testclient import TestClient

from lab.security import (
    FirewallGenerator,
    FirewallRule,
    PortForwardRule,
    NATRule,
    IDSEngine,
    IDSRule,
    ids_engine,
    SecurityAlertManager,
)
import lab.p4 as p4
from lab.telemetry.dpi_dissector import DPIDissector
from lab.server import create_app


class FirewallGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.fw = FirewallGenerator(default_input="drop", default_forward="drop", default_output="accept")

    def test_basic_iptables_and_nftables_stateful_generation(self):
        """Stateful conntrack qoidalari to'g'ri generatsiya qilinishi."""
        self.fw.add_rule(FirewallRule(
            chain="forward",
            action="accept",
            proto="tcp",
            dport=80,
            state="new",
            comment="Allow HTTP new",
        ))

        # iptables tekshiruvi
        ipt = self.fw.generate_iptables()
        ipt_text = "\n".join(ipt)
        self.assertIn("-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT", ipt_text)
        self.assertIn("-m conntrack --ctstate INVALID -j DROP", ipt_text)
        self.assertIn("iptables -A FORWARD -p tcp --dport 80 -m conntrack --ctstate NEW -j ACCEPT", ipt_text)

        # nftables tekshiruvi
        nft = self.fw.generate_nftables()
        self.assertIn("table inet filter {", nft)
        self.assertIn("ct state established,related accept", nft)
        self.assertIn("ct state invalid drop", nft)
        self.assertIn("tcp dport 80 ct state new accept", nft)

    def test_port_forwarding_and_nat(self):
        """Port Forwarding (DNAT) va Masquerade / SNAT qoidalari."""
        self.fw.add_port_forward(
            ext_port=8080,
            int_ip="10.0.2.15",
            int_port=80,
            proto="tcp",
            in_if="eth0",
            comment="Web Redirect",
        )
        self.fw.add_nat(out_if="eth0", src_net="10.0.1.0/24", comment="LAN Masquerade")

        # iptables
        ipt = "\n".join(self.fw.generate_iptables())
        self.assertIn("iptables -t nat -A PREROUTING -i eth0 -p tcp --dport 8080 -j DNAT --to-destination 10.0.2.15:80", ipt)
        self.assertIn("iptables -A FORWARD -d 10.0.2.15 -p tcp --dport 80 -m conntrack --ctstate NEW -j ACCEPT", ipt)
        self.assertIn("iptables -t nat -A POSTROUTING -o eth0 -s 10.0.1.0/24 -j MASQUERADE", ipt)

        # nftables
        nft = self.fw.generate_nftables()
        self.assertIn("table ip nat {", nft)
        self.assertIn('iif "eth0" tcp dport 8080 dnat to 10.0.2.15:80', nft)
        self.assertIn('oif "eth0" ip saddr 10.0.1.0/24 masquerade', nft)
        self.assertIn("ip daddr 10.0.2.15 tcp dport 80 ct state new accept", nft)

    def test_dmz_preset_isolation(self):
        """3-zonali DMZ arxitekturasi va qat'iy izolatsiya."""
        dmz_fw = FirewallGenerator.create_dmz_preset(
            lan_subnet="10.0.1.0/24",
            dmz_subnet="10.0.2.0/24",
            wan_if="eth0",
            wan_ip="203.0.113.1",
        )

        ipt = "\n".join(dmz_fw.generate_iptables())
        # DMZ dan LAN ga qat'iy drop bo'lishi kerak
        self.assertIn("iptables -A FORWARD -s 10.0.2.0/24 -d 10.0.1.0/24 -m conntrack --ctstate NEW -j DROP", ipt)
        # LAN dan DMZ ga ruxsat bo'lishi kerak
        self.assertIn("iptables -A FORWARD -s 10.0.1.0/24 -d 10.0.2.0/24 -m conntrack --ctstate NEW -j ACCEPT", ipt)
        # Web server (port 80 va 443) DNAT qilingan bo'lishi kerak
        self.assertIn("DNAT --to-destination 10.0.2.10:80", ipt)
        self.assertIn("DNAT --to-destination 10.0.2.10:443", ipt)

        nft = dmz_fw.generate_nftables()
        self.assertIn("ip saddr 10.0.2.0/24 ip daddr 10.0.1.0/24 ct state new drop", nft)
        self.assertIn("ip saddr 10.0.1.0/24 ip daddr 10.0.2.0/24 ct state new accept", nft)


class IDSEngineTests(unittest.TestCase):
    def setUp(self):
        self.alert_mgr = SecurityAlertManager()
        self.ids = IDSEngine(alert_manager=self.alert_mgr)

    def test_snort_rule_parsing(self):
        """Snort qoidasini parsing qilish va maydonlarni ajratish."""
        rule_str = (
            'alert tcp $EXTERNAL_NET any -> $HOME_NET 80 '
            '(msg:"ET EXPLOIT Apache Struts RCE"; content:"cmd.exe"; nocase; sid:2000001; rev:2; classtype:attempted-admin; priority:1;)'
        )
        rule = self.ids.parse_rule(rule_str)
        self.assertIsNotNone(rule)
        self.assertEqual(rule.action, "alert")
        self.assertEqual(rule.proto, "tcp")
        self.assertEqual(rule.dst_port, "80")
        self.assertEqual(rule.sid, 2000001)
        self.assertEqual(rule.rev, 2)
        self.assertEqual(rule.content, "cmd.exe")
        self.assertTrue(rule.nocase)
        self.assertEqual(rule.priority, 1)

    def test_sql_injection_signature_match(self):
        """SQL injection imzosini aniqlash va alert chiqarish."""
        pkt = {
            "proto": "tcp",
            "src_ip": "198.51.100.55",
            "src_port": 49152,
            "dst_ip": "10.0.2.10",
            "dst_port": 80,
            "flags": {"psh": True, "ack": True},
        }
        payload = b"GET /index.php?id=1 UNION SELECT username,password FROM users HTTP/1.1\r\nHost: target\r\n\r\n"

        alerts = self.ids.match_packet(pkt, payload=payload)
        self.assertGreaterEqual(len(alerts), 1)
        first_alert = alerts[0]
        self.assertEqual(first_alert["sid"], 1000001)
        self.assertIn("SQL Injection", first_alert["msg"])
        self.assertIn("[1:1000001:1]", first_alert["fast_log"])

        # SecurityAlertManager da ham qayd etilganini tekshirish
        manager_alerts = self.alert_mgr.get_alerts()
        self.assertGreaterEqual(len(manager_alerts), 1)
        self.assertEqual(manager_alerts[0]["severity"], "critical")

    def test_path_traversal_signature_match(self):
        """Directory Traversal (../../etc/passwd) imzosini aniqlash."""
        pkt = {
            "proto": "tcp",
            "src_ip": "203.0.113.88",
            "src_port": 34567,
            "dst_ip": "10.0.2.10",
            "dst_port": 80,
            "flags": {"psh": True, "ack": True},
        }
        payload = b"GET /view?file=../../etc/passwd HTTP/1.1\r\nHost: target\r\n\r\n"

        alerts = self.ids.match_packet(pkt, payload=payload)
        sids = [a["sid"] for a in alerts]
        self.assertIn(1000003, sids)  # ../..
        self.assertIn(1000004, sids)  # /etc/passwd

    def test_xmas_tree_scan_detection(self):
        """Nmap XMAS Scan (FIN+PSH+URG) flaglarini aniqlash."""
        pkt = {
            "proto": "tcp",
            "src_ip": "198.51.100.12",
            "src_port": 60000,
            "dst_ip": "10.0.1.10",
            "dst_port": 443,
            "flags": {"fin": True, "psh": True, "urg": True},
        }
        alerts = self.ids.match_packet(pkt, payload=b"")
        sids = [a["sid"] for a in alerts]
        self.assertIn(1000006, sids)

    def test_syn_flood_threshold_detection(self):
        """SYN Flood threshold bo'yicha ogohlantirish berishi."""
        pkt = {
            "proto": "tcp",
            "src_ip": "198.51.100.99",
            "src_port": 12345,
            "dst_ip": "10.0.2.10",
            "dst_port": 80,
            "flags": {"syn": True},
        }
        now = 1000.0
        alerts = []
        for i in range(25):
            res = self.ids.match_packet(pkt, now=now + (i * 0.05))
            if res:
                alerts.extend(res)

        # 20 tadan oshganda threshold trigerr bo'ladi (sid 1000008)
        syn_alerts = [a for a in alerts if a["sid"] == 1000008]
        self.assertGreater(len(syn_alerts), 0)


class P4ModuleAndDissectorTests(unittest.TestCase):
    def test_p4_package_exports_and_templates(self):
        """lab.p4 to'plamining eksportlari va shablonlari."""
        templates = p4.list_p4_templates()
        self.assertIn("l2_switch.p4", templates)
        self.assertIn("ipv4_router.p4", templates)

        l2_src = p4.get_p4_template("l2_switch.p4")
        self.assertIn("dmac", l2_src)

        # Pipeline outline parsing
        outline = p4.parse_p4_outline(l2_src)
        self.assertIn("pipelines", outline)
        self.assertTrue(len(outline["pipelines"]) > 0)

    def test_dpi_dissector_with_raw_hex(self):
        """DPIDissector orqali xom hex freymni Cisco PDU ga aylantirish."""
        dissector = DPIDissector()
        hex_data = "00112233445566778899aabb0800450000280001000040067cb20a0001010a00020204d20050000000010000000050022000b21a0000"
        pdu = dissector.build_cisco_pdu_inspection({"raw_hex": hex_data})
        self.assertEqual(pdu["in_layers"]["l3"]["src_ip"], "10.0.1.1")
        self.assertEqual(pdu["in_layers"]["l3"]["dst_ip"], "10.0.2.2")
        self.assertEqual(pdu["in_layers"]["l4"]["proto"], "TCP")
        self.assertEqual(pdu["in_layers"]["l4"]["dst_port"], 80)


class SecurityAndP4ServerEndpointsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = create_app("demo")
        cls.client = TestClient(app)

    def test_firewall_generate_endpoint(self):
        """POST /api/security/firewall/generate va DMZ preset endpointi."""
        payload = {
            "dmz_preset": True,
            "lan_subnet": "10.0.1.0/24",
            "dmz_subnet": "10.0.2.0/24",
            "wan_if": "eth0",
            "syntax": "both",
        }
        resp = self.client.post("/api/security/firewall/generate", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("iptables", data)
        self.assertIn("nftables", data)
        self.assertIn("table inet filter", data["nftables"])

    def test_firewall_rules_action_generate(self):
        """POST /api/security/firewall/rules action=generate."""
        payload = {
            "action": "generate",
            "rules": [
                {"chain": "forward", "action": "accept", "proto": "tcp", "dport": 443, "state": "new"}
            ],
            "syntax": "both",
        }
        resp = self.client.post("/api/security/firewall/rules", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("iptables", data)

    def test_ids_rules_and_match_endpoints(self):
        """GET /api/security/ids/rules va POST /api/security/ids/match."""
        # Rules ro'yxati
        resp_r = self.client.get("/api/security/ids/rules")
        self.assertEqual(resp_r.status_code, 200)
        data_r = resp_r.json()
        self.assertTrue(data_r["ok"])
        self.assertGreaterEqual(data_r["count"], 5)

        # Imzo mosligi tekshiruvi
        payload_match = {
            "packet": {
                "proto": "tcp",
                "src_ip": "1.2.3.4",
                "src_port": 50000,
                "dst_ip": "10.0.2.10",
                "dst_port": 80,
            },
            "payload": "GET /test?q=UNION SELECT 1,2,3 HTTP/1.1\r\n\r\n",
        }
        resp_m = self.client.post("/api/security/ids/match", json=payload_match)
        self.assertEqual(resp_m.status_code, 200)
        data_m = resp_m.json()
        self.assertTrue(data_m["ok"])
        self.assertTrue(data_m["matched"])
        self.assertEqual(data_m["alerts"][0]["sid"], 1000001)

    def test_pdu_inspect_with_raw_hex(self):
        """POST /api/pdu/inspect raw_hex bilan."""
        hex_data = "00112233445566778899aabb0800450000280001000040067cb20a0001010a00020204d20050000000010000000050022000b21a0000"
        resp = self.client.post("/api/pdu/inspect", json={"raw_hex": hex_data, "device": "Router1"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["in_layers"]["l3"]["src_ip"], "10.0.1.1")
        self.assertEqual(data["in_layers"]["l4"]["dst_port"], 80)


if __name__ == "__main__":
    unittest.main()
