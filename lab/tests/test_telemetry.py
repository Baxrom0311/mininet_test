import base64
import struct
import unittest
from fastapi.testclient import TestClient

from lab.server import create_app
from lab.telemetry.tcp_info_collector import TCPInfoCollector, TCPInfoSample, parse_rate_string, hex_to_ip
from lab.telemetry.dpi_dissector import DPIDissector, calculate_rfc3550_jitter, UDPJitterTracker
from lab.telemetry.ebpf_tracker import EBPFTracker, EBPFEvent
from lab.telemetry.queue_tracker import QueueTracker, QueueStats
from lab.telemetry.manager import TelemetryManager, StreamHistory


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

    def test_congestion_states_parsing(self):
        collector = TCPInfoCollector()
        # Test Open, Disorder, CWR, Recovery, Loss
        test_cases = [
            ("ESTAB 0 0 10.0.0.1:1000 10.0.0.2:80\n\t cubic open cwnd:10", "open"),
            ("ESTAB 0 0 10.0.0.1:1001 10.0.0.2:80\n\t cubic disorder cwnd:12 retrans:0/1", "disorder"),
            ("ESTAB 0 0 10.0.0.1:1002 10.0.0.2:80\n\t cubic cwr cwnd:14", "cwr"),
            ("ESTAB 0 0 10.0.0.1:1003 10.0.0.2:80\n\t cubic recovery cwnd:8 retrans:1/2", "recovery"),
            ("ESTAB 0 0 10.0.0.1:1004 10.0.0.2:80\n\t cubic loss cwnd:1 retrans:2/5", "loss"),
            # Default when unmentioned is open
            ("ESTAB 0 0 10.0.0.1:1005 10.0.0.2:80\n\t bbr cwnd:20", "open"),
        ]
        for output, expected_state in test_cases:
            samples = collector.parse_ss_output(output)
            self.assertEqual(len(samples), 1)
            self.assertEqual(samples[0].ca_state, expected_state)
            self.assertEqual(samples[0].to_dict()["ca_state"], expected_state)

    def test_pacing_and_rate_parsers(self):
        self.assertEqual(parse_rate_string("2388648bps"), 2388648.0)
        self.assertEqual(parse_rate_string("30.6Mbps"), 30.6e6)
        self.assertEqual(parse_rate_string("1.5Gbps"), 1.5e9)
        self.assertEqual(parse_rate_string("512Kbps"), 512e3)
        self.assertEqual(parse_rate_string("1000"), 1000.0)

        collector = TCPInfoCollector()
        raw = "ESTAB 0 0 192.168.1.10:5000 192.168.1.20:80 cubic cwnd:10 bytes_sent:50000 bytes_acked:48000 bytes_received:92000 pacing_rate 12.5Mbps delivery_rate 10.2Mbps send 11.0Mbps"
        samples = collector.parse_ss_output(raw)
        self.assertEqual(len(samples), 1)
        s = samples[0]
        self.assertEqual(s.bytes_sent, 50000)
        self.assertEqual(s.bytes_acked, 48000)
        self.assertEqual(s.bytes_received, 92000)
        self.assertEqual(s.pacing_rate_bps, 12.5e6)
        self.assertEqual(s.delivery_rate_bps, 10.2e6)
        self.assertEqual(s.send_rate_bps, 11.0e6)
        self.assertEqual(s.to_dict()["goodput_bytes"], 48000)

    def test_proc_net_tcp_parser(self):
        collector = TCPInfoCollector()
        sample_proc = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode                                                     
   0: 0100007F:1F90 0200007F:0050 01 00001000:00000000 00:00000000 00000003  1000        0 7076683 2 0000000000000000 20 0 0 48 24
"""
        samples = collector.parse_proc_tcp_content(sample_proc, node="test_node")
        self.assertEqual(len(samples), 1)
        s = samples[0]
        self.assertEqual(s.src_ip, "127.0.0.1")
        self.assertEqual(s.src_port, 8080)
        self.assertEqual(s.dst_ip, "127.0.0.2")
        self.assertEqual(s.dst_port, 80)
        self.assertEqual(s.state, "ESTABLISHED")
        self.assertEqual(s.snd_cwnd, 48)
        self.assertEqual(s.snd_ssthresh, 24)
        self.assertEqual(s.retrans, 3)
        self.assertEqual(s.node, "test_node")

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

        tracker = UDPJitterTracker()
        r1 = tracker.update("stream1", arrival_time=1.000, seq=1, transit_ms=20.0)
        self.assertEqual(r1["jitter_ms"], 0.0)
        r2 = tracker.update("stream1", arrival_time=1.025, seq=2, transit_ms=28.0)
        self.assertAlmostEqual(r2["jitter_ms"], 0.5)

    def test_dpi_raw_tcp_dissector(self):
        dissector = DPIDissector()
        eth = bytes([0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x00, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0x08, 0x00])
        ip_hdr = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 40, 100, 0x4000, 64, 6, 0,
                             bytes([10, 0, 1, 1]), bytes([10, 0, 2, 2]))
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

    def test_dpi_all_tcp_flags(self):
        dissector = DPIDissector()
        eth = bytes([0] * 12 + [0x08, 0x00])
        ip_hdr = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 40, 100, 0, 64, 6, 0,
                             bytes([10, 0, 0, 1]), bytes([10, 0, 0, 2]))
        # off_res with NS bit set (0x01) and flags with all 8 bits set (0xFF) -> all 9 flags
        off_res = (5 << 4) | 0x01
        flags_b = 0xFF
        tcp_hdr = struct.pack("!HHIIBBHHH", 443, 60000, 1234, 5678, off_res, flags_b, 32768, 0, 0)
        raw = eth + ip_hdr + tcp_hdr

        d = dissector.dissect_raw(raw)
        for expected_flag in ["NS", "CWR", "ECE", "URG", "ACK", "PSH", "RST", "SYN", "FIN"]:
            self.assertIn(expected_flag, d.flags)
            self.assertTrue(d.l4["flags_detail"][expected_flag])

    def test_dpi_raw_udp_dissector(self):
        dissector = DPIDissector()
        eth = bytes([0] * 12 + [0x08, 0x00])
        ip_hdr = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 36, 101, 0, 64, 17, 0,
                             bytes([192, 168, 1, 5]), bytes([192, 168, 1, 6]))
        udp_hdr = struct.pack("!HHHH", 5004, 5004, 16, 0)
        payload = b"RTP_DATA"
        raw = eth + ip_hdr + udp_hdr + payload

        d = dissector.dissect_raw(raw, ts=100.0)
        self.assertIsNotNone(d)
        self.assertEqual(d.l4["proto"], "UDP")
        self.assertEqual(d.payload_len, 8)
        self.assertEqual(d.l4["src_port"], 5004)
        self.assertEqual(d.app_summary, "RTP")

    def test_dpi_arp_dissector(self):
        dissector = DPIDissector()
        eth = bytes([0xFF] * 6 + [0x00, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE] + [0x08, 0x06])
        # ARP Request: htype=1, ptype=0x0800, hlen=6, plen=4, opcode=1
        arp_hdr = struct.pack("!HHBBH", 1, 0x0800, 6, 4, 1)
        sha = bytes([0x00, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE])
        spa = bytes([192, 168, 1, 100])
        tha = bytes([0] * 6)
        tpa = bytes([192, 168, 1, 1])
        raw = eth + arp_hdr + sha + spa + tha + tpa

        d = dissector.dissect_raw(raw)
        self.assertEqual(d.l3["proto"], "ARP")
        self.assertEqual(d.l3["opcode"], 1)
        self.assertEqual(d.l3["op_name"], "Request")
        self.assertEqual(d.l3["sender_ip"], "192.168.1.100")
        self.assertEqual(d.l3["target_ip"], "192.168.1.1")
        self.assertIn("Who has 192.168.1.1", d.summary)

    def test_dpi_ipv6_dissector(self):
        dissector = DPIDissector()
        eth = bytes([0] * 12 + [0x86, 0xDD])
        src_ip6 = bytes.fromhex("20010db8000000000000000000000001")
        dst_ip6 = bytes.fromhex("20010db8000000000000000000000002")
        # IPv6: version=6, tc=0, flow_label=0 -> ver_tc_fl = 0x60000000, payload_len=20, next_hdr=6 (TCP), hop_limit=64
        ip6_hdr = struct.pack("!IHBB16s16s", 0x60000000, 20, 6, 64, src_ip6, dst_ip6)
        tcp_hdr = struct.pack("!HHIIHHHH", 8080, 43210, 500, 100, (5 << 12) | 0x10, 65535, 0, 0)
        raw = eth + ip6_hdr + tcp_hdr

        d = dissector.dissect_raw(raw)
        self.assertEqual(d.l3["proto"], "IPv6")
        self.assertEqual(d.l3["version"], 6)
        self.assertEqual(d.l3["src_ip"], "2001:db8::1")
        self.assertEqual(d.l3["dst_ip"], "2001:db8::2")
        self.assertEqual(d.l4["proto"], "TCP")
        self.assertEqual(d.l4["src_port"], 8080)

    def test_dpi_icmp_dissector(self):
        dissector = DPIDissector()
        eth = bytes([0] * 12 + [0x08, 0x00])
        ip_hdr = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 28, 1, 0, 64, 1, 0,
                             bytes([10, 0, 0, 1]), bytes([10, 0, 0, 2]))
        # ICMP: type=8 (Echo Request), code=0, cksum=0xF7FD, id=42, seq=1 + payload b"PING"
        icmp_hdr = struct.pack("!BBHHH", 8, 0, 0xF7FD, 42, 1)
        raw = eth + ip_hdr + icmp_hdr + b"PING"

        d = dissector.dissect_raw(raw)
        self.assertEqual(d.l4["proto"], "ICMP")
        self.assertEqual(d.l4["type"], 8)
        self.assertEqual(d.l4["type_name"], "Echo Request")
        self.assertEqual(d.l4["id"], 42)
        self.assertEqual(d.l4["seq"], 1)

    def test_cisco_pdu_inspection_variations(self):
        dissector = DPIDissector()
        # Test Router processing
        res_router = dissector.build_cisco_pdu_inspection({
            "proto": "TCP",
            "src_ip": "10.0.1.5",
            "dst_ip": "10.0.2.10",
            "ttl": 64,
            "link": "eth0",
        }, device_name="CoreRouter", device_type="router")
        self.assertEqual(res_router["in_layers"]["l3"]["ttl"], 64)
        self.assertEqual(res_router["out_layers"]["l3"]["ttl"], 63)
        self.assertEqual(res_router["out_layers"]["l1"]["port"], "eth1 (Outbound Tx)")
        self.assertTrue(any("FIB lookup" in log for log in res_router["device_logs"]))

        # Test Switch processing
        res_switch = dissector.build_cisco_pdu_inspection({
            "proto": "TCP",
            "src_mac": "00:11:22:33:44:55",
            "dst_mac": "00:aa:bb:cc:dd:ee",
            "link": "eth0",
        }, device_name="AccessSwitch", device_type="switch")
        self.assertEqual(res_switch["in_layers"]["l3"]["ttl"], 64)
        self.assertEqual(res_switch["out_layers"]["l3"]["ttl"], 64)  # Switch does not decrement TTL
        self.assertTrue(any("CAM" in log for log in res_switch["device_logs"]))

        # Test ARP processing
        res_arp = dissector.build_cisco_pdu_inspection({
            "proto": "ARP",
            "link": "eth0",
        }, device_name="R1", device_type="router")
        self.assertTrue(any("ARP" in log for log in res_arp["device_logs"]))

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

    def test_telemetry_manager_ring_buffer(self):
        mgr = TelemetryManager(history_len=50)
        # Ingest 75 samples to verify ring buffer truncates to history_len
        for i in range(75):
            s = TCPInfoCollector.simulate_sample("10.0.0.1", 10000, "10.0.0.2", 80, step=i)
            mgr.ingest_tcp_sample(s)

        stream = mgr.get_stream_detail("10.0.0.1:10000->10.0.0.2:80")
        self.assertIsNotNone(stream)
        self.assertEqual(len(stream["samples"]), 50)
        self.assertIn("stats", stream)
        self.assertGreater(stream["stats"]["max_rtt_ms"], 0)

        stats = mgr.get_global_stats()
        self.assertEqual(stats["active_streams_count"], 1)
        self.assertEqual(stats["total_history_points"], 50)

        # Reset
        mgr.reset()
        self.assertEqual(len(mgr.get_active_streams()), 0)
        empty_stats = mgr.get_global_stats()
        self.assertEqual(empty_stats["active_streams_count"], 0)

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

            # Stream detail (raw and URL-encoded)
            res_detail = client.get(f"/api/telemetry/stream/{stream_id}").json()
            self.assertEqual(res_detail["stream_id"], stream_id)
            self.assertEqual(len(res_detail["samples"]), 5)

            encoded_id = stream_id.replace(":", "%3A").replace("->", "-%3E")
            res_detail_enc = client.get(f"/api/telemetry/stream/{encoded_id}").json()
            self.assertEqual(res_detail_enc["stream_id"], stream_id)

            # Stream detail 404
            res_404 = client.get("/api/telemetry/stream/nonexistent:1->2:3")
            self.assertEqual(res_404.status_code, 404)

            # Global stats
            res_stats = client.get("/api/telemetry/l4/stats").json()
            self.assertIn("active_streams_count", res_stats)
            self.assertGreaterEqual(res_stats["active_streams_count"], 1)

            # Drops with limit
            res_drops = client.get("/api/telemetry/l4/drops?limit=10").json()
            self.assertIn("drops", res_drops)

            # Dissect raw hex endpoint
            raw_hex = "00112233445500aabbccddee08004500002800644000400600000a0001010a0002020050d431000003e8000001f55012faf000000000"
            res_dissect = client.post("/api/telemetry/l4/dissect", json={"hex": raw_hex}).json()
            self.assertIn("layers", res_dissect)
            self.assertEqual(res_dissect["layers"]["l3"]["src_ip"], "10.0.1.1")
            self.assertEqual(res_dissect["layers"]["l4"]["proto"], "TCP")

            # Dissect base64 endpoint
            raw_b64 = base64.b64encode(bytes.fromhex(raw_hex)).decode("ascii")
            res_dissect_b64 = client.post("/api/telemetry/dissect", json={"base64": raw_b64}).json()
            self.assertEqual(res_dissect_b64["layers"]["l3"]["src_ip"], "10.0.1.1")

            # Dissect invalid input handling
            res_bad_hex = client.post("/api/telemetry/dissect", json={"hex": "not_hex_at_all"})
            self.assertEqual(res_bad_hex.status_code, 400)

            res_empty_body = client.post("/api/telemetry/dissect", json={})
            self.assertEqual(res_empty_body.status_code, 400)

            # Cisco PDU inspection endpoint
            res_pdu = client.post("/api/pdu/inspect", json={
                "packet": {"proto": "TCP", "summary": "TCP 10.0.1.10:45678 -> 10.0.2.20:80", "link": "eth0"},
                "device": "R1",
                "device_type": "router"
            }).json()
            self.assertIn("in_layers", res_pdu)
            self.assertIn("out_layers", res_pdu)
            self.assertIn("device_logs", res_pdu)
            self.assertEqual(res_pdu["in_layers"]["l3"]["ttl"], 64)
            self.assertEqual(res_pdu["out_layers"]["l3"]["ttl"], 63)
            self.assertGreater(len(res_pdu["device_logs"]), 3)

            # Queue telemetry endpoint
            res_queues = client.get("/api/telemetry/l4/queues").json()
            self.assertIn("queues", res_queues)
            self.assertIn("bufferbloat", res_queues)
            self.assertIn("summary", res_queues)

            # Bufferbloat report endpoint
            res_bb = client.get("/api/telemetry/l4/bufferbloat").json()
            self.assertIn("summary", res_bb)
            self.assertIn("assessments", res_bb)

    def test_tcp_zero_window_edge_case(self):
        """Zero window e'lon qilingan holatni (window = 0) DPI va ss parserda tekshirish."""
        dissector = DPIDissector()
        eth = bytes([0] * 12 + [0x08, 0x00])
        ip_hdr = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 40, 101, 0, 64, 6, 0,
                             bytes([10, 0, 1, 1]), bytes([10, 0, 2, 2]))
        # TCP header with window = 0 (Zero Window)
        tcp_hdr = struct.pack("!HHIIBBHHH", 80, 54321, 2000, 1000, (5 << 4), 0x10, 0, 0, 0)
        raw = eth + ip_hdr + tcp_hdr

        d = dissector.dissect_raw(raw)
        self.assertIsNotNone(d)
        self.assertEqual(d.l4["proto"], "TCP")
        self.assertEqual(d.l4["window"], 0)
        self.assertIn("Win=0", d.summary)

        # ss parser zero window
        collector = TCPInfoCollector()
        ss_zero_win = "ESTAB 0 0 10.0.1.1:80 10.0.2.2:54321 cubic cwnd:10 rcvspace:0 wscale:0,0"
        samples = collector.parse_ss_output(ss_zero_win)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].rcv_space, 0)

    def test_tcp_duplicate_acks_and_loss(self):
        """Takroriy ACKlar va paket yo'qotish (loss/recovery) dinamikasini tekshirish."""
        dissector = DPIDissector()
        eth = bytes([0] * 12 + [0x08, 0x00])
        ip_hdr = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 40, 102, 0, 64, 6, 0,
                             bytes([10, 0, 1, 1]), bytes([10, 0, 2, 2]))
        # Duplicate ACK: sport=54321, dport=80, seq=1000, ack=5000, ACK flag set
        tcp_hdr = struct.pack("!HHIIBBHHH", 54321, 80, 1000, 5000, (5 << 4), 0x10, 64240, 0, 0)
        raw = eth + ip_hdr + tcp_hdr

        # Dissect multiple identical ACKs
        d1 = dissector.dissect_raw(raw)
        d2 = dissector.dissect_raw(raw)
        self.assertEqual(d1.l4["ack"], 5000)
        self.assertEqual(d2.l4["ack"], 5000)
        self.assertEqual(d1.l4["seq"], d2.l4["seq"])
        self.assertEqual(d1.flags, ["ACK"])

        # Loss and recovery simulation in TCPInfoCollector
        loss_sample = TCPInfoCollector.simulate_sample("10.0.0.1", 1234, "10.0.0.2", 80, step=7, has_loss=True)
        self.assertEqual(loss_sample.ca_state, "recovery")
        self.assertEqual(loss_sample.retrans, 1)
        self.assertEqual(loss_sample.snd_cwnd, 10)

    def test_icmp_reply_and_unreachable_variations(self):
        """ICMP Echo Reply va Destination Unreachable paketlarini tekshirish."""
        dissector = DPIDissector()
        eth = bytes([0] * 12 + [0x08, 0x00])

        # 1. ICMP Echo Reply (type=0, code=0)
        ip_hdr_reply = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 28, 201, 0, 64, 1, 0,
                                   bytes([10, 0, 0, 2]), bytes([10, 0, 0, 1]))
        icmp_reply = struct.pack("!BBHHH", 0, 0, 0xFFFF, 42, 1)
        raw_reply = eth + ip_hdr_reply + icmp_reply + b"PONG"

        d_rep = dissector.dissect_raw(raw_reply)
        self.assertEqual(d_rep.l4["proto"], "ICMP")
        self.assertEqual(d_rep.l4["type"], 0)
        self.assertEqual(d_rep.l4["type_name"], "Echo Reply")

        # 2. ICMP Destination Unreachable (type=3, code=1: Host Unreachable)
        ip_hdr_unreach = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 28, 202, 0, 64, 1, 0,
                                     bytes([10, 0, 0, 254]), bytes([10, 0, 0, 1]))
        icmp_unreach = struct.pack("!BBHHH", 3, 1, 0, 0, 0)
        raw_unreach = eth + ip_hdr_unreach + icmp_unreach

        d_unr = dissector.dissect_raw(raw_unreach)
        self.assertEqual(d_unr.l4["proto"], "ICMP")
        self.assertEqual(d_unr.l4["type"], 3)
        self.assertEqual(d_unr.l4["type_name"], "Destination Unreachable")

        # 3. Cisco PDU inspection for ICMP packet
        pdu_icmp = dissector.build_cisco_pdu_inspection({
            "proto": "ICMP",
            "summary": "ICMP Echo Request 10.0.0.1 -> 10.0.0.2",
            "src_ip": "10.0.0.1",
            "dst_ip": "10.0.0.2",
            "link": "eth0",
        }, device_name="R1", device_type="router")
        self.assertEqual(pdu_icmp["proto"], "ICMP")
        self.assertEqual(pdu_icmp["in_layers"]["l3"]["ttl"], 64)
        self.assertEqual(pdu_icmp["out_layers"]["l3"]["ttl"], 63)
        self.assertTrue(any("FIB lookup" in log or "IPv4" in log for log in pdu_icmp["device_logs"]))

    def test_arp_reply_and_switch_inspection(self):
        """ARP Reply paketi va kommutator (switch) PDU tahlili tekshiruvi."""
        dissector = DPIDissector()
        eth = bytes([0x00, 0x11, 0x22, 0x33, 0x44, 0x55] + [0x00, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE] + [0x08, 0x06])
        # ARP Reply: htype=1, ptype=0x0800, hlen=6, plen=4, opcode=2 (Reply)
        arp_hdr = struct.pack("!HHBBH", 1, 0x0800, 6, 4, 2)
        sha = bytes([0x00, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE])
        spa = bytes([192, 168, 1, 1])
        tha = bytes([0x00, 0x11, 0x22, 0x33, 0x44, 0x55])
        tpa = bytes([192, 168, 1, 100])
        raw = eth + arp_hdr + sha + spa + tha + tpa

        d = dissector.dissect_raw(raw)
        self.assertEqual(d.l3["proto"], "ARP")
        self.assertEqual(d.l3["opcode"], 2)
        self.assertEqual(d.l3["op_name"], "Reply")
        self.assertIn("192.168.1.1 is at", d.summary)

        # Switch PDU inspection for ARP
        pdu_switch_arp = dissector.build_cisco_pdu_inspection({
            "proto": "ARP",
            "summary": "ARP Reply 192.168.1.1 is at 00:aa:bb:cc:dd:ee",
            "src_mac": "00:aa:bb:cc:dd:ee",
            "dst_mac": "00:11:22:33:44:55",
            "link": "eth1",
        }, device_name="SW1", device_type="switch")
        self.assertEqual(pdu_switch_arp["proto"], "ARP")
        self.assertTrue(any("ARP" in log for log in pdu_switch_arp["device_logs"]))

    def test_vlan_8021q_tagging(self):
        """802.1Q VLAN teglangan freymlarni to'g'ri dekonstruktsiya qilish tekshiruvi."""
        dissector = DPIDissector()
        # Ethertype = 0x8100, TCI = (priority=5 << 13) | vlan_id=200, inner ethertype = 0x0800 (IPv4)
        tci = (5 << 13) | 200
        eth = bytes([0] * 12) + struct.pack("!HH", 0x8100, tci) + struct.pack("!H", 0x0800)
        ip_hdr = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 28, 301, 0, 64, 17, 0,
                             bytes([192, 168, 200, 10]), bytes([192, 168, 200, 20]))
        udp_hdr = struct.pack("!HHHH", 9000, 9001, 8, 0)
        raw = eth + ip_hdr + udp_hdr

        d = dissector.dissect_raw(raw)
        self.assertIsNotNone(d.l2.get("vlan"))
        self.assertEqual(d.l2["vlan"]["vlan_id"], 200)
        self.assertEqual(d.l2["vlan"]["priority"], 5)
        self.assertEqual(d.l3["proto"], "IPv4")
        self.assertEqual(d.l3["src_ip"], "192.168.200.10")
        self.assertEqual(d.l4["proto"], "UDP")
        self.assertEqual(d.l4["src_port"], 9000)

    def test_udp_jitter_tracker_loss_and_reordering(self):
        """UDPJitterTracker: paket yo'qotish (loss) va tartibsiz yetib kelish (out-of-order)."""
        tracker = UDPJitterTracker()
        s_id = "test_udp_jitter_stream"

        # Packet 1 arrives
        r1 = tracker.update(s_id, arrival_time=1.000, seq=1, transit_ms=10.0)
        self.assertEqual(r1["lost_pkts"], 0)
        self.assertEqual(r1["total_pkts"], 1)

        # Packet 2 arrives (normal)
        r2 = tracker.update(s_id, arrival_time=1.020, seq=2, transit_ms=12.0)
        self.assertEqual(r2["lost_pkts"], 0)
        self.assertEqual(r2["total_pkts"], 2)

        # Packet 5 arrives next -> packets 3 and 4 were lost (gap of 2 packets)
        r3 = tracker.update(s_id, arrival_time=1.080, seq=5, transit_ms=15.0)
        self.assertEqual(r3["lost_pkts"], 2)
        self.assertEqual(r3["total_pkts"], 3)

        # Packet 3 arrives late (out of order: seq 3 < seq 5)
        r4 = tracker.update(s_id, arrival_time=1.090, seq=3, transit_ms=16.0)
        self.assertEqual(tracker._streams[s_id]["out_of_order"], 1)

    def test_ebpf_tracker_history_cap_and_resilience(self):
        """EBPFTracker: xotira chegarasi (500) va callback istisnolari barqarorligi."""
        tracker = EBPFTracker()
        # Callback that raises an exception should not break tracker emission
        tracker.register_callback(lambda ev: (_ for _ in ()).throw(RuntimeError("Callback failure")))

        for i in range(550):
            tracker.record_drop("10.0.0.1", 1000 + i, "10.0.0.2", 80, reason="TEST_DROP")

        recent = tracker.get_recent_drops(limit=1000)
        self.assertEqual(len(recent), 500)
        self.assertEqual(len(tracker._recent_events), 500)

    def test_api_simulate_and_drops_boundary(self):
        """FastAPI telemetriya APIlarining chegara parametrlari tekshiruvi."""
        app = create_app("demo")
        with TestClient(app) as client:
            # Clamping of simulate steps: negative/0 clamped to 1
            res_min = client.post("/api/telemetry/simulate", json={"steps": -5, "src_ip": "10.1.1.1"})
            self.assertEqual(res_min.status_code, 200)

            # Clamping of simulate steps: >500 clamped to 500
            res_max = client.post("/api/telemetry/simulate", json={"steps": 1000, "src_ip": "10.1.1.2"})
            self.assertEqual(res_max.status_code, 200)

            # Drops with limit > 200 (clamped to 200)
            res_drops_clamp = client.get("/api/telemetry/l4/drops?limit=500")
            self.assertEqual(res_drops_clamp.status_code, 200)

            # Drops with limit <= 0 (clamped to default 50)
            res_drops_zero = client.get("/api/telemetry/l4/drops?limit=-10")
            self.assertEqual(res_drops_zero.status_code, 200)


if __name__ == "__main__":
    unittest.main()
