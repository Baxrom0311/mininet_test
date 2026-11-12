import unittest
from fastapi.testclient import TestClient

from lab.server import create_app
from lab.telemetry.int_collector import (
    INTHeader,
    INTHopMetadata,
    INTReport,
    INTTelemetryCollector,
    INTSimulator,
    int_telemetry_collector,
)


class TestP4InBandTelemetry(unittest.TestCase):
    def setUp(self):
        self.collector = INTTelemetryCollector(max_reports=50)
        self.sim = INTSimulator(collector=self.collector)

    def test_int_hop_metadata_model(self):
        hop = INTHopMetadata(
            switch_id="s1",
            ingress_port=1,
            egress_port=2,
            hop_latency_us=14.5,
            queue_occupancy_pkts=8,
            queue_occupancy_bytes=12000,
            tx_utilization_pct=45.0,
        )
        d = hop.to_dict()
        self.assertEqual(d["switch_id"], "s1")
        self.assertEqual(d["ingress_port"], 1)
        self.assertEqual(d["egress_port"], 2)
        self.assertEqual(d["hop_latency_us"], 14.5)
        self.assertEqual(d["queue_occupancy_pkts"], 8)
        self.assertEqual(d["tx_utilization_pct"], 45.0)
        self.assertFalse(d["drop_flag"])

    def test_int_header_encode_decode_roundtrip(self):
        h1 = INTHopMetadata(
            switch_id="s1",
            ingress_port=1,
            egress_port=2,
            hop_latency_us=12.4,
            queue_occupancy_pkts=5,
            queue_occupancy_bytes=7500,
        )
        h2 = INTHopMetadata(
            switch_id="s2",
            ingress_port=2,
            egress_port=3,
            hop_latency_us=28.6,
            queue_occupancy_pkts=22,
            queue_occupancy_bytes=33000,
            drop_flag=False,
        )
        original_hdr = INTHeader(
            version=2,
            d_flag=False,
            q_flag=True,
            f_flag=False,
            max_hop_count=5,
            instruction_mask=0xAAAA,
            hops=[h1, h2],
        )

        encoded_bytes = original_hdr.encode()
        self.assertGreater(len(encoded_bytes), 8)
        # Shim (4B) + INT Header (4B) + 2 hops * 16B = 40 bayt
        self.assertEqual(len(encoded_bytes), 40)

        decoded_hdr = INTHeader.decode(encoded_bytes)
        self.assertEqual(decoded_hdr.version, 2)
        self.assertTrue(decoded_hdr.q_flag)
        self.assertFalse(decoded_hdr.d_flag)
        self.assertEqual(len(decoded_hdr.hops), 2)

        # 1-hop tekshiruvi
        self.assertEqual(decoded_hdr.hops[0].switch_id, "s1")
        self.assertEqual(decoded_hdr.hops[0].ingress_port, 1)
        self.assertEqual(decoded_hdr.hops[0].egress_port, 2)
        self.assertAlmostEqual(decoded_hdr.hops[0].hop_latency_us, 12.4, places=1)
        self.assertEqual(decoded_hdr.hops[0].queue_occupancy_pkts, 5)

        # 2-hop tekshiruvi
        self.assertEqual(decoded_hdr.hops[1].switch_id, "s2")
        self.assertAlmostEqual(decoded_hdr.hops[1].hop_latency_us, 28.6, places=1)
        self.assertEqual(decoded_hdr.hops[1].queue_occupancy_pkts, 22)

    def test_simulator_normal_path_traversal(self):
        path = ["s1", "s2", "s3"]
        header, report = self.sim.simulate_path(
            path=path,
            flow_id="test-flow-1",
            base_hop_latency_us=10.0,
            traffic_load_pct=25.0,
        )

        self.assertEqual(report.flow_id, "test-flow-1")
        self.assertEqual(report.total_hops, 3)
        self.assertEqual(report.path, ["s1", "s2", "s3"])
        self.assertGreater(report.end_to_end_latency_us, 30.0)
        self.assertFalse(report.microburst_detected)
        self.assertFalse(header.q_flag)
        self.assertEqual(len(header.hops), 3)

    def test_simulator_microburst_injection_and_detection(self):
        path = ["s1", "s2", "s3", "s4"]
        header, report = self.sim.simulate_path(
            path=path,
            flow_id="test-flow-burst",
            base_hop_latency_us=10.0,
            burst_switch="s2",
            burst_size_pkts=75,
            traffic_load_pct=50.0,
        )

        self.assertTrue(report.microburst_detected)
        self.assertTrue(header.q_flag)
        self.assertEqual(report.bottleneck_switch, "s2")
        self.assertGreater(report.max_queue_depth_pkts, 70)

        # s2 dagi kechikish boshqa switchlarga qaraganda ancha katta bo'lishi kerak
        s2_meta = next(h for h in report.hops if h.switch_id == "s2")
        s1_meta = next(h for h in report.hops if h.switch_id == "s1")
        self.assertGreater(s2_meta.hop_latency_us, s1_meta.hop_latency_us * 3)

    def test_collector_bottleneck_ranking_and_hotspots(self):
        collector = INTTelemetryCollector(max_reports=100)
        sim = INTSimulator(collector=collector)

        # 5 ta normal oqim
        for _ in range(5):
            sim.simulate_path(["s1", "s2", "s3"], burst_size_pkts=0)

        # 5 ta s2 da tiqilinch bo'lgan oqim
        for _ in range(5):
            sim.simulate_path(["s1", "s2", "s3"], burst_switch="s2", burst_size_pkts=60)

        analysis = collector.get_bottleneck_analysis()
        self.assertEqual(analysis["total_reports"], 10)
        self.assertIn("s2", analysis["switches"])

        ranked = analysis["ranked_bottlenecks"]
        self.assertGreater(len(ranked), 0)
        # Eng yuqori navbat va kechikishga ega bo'lgan s2 birinchi o'rinda bo'lishi kerak
        self.assertEqual(ranked[0]["switch_id"], "s2")
        self.assertTrue(ranked[0]["is_critical_bottleneck"])
        self.assertGreaterEqual(ranked[0]["microburst_count"], 5)


class TestP4INTServerEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = create_app()
        cls.client = TestClient(app)

    def setUp(self):
        int_telemetry_collector.clear()

    def test_p4_int_simulate_and_get_reports_endpoint(self):
        payload = {
            "path": ["s1", "s2", "s3"],
            "flow_id": "test-api-flow",
            "base_hop_latency_us": 15.0,
            "burst_switch": "s3",
            "burst_size_pkts": 80,
            "traffic_load_pct": 40.0,
        }
        resp = self.client.post("/api/telemetry/p4/int/simulate", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["report"]["flow_id"], "test-api-flow")
        self.assertTrue(data["report"]["microburst_detected"])
        self.assertEqual(data["report"]["bottleneck_switch"], "s3")

        # GET /api/telemetry/p4/int/reports
        resp_rep = self.client.get("/api/telemetry/p4/int/reports?limit=10")
        self.assertEqual(resp_rep.status_code, 200)
        reports = resp_rep.json()["reports"]
        self.assertGreaterEqual(len(reports), 1)
        self.assertEqual(reports[0]["flow_id"], "test-api-flow")

    def test_p4_int_bottlenecks_endpoint(self):
        # Bir nechta simulyatsiya o'tkazamiz
        self.client.post(
            "/api/telemetry/p4/int/simulate",
            json={"path": ["s1", "s2"], "burst_switch": "s1", "burst_size_pkts": 50},
        )
        resp = self.client.get("/api/telemetry/p4/int/bottlenecks")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("switches", data)
        self.assertIn("ranked_bottlenecks", data)
        self.assertGreaterEqual(data["total_reports"], 1)

    def test_p4_int_decode_endpoint(self):
        # Haqiqiy INT header encode qilib, uning hex ko'rinishini yuboramiz
        h = INTHopMetadata(switch_id="s1", ingress_port=1, egress_port=2, hop_latency_us=25.0)
        hdr = INTHeader(hops=[h])
        hex_data = hdr.encode().hex()

        resp = self.client.post("/api/telemetry/p4/int/decode", json={"hex": hex_data})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["header"]["hops_count"], 1)
        self.assertEqual(data["header"]["hops"][0]["switch_id"], "s1")

        # Noto'g'ri hex so'rovi
        resp_err = self.client.post("/api/telemetry/p4/int/decode", json={"hex": "invalid_hex"})
        self.assertEqual(resp_err.status_code, 400)


if __name__ == "__main__":
    unittest.main()
