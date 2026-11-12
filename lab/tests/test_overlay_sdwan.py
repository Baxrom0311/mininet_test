"""Unit and Integration Test Suite for Faza 9: Multi-Cloud Overlay & SD-WAN.

Tests cover:
1. VXLAN Data-Plane Encap/Decap (RFC 7348) & BGP EVPN Control-Plane (RFC 7432 / RFC 8365).
2. WireGuard Cryptographic VPN (Noise IK, Curve25519, MTU 1420, IPsec comparison).
3. Application-Aware SD-WAN dynamic path selector (SLA evaluation, brownout/blackout auto-failover).
4. FastAPI overlay REST endpoints.
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
    sdwan_router,
)


class TestVXLANDataPlaneAndEVPN(unittest.TestCase):
    """VXLAN data-plane enkapsulyatsiyasi va BGP EVPN control-plane marshrutlari."""

    def test_ethernet_frame_encoding_and_decoding(self):
        """Ethernet freym seriyalizatsiyasi va deseriylizatsiyasi."""
        src_mac = "02:00:aa:bb:cc:01"
        dst_mac = "02:00:aa:bb:cc:02"
        payload = b"PAYLOAD_ETHERNET_TEST_DATA"
        frame = EthernetFrame(src_mac=src_mac, dst_mac=dst_mac, ethertype=0x0800, payload=payload)
        raw_bytes = frame.encode()

        # 6B dst + 6B src + 2B ethertype = 14B header
        self.assertEqual(len(raw_bytes), 14 + len(payload))

        decoded = EthernetFrame.decode(raw_bytes)
        self.assertEqual(decoded.src_mac, src_mac)
        self.assertEqual(decoded.dst_mac, dst_mac)
        self.assertEqual(decoded.ethertype, 0x0800)
        self.assertEqual(decoded.payload, payload)

    def test_ipv4_packet_encoding_and_decoding(self):
        """IPv4 paket seriyalizatsiyasi va tekshiruvi."""
        src_ip = "192.168.10.5"
        dst_ip = "192.168.20.10"
        payload = b"UDP_TEST_PAYLOAD"
        pkt = IPv4Packet(src_ip=src_ip, dst_ip=dst_ip, proto=17, ttl=64, payload=payload)
        encoded = pkt.encode()

        self.assertGreaterEqual(len(encoded), 20)
        decoded = IPv4Packet.decode(encoded)
        self.assertEqual(decoded.src_ip, src_ip)
        self.assertEqual(decoded.dst_ip, dst_ip)
        self.assertEqual(decoded.proto, 17)
        self.assertEqual(decoded.payload, payload)

    def test_vxlan_encapsulation_and_overhead(self):
        """VXLAN enkapsulyatsiya formati va 50 baytlik overhead hisob-kitobi."""
        inner_ip = IPv4Packet(src_ip="10.10.1.1", dst_ip="10.10.1.2", proto=6, payload=b"SYN_PACKET")
        inner_eth = EthernetFrame(
            src_mac="00:aa:00:00:00:01",
            dst_mac="00:aa:00:00:00:02",
            ethertype=0x0800,
            payload=inner_ip.encode(),
        )

        encap = VXLANPacket.encapsulate(
            src_vtep="172.20.0.1",
            dst_vtep="172.20.0.2",
            vni=100,
            inner_frame=inner_eth,
        )

        # 50B overhead: 14B outer eth + 20B outer ip + 8B outer udp + 8B vxlan
        self.assertEqual(VXLAN_ENCAP_OVERHEAD, 50)
        self.assertEqual(len(encap), VXLAN_ENCAP_OVERHEAD + len(inner_eth.encode()))

        # Deserializatsiya tekshiruvi
        decap = VXLANPacket.decapsulate(encap)
        self.assertEqual(decap["outer"]["src_vtep"], "172.20.0.1")
        self.assertEqual(decap["outer"]["dst_vtep"], "172.20.0.2")
        self.assertEqual(decap["outer"]["dst_port"], VXLAN_UDP_PORT)
        self.assertEqual(decap["vxlan"]["vni"], 100)
        self.assertTrue(decap["vxlan"]["i_bit_set"])
        self.assertEqual(decap["inner"]["src_mac"], "00:aa:00:00:00:01")
        self.assertEqual(decap["inner"]["dst_mac"], "00:aa:00:00:00:02")
        self.assertEqual(decap["inner"]["ip"]["src_ip"], "10.10.1.1")
        self.assertEqual(decap["inner"]["ip"]["dst_ip"], "10.10.1.2")

    def test_evpn_route_types_2_and_5(self):
        """BGP EVPN Type-2 (MAC/IP) va Type-5 (IP Prefix) marshrutlari tekshiruvi."""
        engine = VXLANOverlayEngine()

        # Type-2
        route_t2 = engine.advertise_mac_ip(
            origin_vtep_id="leaf1",
            vni=100,
            mac="aa:bb:cc:11:22:33",
            ip="10.10.1.15",
            rd="65001:100",
            rt="target:65000:100",
        )
        self.assertIsInstance(route_t2, EVPNRouteType2)
        self.assertEqual(route_t2.vni, 100)
        self.assertEqual(route_t2.mac, "aa:bb:cc:11:22:33")
        self.assertEqual(route_t2.ip, "10.10.1.15")

        # Type-5
        route_t5 = engine.advertise_prefix(
            origin_vtep_id="leaf2",
            prefix="10.200.0.0/16",
            gw_ip="172.20.0.2",
            vni=200,
            rd="65002:200",
            rt="target:65000:200",
        )
        self.assertIsInstance(route_t5, EVPNRouteType5)
        self.assertEqual(route_t5.prefix, "10.200.0.0/16")
        self.assertEqual(route_t5.gw_ip, "172.20.0.2")
        self.assertEqual(route_t5.vni, 200)

    def test_leaf_spine_multi_tenant_isolation(self):
        """Leaf-Spine multi-tenant topologiyasi va tenant izolyatsiyasi."""
        topo = LeafSpineVXLANTopology()
        spec = topo.build_topology_spec()

        self.assertIn("underlay", spec)
        self.assertIn("tenant_a", spec["tenants"])
        self.assertIn("tenant_b", spec["tenants"])

        # Intra-tenant routing muvaffaqiyatli
        res_a = topo.simulate_forwarding("h1-tenant-a", "h2-tenant-a")
        self.assertTrue(res_a["ok"])
        self.assertEqual(res_a["vni"], 100)
        self.assertEqual(res_a["overhead_bytes"], 50)

        # Cross-tenant routing bloklanadi
        res_cross = topo.simulate_forwarding("h1-tenant-a", "h3-tenant-b")
        self.assertFalse(res_cross["ok"])
        self.assertTrue(res_cross["tenant_isolation_enforced"])
        self.assertIn("DROPPED", res_cross["verdict"])

        # FRR BGP EVPN va Linux VXLAN komandalarini generatsiya qilish
        frr_cfg = topo.generate_frr_evpn_config("leaf1")
        self.assertIn("router bgp 65001", frr_cfg)
        self.assertIn("address-family l2vpn evpn", frr_cfg)

        cmds = topo.generate_linux_vxlan_cmds("leaf1")
        self.assertTrue(any("ip link add" in c for c in cmds))
        self.assertTrue(any("vxlan100" in c for c in cmds))


class TestWireGuardVPN(unittest.TestCase):
    """WireGuard kriptografik tunnel, Noise IK va IPsec taqqoslashi."""

    def test_curve25519_keypair_and_secret_derivation(self):
        """Curve25519 kalit juftligini yaratish va umumiy maxfiy kalit hisoblash."""
        priv_a, pub_a = Curve25519Key.generate_keypair()
        priv_b, pub_b = Curve25519Key.generate_keypair()

        self.assertTrue(Curve25519Key.validate_key(priv_a))
        self.assertTrue(Curve25519Key.validate_key(pub_a))
        self.assertTrue(Curve25519Key.validate_key(priv_b))
        self.assertTrue(Curve25519Key.validate_key(pub_b))

        shared_a = Curve25519Key.derive_shared_secret(priv_a, pub_b)
        shared_b = Curve25519Key.derive_shared_secret(priv_b, pub_a)
        self.assertIsNotNone(shared_a)
        self.assertIsNotNone(shared_b)
        self.assertEqual(shared_a, shared_b)

    def test_noise_ik_handshake_simulation(self):
        """Noise IK 1-RTT handshake simulyatsiyasi va xabar o'lchamlari."""
        engine = WireGuardEngine(interface_name="wg0")
        _, peer_pub = Curve25519Key.generate_keypair()

        peer = engine.add_peer(
            public_key=peer_pub,
            endpoint="198.51.100.1:51820",
            allowed_ips=["10.100.0.0/24"],
            persistent_keepalive_s=25,
        )
        self.assertEqual(peer.persistent_keepalive_s, 25)
        self.assertFalse(peer.is_connected)

        hs = engine.initiate_handshake(peer_pub)
        self.assertEqual(hs["handshake_status"], "established")
        self.assertEqual(hs["initiation_msg_bytes"], 148)
        self.assertEqual(hs["response_msg_bytes"], 92)
        self.assertEqual(hs["rtt_mode"], "1-RTT Handshake")
        self.assertTrue(peer.is_connected)

    def test_ipsec_vs_wireguard_performance_comparison(self):
        """WireGuard va IPsec (IKEv2/ESP) overhead va kod murakkabligi taqqoslashi."""
        engine = WireGuardEngine()
        comp = engine.compare_with_ipsec(packet_size=1420, link_rtt_ms=30.0)

        metrics = comp["metrics"]
        self.assertIn("handshake", metrics)
        self.assertIn("packet_overhead", metrics)
        self.assertIn("codebase_complexity", metrics)

        # Handshake latency: WireGuard 1-RTT < IPsec 2-RTT
        self.assertLess(
            metrics["handshake"]["wireguard"]["latency_ms"],
            metrics["handshake"]["ipsec_ikev2"]["latency_ms"],
        )

        # WireGuard 60B vs IPsec 78B overhead
        self.assertEqual(metrics["packet_overhead"]["wireguard_bytes"], 60)
        self.assertGreater(
            metrics["packet_overhead"]["ipsec_esp_udp_bytes"],
            metrics["packet_overhead"]["wireguard_bytes"],
        )


class TestSDWANPathSelector(unittest.TestCase):
    """SD-WAN Application-Aware dinamik yo'l tanlash va SLA boshqaruvi."""

    def test_sla_traffic_class_mapping(self):
        """SLA bo'yicha trafik turlarining optimal kanallarga yo'naltirilishi."""
        mgr = SDWANManager()

        # VoIP: kam kechikish talab etiladi -> MPLS
        voip = mgr.select_path(TrafficClass.VOIP)
        self.assertEqual(voip["selected_tunnel"]["tunnel_id"], "mpls_1")

        # Critical DB: minimal paket yo'qotish talab etiladi -> MPLS
        db = mgr.select_path(TrafficClass.CRITICAL_DB)
        self.assertEqual(db["selected_tunnel"]["tunnel_id"], "mpls_1")

        # Bulk: xarajat ustun -> inet_wg
        bulk = mgr.select_path(TrafficClass.BULK)
        self.assertEqual(bulk["selected_tunnel"]["tunnel_id"], "inet_wg")

    def test_blackout_and_brownout_auto_failover(self):
        """Blackout (kanal uzilishi) va brownout (SLA buzilishi) failover sinovi."""
        mgr = SDWANManager()

        # Blackout: MPLS kanalini o'chiramiz
        res = mgr.inject_tunnel_impairment("mpls_1", set_status="down")
        self.assertGreaterEqual(res["failovers_triggered"], 1)

        # VoIP kanali inet_wg ga o'tishi kerak
        decision = mgr.select_path(TrafficClass.VOIP)
        self.assertNotEqual(decision["selected_tunnel"]["tunnel_id"], "mpls_1")
        self.assertEqual(decision["selected_tunnel"]["tunnel_id"], "inet_wg")

        # Kanalni qayta tiklash
        restored = mgr.restore_tunnel("mpls_1")
        self.assertEqual(restored["tunnel"]["status"], "healthy")

        # Qayta MPLS ga qaytish
        decision_recovered = mgr.select_path(TrafficClass.VOIP)
        self.assertEqual(decision_recovered["selected_tunnel"]["tunnel_id"], "mpls_1")


class TestOverlayFastAPIEndpoints(unittest.TestCase):
    """FastAPI Overlay REST endpointlar integratsion testlari."""

    @classmethod
    def setUpClass(cls):
        app = create_app()
        cls.client = TestClient(app)

    def test_vxlan_endpoints(self):
        """GET /api/overlay/vxlan/tunnels va POST /api/overlay/vxlan/configure."""
        resp = self.client.get("/api/overlay/vxlan/tunnels")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("tunnels", data)
        self.assertIn("vteps", data)

        resp_cfg = self.client.post(
            "/api/overlay/vxlan/configure",
            json={"vni": 500, "src_vtep": "10.1.1.1", "dst_vtep": "10.1.1.2"},
        )
        self.assertEqual(resp_cfg.status_code, 200)
        self.assertTrue(resp_cfg.json()["ok"])
        self.assertEqual(resp_cfg.json()["tunnel"]["vni"], 500)

        # Legacy encap & status endpoints
        resp_status = self.client.get("/api/overlay/vxlan/status")
        self.assertEqual(resp_status.status_code, 200)

        resp_encap = self.client.post(
            "/api/overlay/vxlan/encap",
            json={
                "vni": 100,
                "src_vtep": "10.0.0.1",
                "dst_vtep": "10.0.0.2",
                "src_ip": "192.168.1.10",
                "dst_ip": "192.168.1.20",
                "payload": "HELLO_VXLAN",
            },
        )
        self.assertEqual(resp_encap.status_code, 200)

    def test_wireguard_endpoints(self):
        """GET /api/overlay/wireguard/peers va POST /api/overlay/wireguard/peer."""
        resp = self.client.get("/api/overlay/wireguard/peers")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("peers", data)
        self.assertIn("ipsec_comparison", data)

        _, new_pub = Curve25519Key.generate_keypair()
        resp_peer = self.client.post(
            "/api/overlay/wireguard/peer",
            json={
                "public_key": new_pub,
                "endpoint": "198.51.100.99:51820",
                "allowed_ips": ["10.250.0.0/24"],
                "persistent_keepalive": 25,
            },
        )
        self.assertEqual(resp_peer.status_code, 200)
        self.assertTrue(resp_peer.json()["ok"])

        # Legacy status & handshake endpoints
        resp_st = self.client.get("/api/overlay/wireguard/status")
        self.assertEqual(resp_st.status_code, 200)

        resp_hs = self.client.post(
            "/api/overlay/wireguard/handshake",
            json={"public_key": new_pub},
        )
        self.assertEqual(resp_hs.status_code, 200)

    def test_sdwan_endpoints(self):
        """GET /api/overlay/sdwan/paths va POST /api/overlay/sdwan/evaluate."""
        resp = self.client.get("/api/overlay/sdwan/paths")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("paths", data)

        resp_eval = self.client.post(
            "/api/overlay/sdwan/evaluate",
            json={"traffic_class": "voip"},
        )
        self.assertEqual(resp_eval.status_code, 200)
        data_eval = resp_eval.json()
        self.assertTrue(data_eval["ok"])
        self.assertEqual(data_eval["traffic_class"], "voip")
        self.assertIn("decision", data_eval)

        # Legacy tunnels & steer endpoints
        resp_tunnels = self.client.get("/api/overlay/sdwan/tunnels")
        self.assertEqual(resp_tunnels.status_code, 200)

        resp_steer = self.client.post(
            "/api/overlay/sdwan/steer",
            json={"traffic_class": "bulk"},
        )
        self.assertEqual(resp_steer.status_code, 200)


if __name__ == "__main__":
    unittest.main()
