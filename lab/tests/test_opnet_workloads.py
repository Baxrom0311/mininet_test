import unittest
from lab.engines.opnet_workloads import (
    AdaptiveVideoEngine,
    QUICStreamSimulator,
    VoIPQualityEvaluator,
)


class TestOPNETWorkloads(unittest.TestCase):
    def test_voip_evaluator_ideal_conditions(self):
        evaluator = VoIPQualityEvaluator()
        # Ideal sharoit: latency=10ms, jitter=0ms, loss=0%
        res = evaluator.evaluate(latency_ms=10.0, jitter_ms=0.0, packet_loss_pct=0.0, codec="g711")
        self.assertGreaterEqual(res.r_factor, 90.0)
        self.assertGreaterEqual(res.mos, 4.3)
        self.assertIn("A'lo", res.quality_category)
        self.assertEqual(res.equipment_impairment, 0.0)

    def test_voip_evaluator_degraded_conditions(self):
        evaluator = VoIPQualityEvaluator()
        # Yuqori kechikish va yo'qotish: latency=220ms, jitter=25ms, loss=8%
        res = evaluator.evaluate(latency_ms=220.0, jitter_ms=25.0, packet_loss_pct=8.0, codec="g729")
        self.assertLess(res.r_factor, 65.0)
        self.assertLess(res.mos, 3.5)
        self.assertGreater(res.delay_impairment, 10.0)
        self.assertGreater(res.equipment_impairment, 15.0)

    def test_adaptive_video_engine(self):
        engine = AdaptiveVideoEngine(chunk_duration_s=2.0)
        # O'zgaruvchan tezlik: avval yuqori (8000 kbps), keyin tushadi (800 kbps), keyin qayta ko'tariladi
        trace = [8000.0] * 5 + [800.0] * 5 + [10000.0] * 5
        summary, chunks = engine.simulate_stream(trace)

        self.assertEqual(len(chunks), 15)
        self.assertEqual(summary.total_chunks, 15)
        self.assertGreater(summary.quality_switches, 0)
        self.assertGreater(summary.qoe_score, 1.5)
        self.assertIn("1080p", summary.resolution_distribution)

    def test_quic_stream_simulator(self):
        sim = QUICStreamSimulator()
        # 4 ta parallel oqim (masalan: HTML, 2 ta CSS/JS, 1 ta Rasm)
        sizes = [15000, 45000, 80000, 150000]
        res = sim.compare_transfer(
            stream_sizes_bytes=sizes,
            rtt_ms=40.0,
            bandwidth_mbps=20.0,
            packet_loss_pct=3.0,
            use_0rtt=True,
        )

        self.assertEqual(res.total_streams, 4)
        # 3% yo'qotish va 0-RTT da QUIC TCP ga qaraganda tezroq bo'lishi kerak
        self.assertGreater(res.quic_speedup_pct, 0.0)
        self.assertGreater(res.total_hol_blocking_ms, 0.0)
        self.assertLess(res.total_quic_time_ms, res.total_tcp_time_ms)


if __name__ == "__main__":
    unittest.main()
