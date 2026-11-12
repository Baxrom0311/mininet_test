import struct
import unittest
from fastapi.testclient import TestClient

from lab.server import create_app
from lab.telemetry.tcp_info_collector import TCPInfoCollector, TCPInfoSample
from lab.telemetry.dpi_dissector import DPIDissector, calculate_rfc3550_jitter
from lab.telemetry.ebpf_tracker import EBPFTracker, EBPFEvent
from lab.telemetry.manager import TelemetryManager


class TelemetryTests(unittest.TestCase):
    def test_ss_output_parser(self):
        collector = TCPInfoCollector()
        sample_output = """
ESTAB 0 0 10.0.1.10:45678 10.0.2.20:80
	 cubic wscale:7,7 rto:200 rtt:14.25/2.15 ato:40 mss:1460 rcvspace:14600 ssthresh:24 cwnd:36 bytes_acked:146000 delivery_rate 25.5Mbps pacing_rate 30.6Mbps retrans:1/3
        """
        samples = collector.parse_ss_output(sample_output, now=1000.0)
        self.assertEqual(len(samples), 1)
        s = samples[0]
        self.assertEqual(s.src_ip, "10.0.1.10")
        self.assertEqual(s.src_port, 45678)
        self.assertEqual(s.dst_ip, "10.0.2.20")
        self.assertEqual(s.dst_port, 80)
        self.assertEqual(s.snd_cwnd, 36)
        self.assertEqual(s.snd_ssthresh, 24)
        self.assertAlmostEqual(s.rtt_ms, 14.25)
        self.assertAlmostEqual(s.rttvar_ms, 2.15)
        self.assertEqual(s.retrans, 1)
        self.assertEqual(s.total_retrans, 3)
        self.assertEqual(s.bytes_acked, 146000)
        self.assertEqual(s.delivery_rate_bps, 25.5 * 1e6)
        self.assertEqual(s.pacing_rate_bps, 30.6 * 1e6)
        self.assertEqual(s.stream_key, "10.0.1.10:45678->10.0.2.20:80")

    def test_rfc3550_jitter(self):
        # Initial jitter = 0.0, transit delta changed from 10ms to 18ms
        # d = |18 - 10| = 8
        # J = 0 + (8 - 0)/16 = 0.5
        j = calculate_rfc3550_jitter(0.0, 10.0, 18.0)
        self.assertAlmostEqual(j, 0.5)

        # Next delta: 18ms to 18ms (d=0)
        # J = 0.5 + (0 - 0.5)/16 = 0.5 - 0.03125 = 0.46875
        j2 = calculate_rfc3550_jitter(j, 18.0, 18.0)
        self.assertAlmostEqual(j2, 0.46875)

    def test_dpi_raw_tcp_dissector(self):
        dissector = DPIDissector()
        # Build synthetic Ethernet + IPv4 + TCP SYN-ACK packet
        # Ethernet header (14 bytes)
        eth = bytes([0x00, 0x11, 0x22, 0x33, 0x44, 0x55,  # dst mac
                     0x00, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE,  # src mac
                     0x08, 0x00])                         # eth_type = IPv4

        # IPv4 header (20 bytes)
        # v_ihl=0x45, tos=0, total_len=40, id=100, frag=0x4000 (DF), ttl=64, proto=6 (TCP), cksum=0
        ip_hdr = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 40, 100, 0x4000, 64, 6, 0,
                             bytes([10, 0, 1, 1]), bytes([10, 0, 2, 2]))

        # TCP header (20 bytes)
        # sport=80, dport=54321, seq=1000, ack=501, off_flags=(5<<12)|0x12 (SYN+ACK), win=64240, cksum=0, urp=0
        tcp_hdr = struct.pack("!HHIIHHHH", 80, 54321, 1000, 501, (5 << 12) | 0x12, 64240, 0, 0)

        raw = eth + ip_hdr + tcp_hdr
        d = dissector.dissect_raw(raw, ts=12345.67)
        self.assertIsNotNone(d)
        self.assertEqual(d.l3["src_ip"], "10.0.1.1")
        self.assertEqual(d.l3["dst_ip"], "10.0.2.2")
        self.assertEqual(d.l4["src_port"], 80)
        self.assertEqual(d.l4["dst_port"], 54321)
        self.assertIn("SYN", d.flags)
        self.assertIn("ACK", d.flags)
        self.assertEqual(d.l4["window"], 64240)

    def test_dpi_raw_udp_dissector(self):
        dissector = DPIDissector()
        eth = bytes([0] * 12 + [0x08, 0x00])
        ip_hdr = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 36, 101, 0, 64, 17, 0,
                             bytes([192, 168, 1, 5]), bytes([192, 168, 1, 6]))
        # UDP: sport=5004, dport=5004, len=16, cksum=0 + 8 bytes payload
        udp_hdr = struct.pack("!HHHH", 5004, 5004, 16, 0)
        payload = b"RTP_DATA"
        raw = eth + ip_hdr + udp_hdr + payload

        d = dissector.dissect_raw(raw, ts=100.0)
        self.assertIsNotNone(d)
        self.assertEqual(d.l4["proto"], "UDP")
        self.assertEqual(d.payload_len, 8)
        self.assertEqual(d.l4["src_port"], 5004)

    def test_ebpf_tracker_events(self):
        tracker = EBPFTracker()
        received = []
        tracker.register_callback(lambda ev: received.append(ev))

        tracker.record_drop("10.0.0.1", 1234, "10.0.0.2", 80, reason="NETEM_DROP")
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].event_type, "tcp_drop")
        self.assertEqual(received[0].drop_reason, "NETEM_DROP")

        tracker.record_retransmit("10.0.0.1", 1234, "10.0.0.2", 80, cwnd=16)
        self.assertEqual(len(received), 2)
        self.assertEqual(received[1].event_type, "tcp_retransmit")
        self.assertEqual(received[1].cwnd, 16)

    def test_telemetry_manager_and_api(self):
        mgr = TelemetryManager()
        sample1 = TCPInfoCollector.simulate_sample("10.0.0.1", 50000, "10.0.0.2", 80, 0, base_rtt=10.0)
        sample2 = TCPInfoCollector.simulate_sample("10.0.0.1", 50000, "10.0.0.2", 80, 1, base_rtt=10.0)
        mgr.ingest_tcp_sample(sample1)
        mgr.ingest_tcp_sample(sample2)

        streams = mgr.get_active_streams()
        self.assertEqual(len(streams), 1)
        self.assertEqual(streams[0]["stream_id"], "10.0.0.1:50000->10.0.0.2:80")

        detail = mgr.get_stream_detail("10.0.0.1:50000->10.0.0.2:80")
        self.assertIsNotNone(detail)
        self.assertEqual(len(detail["samples"]), 2)

        # Test FastAPI endpoints with TestClient
        app = create_app("demo")
        with TestClient(app) as client:
            # Simulate endpoint
            res = client.post("/api/telemetry/simulate", json={
                "src_ip": "10.9.9.1",
                "src_port": 12345,
                "dst_ip": "10.9.9.2",
                "dst_port": 443,
                "steps": 5,
                "has_loss": True
            })
            self.assertEqual(res.status_code, 200)
            stream_id = res.json()["stream_id"]

            # Streams list
            res_streams = client.get("/api/telemetry/streams").json()
            stream_ids = [s["stream_id"] for s in res_streams["streams"]]
            self.assertIn(stream_id, stream_ids)

            # Stream detail
            res_detail = client.get(f"/api/telemetry/stream/{stream_id}").json()
            self.assertEqual(res_detail["stream_id"], stream_id)
            self.assertEqual(len(res_detail["samples"]), 5)

            # Global stats
            res_stats = client.get("/api/telemetry/l4/stats").json()
            self.assertIn("active_streams_count", res_stats)
            self.assertGreaterEqual(res_stats["active_streams_count"], 1)

            # Drops
            res_drops = client.get("/api/telemetry/l4/drops").json()
            self.assertIn("drops", res_drops)

            # Dissect raw hex endpoint
            raw_hex = "00112233445500aabbccddee08004500002800644000400600000a0001010a0002020050d431000003e8000001f55012faf000000000"
            res_dissect = client.post("/api/telemetry/l4/dissect", json={"hex": raw_hex}).json()
            self.assertIn("layers", res_dissect)
            self.assertEqual(res_dissect["layers"]["l3"]["src_ip"], "10.0.1.1")
            self.assertEqual(res_dissect["layers"]["l4"]["proto"], "TCP")


if __name__ == "__main__":
    unittest.main()
