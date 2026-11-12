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


class TestVXLANDataPlaneAndEVPN(unittest.TestCase):
    def test_ethernet_frame_pack_unpack(self):
        src = "00:11:22:33:44:55"
        dst = "aa:bb:cc:dd:ee:ff"
        payload = b"TEST_PAYLOAD_123456"
        frame = EthernetFrame(src_mac=src, dst_mac=dst, ethertype=0x0800, payload=payload)
        raw = frame.encode()
        self.assertEqual(len(raw), 14 + len(payload))

        decoded = EthernetFrame.decode(raw)
        self.assertEqual(decoded.src_mac, src)
        self.assertEqual(decoded.dst_mac, dst)
        self.assertEqual(decoded.ethertype, 0x0800)
        self.assertEqual(decoded.payload, payload)

    def test_ipv4_packet_pack_unpack(self):
        pkt = IPv4Packet(
            src_ip="192.168.10.1",
            dst_ip="192.168.20.2",
            proto=17,
            ttl=64,
            payload=b"UDP_PAYLOAD",
        )
        raw = pkt.encode()
        self.assertGreaterEqual(len(raw), 20)
        decoded = IPv4Packet.decode(raw)
        self.assertEqual(decoded.src_ip, "192.168.10.1")
        self.assertEqual(decoded.dst_ip, "192.168.20.2")
        self.assertEqual(decoded.proto, 17)
        self.assertEqual(decoded.payload, b"UDP_PAYLOAD")

    def test_vxlan_full_packet_encapsulation_and_decapsulation(self):
        inner_ip = IPv4Packet(src_ip="10.1.1.1", dst_ip="10.1.1.2", proto=6, payload=b"TCP_SYN")
        inner_eth = EthernetFrame(
            src_mac="00:10:00:00:00:01",
            dst_mac="00:10:00:00:00:02",
            ethertype=0x0800,
            payload=inner_ip.encode(),
        )

        encap_raw = VXLANPacket.encapsulate(
            src_vtep="172.16.0.1",
            dst_vtep="172.16.0.2",
            vni=100,
            inner_frame=inner_eth,
        )

        # Overhead: Outer Eth (14) + Outer IP (20) + Outer UDP (8) + VXLAN (8) = 50 bytes
        self.assertEqual(len(encap_raw), 50 + len(inner_eth.encode()))

        # Decapsulate
        decap = VXLANPacket.decapsulate(encap_raw)
        self.assertEqual(decap["outer"]["src_vtep"], "172.16.0.1")
        self.assertEqual(decap["outer"]["dst_vtep"], "172.16.0.2")
        self.assertEqual(decap["outer"]["dst_port"], 4789)
        self.assertEqual(decap["vxlan"]["vni"], 100)
        self.assertTrue(decap["vxlan"]["i_bit_set"])
        self.assertEqual(decap["inner"]["src_mac"], "00:10:00:00:00:01")
        self.assertEqual(decap["inner"]["dst_mac"], "00:10:00:00:00:02")
        self.assertEqual(decap["inner"]["ip"]["src_ip"], "10.1.1.1")
        self.assertEqual(decap["inner"]["ip"]["dst_ip"], "10.1.1.2")

    def test_evpn_type5_prefix_routes(self):
        engine = VXLANOverlayEngine()
        route = engine.advertise_prefix(
            origin_vtep_id="leaf1",
            prefix="192.168.100.0/24",
            gw_ip="10.0.0.1",
            vni=1000,
            rd="65001:1000",
            rt="target:65000:1000",
        )
        self.assertEqual(route.prefix, "192.168.100.0/24")
        self.assertEqual(route.vni, 1000)
        self.assertEqual(route.rd, "65001:1000")
        self.assertEqual(route.rt, "target:65000:1000")

    def test_leaf_spine_topology_helper_and_tenant_isolation(self):
        topo = LeafSpineVXLANTopology()
        spec = topo.build_topology_spec()
        self.assertIn("underlay", spec)
        self.assertIn("tenant_a", spec["tenants"])
        self.assertIn("tenant_b", spec["tenants"])

        # Intra-tenant cross-leaf communication (allowed)
        res_same_tenant = topo.simulate_forwarding("h1-tenant-a", "h2-tenant-a")
        self.assertTrue(res_same_tenant["ok"])
        self.assertEqual(res_same_tenant["vni"], 100)
        self.assertEqual(res_same_tenant["overhead_bytes"], 50)

        # Cross-tenant communication (blocked by tenant isolation)
        res_cross_tenant = topo.simulate_forwarding("h1-tenant-a", "h3-tenant-b")
        self.assertFalse(res_cross_tenant["ok"])
        self.assertTrue(res_cross_tenant["tenant_isolation_enforced"])
        self.assertIn("DROPPED", res_cross_tenant["verdict"])

        # FRR & Linux config generation
        frr_cfg = topo.generate_frr_evpn_config("leaf1")
        self.assertIn("router bgp 65001", frr_cfg)
        self.assertIn("address-family l2vpn evpn", frr_cfg)

        linux_cmds = topo.generate_linux_vxlan_cmds("leaf1")
        self.assertTrue(any("vxlan100" in cmd for cmd in linux_cmds))
        self.assertTrue(any("vxlan200" in cmd for cmd in linux_cmds))


class TestWireGuardCryptographicVPN(unittest.TestCase):
    def test_curve25519_key_generation_and_validation(self):
        priv, pub = Curve25519Key.generate_keypair()
        self.assertTrue(Curve25519Key.validate_key(priv))
        self.assertTrue(Curve25519Key.validate_key(pub))

        # Shared secret derivation
        priv2, pub2 = Curve25519Key.generate_keypair()
        s1 = Curve25519Key.derive_shared_secret(priv, pub2)
        s2 = Curve25519Key.derive_shared_secret(priv2, pub)
        self.assertIsNotNone(s1)
        self.assertIsNotNone(s2)

    def test_wireguard_noise_ik_handshake_simulation(self):
        wg = WireGuardEngine(interface_name="wg_test")
        _, peer_pub = Curve25519Key.generate_keypair()
        peer = wg.add_peer(
            public_key=peer_pub,
            endpoint="203.0.113.1:51820",
            allowed_ips=["10.99.0.0/24"],
            persistent_keepalive_s=25,
        )
        self.assertEqual(peer.persistent_keepalive_s, 25)

        hs = wg.initiate_handshake(peer_pub)
        self.assertEqual(hs["handshake_status"], "established")
        self.assertEqual(hs["initiation_msg_bytes"], 148)
        self.assertEqual(hs["response_msg_bytes"], 92)
        self.assertEqual(hs["rtt_mode"], "1-RTT Handshake")
        self.assertTrue(peer.is_connected)

    def test_ipsec_vs_wireguard_comparison_model(self):
        wg = WireGuardEngine()
        comp = wg.compare_with_ipsec(packet_size=1420, link_rtt_ms=25.0)
        self.assertIn("metrics", comp)
        handshake = comp["metrics"]["handshake"]
        self.assertIn("wireguard", handshake)
        self.assertIn("ipsec_ikev2", handshake)
        self.assertLess(handshake["wireguard"]["latency_ms"], handshake["ipsec_ikev2"]["latency_ms"])

        overhead = comp["metrics"]["packet_overhead"]
        self.assertEqual(overhead["wireguard_bytes"], 60)
        self.assertGreater(overhead["ipsec_esp_udp_bytes"], overhead["wireguard_bytes"])

        complexity = comp["metrics"]["codebase_complexity"]
        self.assertIn("~4,000", complexity["wireguard_lines_of_code"])


class TestSDWANDynamicSteering(unittest.TestCase):
    def test_traffic_class_sla_mapping(self):
        mgr = SDWANManager()
        # VoIP -> MPLS preferred for lowest latency
        res_voip = mgr.select_path(TrafficClass.VOIP)
        self.assertEqual(res_voip["selected_tunnel"]["tunnel_id"], "mpls_1")

        # Critical DB -> Loss preferred
        res_db = mgr.select_path(TrafficClass.CRITICAL_DB)
        self.assertEqual(res_db["selected_tunnel"]["tunnel_id"], "mpls_1")

        # Bulk -> Cost preferred
        res_bulk = mgr.select_path(TrafficClass.BULK)
        self.assertEqual(res_bulk["selected_tunnel"]["tunnel_id"], "inet_wg")

    def test_brownout_and_blackout_failover(self):
        mgr = SDWANManager()
        # Blackout simulation: link down
        imp = mgr.inject_tunnel_impairment("mpls_1", set_status="down")
        self.assertGreater(imp["failovers_triggered"], 0)
        self.assertEqual(imp["tunnel"]["status"], "down")

        # After blackout, VoIP fails over to inet_wg
        res_voip = mgr.select_path(TrafficClass.VOIP)
        self.assertNotEqual(res_voip["selected_tunnel"]["tunnel_id"], "mpls_1")

        # Restore
        restored = mgr.restore_tunnel("mpls_1")
        self.assertEqual(restored["tunnel"]["status"], "healthy")


class TestOverlayFastAPIEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = create_app()
        cls.client = TestClient(app)

    def test_vxlan_endpoints(self):
        # GET /api/overlay/vxlan/tunnels
        resp = self.client.get("/api/overlay/vxlan/tunnels")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("tunnels", data)
        self.assertIn("vteps", data)

        # POST /api/overlay/vxlan/configure
        resp_cfg = self.client.post(
            "/api/overlay/vxlan/configure",
            json={"vni": 300, "src_vtep": "10.0.0.1", "dst_vtep": "10.0.0.3"},
        )
        self.assertEqual(resp_cfg.status_code, 200)
        self.assertTrue(resp_cfg.json()["ok"])
        self.assertEqual(resp_cfg.json()["tunnel"]["vni"], 300)

    def test_wireguard_endpoints(self):
        # GET /api/overlay/wireguard/peers
        resp = self.client.get("/api/overlay/wireguard/peers")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("peers", data)
        self.assertIn("ipsec_comparison", data)

        # POST /api/overlay/wireguard/peer
        _, new_pub = Curve25519Key.generate_keypair()
        resp_add = self.client.post(
            "/api/overlay/wireguard/peer",
            json={
                "public_key": new_pub,
                "endpoint": "198.51.100.20:51820",
                "allowed_ips": ["10.50.3.0/24"],
                "persistent_keepalive": 25,
            },
        )
        self.assertEqual(resp_add.status_code, 200)
        self.assertTrue(resp_add.json()["ok"])

    def test_sdwan_endpoints(self):
        # GET /api/overlay/sdwan/paths
        resp = self.client.get("/api/overlay/sdwan/paths")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("paths", data)

        # POST /api/overlay/sdwan/evaluate
        resp_eval = self.client.post(
            "/api/overlay/sdwan/evaluate",
            json={"traffic_class": "voip"},
        )
        self.assertEqual(resp_eval.status_code, 200)
        data_eval = resp_eval.json()
        self.assertTrue(data_eval["ok"])
        self.assertEqual(data_eval["traffic_class"], "voip")
        self.assertIn("decision", data_eval)


if __name__ == "__main__":
    unittest.main()
