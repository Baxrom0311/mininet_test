import unittest
from fastapi.testclient import TestClient

from lab.server import create_app
from lab.telemetry.comparison import TelemetryComparator
from lab.telemetry.manager import TelemetryManager


class TestTelemetryComparison(unittest.TestCase):
    def test_compare_streams_cubic_vs_bbr(self):
        """Oqimlar solishtirilganda delta ko'rsatkichlar va g'olib to'g'ri aniqlanishi kerak."""
        stream_cubic = {
            "stream_id": "flow_cubic",
            "congestion_algo": "cubic",
            "delivery_rate_bps": 5_000_000,
            "rtt_ms": 65.0,
            "rttvar_ms": 12.0,
            "total_retrans": 8,
            "cwnd": 25,
            "bytes_acked": 2_000_000,
        }
        stream_bbr = {
            "stream_id": "flow_bbr",
            "congestion_algo": "bbr",
            "delivery_rate_bps": 9_500_000,
            "rtt_ms": 32.0,
            "rttvar_ms": 2.5,
            "total_retrans": 1,
            "cwnd": 35,
            "bytes_acked": 4_000_000,
        }

        res = TelemetryComparator.compare_streams(stream_cubic, stream_bbr, label_a="CUBIC", label_b="BBR")
        self.assertEqual(res["algo_a"], "CUBIC")
        self.assertEqual(res["algo_b"], "BBR")
        self.assertEqual(res["overall_winner"], "candidate")
        self.assertIn("BBR", res["verdict"]["uz"])

        # Metrikalar tekshiruvi
        metrics_by_name = {m["metric"]: m for m in res["metrics"]}
        self.assertIn("Yetkazib berish tezligi (Throughput)", metrics_by_name)
        thr_m = metrics_by_name["Yetkazib berish tezligi (Throughput)"]
        self.assertEqual(thr_m["winner"], "candidate")
        self.assertGreater(thr_m["delta"], 0)

        rtt_m = metrics_by_name["Kechikish (Smoothed RTT)"]
        self.assertEqual(rtt_m["winner"], "candidate")
        self.assertLess(rtt_m["delta"], 0)  # BBR ning kechikishi pastroq

    def test_compare_algorithms_analytic_model(self):
        """Tarmoqda paket yo'qotish bo'lganda (1% loss), BBR CUBIC dan ancha yuqori tezlikka ega bo'lishi kerak."""
        res = TelemetryComparator.compare_algorithms(
            algo_a="cubic",
            algo_b="bbr",
            bottleneck_bps=20_000_000.0,
            base_rtt_ms=30.0,
            loss_pct=1.5,
        )
        self.assertEqual(res["overall_winner"], "candidate")
        self.assertIn("simulation_parameters", res)
        self.assertEqual(res["simulation_parameters"]["packet_loss_pct"], 1.5)

    def test_compare_queuing_bufferbloat_mitigation(self):
        """FQ-CoDel ortiqcha yuklama sharoitida FIFO dan kamroq sojourn time va yuqori VoIP MOS berishi kerak."""
        res = TelemetryComparator.compare_queuing(
            qdisc_a="pfifo_fast",
            qdisc_b="fq_codel",
            link_bps=10_000_000.0,
            load_pct=130.0,
        )
        comp = res["comparison"]
        self.assertTrue(comp["bufferbloat_detected"]["baseline"])
        self.assertFalse(comp["bufferbloat_detected"]["candidate"])
        self.assertGreater(comp["sojourn_time_ms"]["reduction_pct"], 80.0)
        self.assertGreater(comp["voip_mos"]["candidate"], comp["voip_mos"]["baseline"])

    def test_api_compare_endpoints(self):
        """FastAPI solishtirish endpointlari (HTTP 200) javob berishi kerak."""
        app = create_app()
        client = TestClient(app)

        # 1. Algorithms comparison
        res1 = client.get("/api/telemetry/l4/compare/algorithms?algo_a=cubic&algo_b=bbr&loss_pct=2.0")
        self.assertEqual(res1.status_code, 200)
        data1 = res1.json()
        self.assertIn("overall_winner", data1)
        self.assertIn("metrics", data1)

        # 2. Queues comparison
        res2 = client.get("/api/telemetry/l4/compare/queues?qdisc_a=pfifo_fast&qdisc_b=fq_codel&load_pct=140")
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        self.assertIn("recommendation", data2)
        self.assertTrue(data2["comparison"]["bufferbloat_detected"]["baseline"])

        # 3. Streams comparison
        res3 = client.post("/api/telemetry/l4/compare/streams", json={"stream_a": "s1", "stream_b": "s2"})
        self.assertEqual(res3.status_code, 200)
        data3 = res3.json()
        self.assertIn("overall_winner", data3)


if __name__ == "__main__":
    unittest.main()
