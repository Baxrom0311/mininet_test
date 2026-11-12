import unittest
from fastapi.testclient import TestClient

from lab.server import create_app
from lab.telemetry.opnet_workloads import (
    AdaptiveVideoSession,
    SLAMonitor,
    SLARule,
    VoIPMOSCalculator,
    generate_latex_report,
)
from lab.telemetry.manager import TelemetryManager


class TestOpnetWorkloadsAndSLA(unittest.TestCase):
    def test_voip_g711_excellent_condition(self):
        """Minimal delay (10ms), no jitter, no loss -> MOS >= 4.2 bo'lishi kerak."""
        res = VoIPMOSCalculator.calculate(delay_ms=10.0, jitter_ms=1.0, loss_pct=0.0, codec="G.711")
        self.assertGreaterEqual(res.mos, 4.2)
        self.assertLessEqual(res.mos, 4.5)
        self.assertIn("A'lo", res.rating)
        self.assertGreater(res.r_factor, 85.0)

    def test_voip_g711_high_loss_degradation(self):
        """Katta yo'qotish (10% packet loss) -> MOS sezilarli pasayishi kerak."""
        res_good = VoIPMOSCalculator.calculate(delay_ms=20.0, jitter_ms=2.0, loss_pct=0.0, codec="G.711")
        res_bad = VoIPMOSCalculator.calculate(delay_ms=20.0, jitter_ms=2.0, loss_pct=10.0, codec="G.711")
        self.assertLess(res_bad.mos, res_good.mos)
        self.assertLess(res_bad.r_factor, res_good.r_factor)
        self.assertGreater(res_bad.impairment_loss, 20.0)

    def test_voip_g729_codec(self):
        """G.729 pastroq boshlang'ich sifatga ega (siqilgan 8kbps kodek)."""
        res = VoIPMOSCalculator.calculate(delay_ms=30.0, jitter_ms=3.0, loss_pct=1.0, codec="G.729")
        self.assertGreaterEqual(res.mos, 3.5)
        self.assertLessEqual(res.mos, 4.3)
        self.assertEqual(res.codec, "G.729")

    def test_adaptive_video_streaming_ladder(self):
        """ABR video: tarmoq tezligi pasayganda avtomatik sifat pasayishi kerak."""
        session = AdaptiveVideoSession("test_hls_session")
        # 1. Katta tezlik (10 Mbps) -> 1080p ga ko'tariladi
        p1 = session.update(measured_bandwidth_bps=10_000_000)
        self.assertEqual(p1["quality"], "1080p")
        self.assertGreaterEqual(p1["buffer_sec"], 5.0)

        # 2. Tezlik keskin tushdi (400 Kbps) va bufer kamaydi
        for _ in range(5):
            p2 = session.update(measured_bandwidth_bps=400_000)

        # Sifat 360p yoki 240p ga tushgan bo'lishi kerak
        self.assertIn(p2["quality"], ["480p", "360p", "240p"])
        self.assertGreater(session.quality_switches, 0)

    def test_sla_monitor_evaluation_and_violations(self):
        """SLA qoidalarini tekshirish va buzilishlarni qayd qilish."""
        rule = SLARule(name="Test SLA", max_rtt_ms=40.0, max_jitter_ms=8.0, max_loss_pct=1.0)
        monitor = SLAMonitor(default_rule=rule)

        # 1. Normal oqim -> No violations
        v1 = monitor.evaluate_stream("s1", rtt_ms=20.0, jitter_ms=2.0, loss_pct=0.0, throughput_bps=5_000_000)
        self.assertEqual(len(v1), 0)

        # 2. RTT va Jitter oshib ketdi -> Violations
        v2 = monitor.evaluate_stream("s2", rtt_ms=90.0, jitter_ms=25.0, loss_pct=5.0, throughput_bps=500_000)
        self.assertGreaterEqual(len(v2), 2)
        metrics = [v.metric for v in v2]
        self.assertIn("rtt_ms", metrics)
        self.assertIn("jitter_ms", metrics)

        summary = monitor.get_summary()
        self.assertEqual(summary["total_evaluations"], 2)
        self.assertLess(summary["compliance_pct"], 100.0)
        self.assertGreater(summary["total_violations"], 0)

    def test_latex_report_generation(self):
        """LaTeX hisobotida IEEE shablon elementlari bo'lishi kerak."""
        latex = generate_latex_report(title="SDN Benchmark Test")
        self.assertIn(r"\documentclass[conference]{IEEEtran}", latex)
        self.assertIn("SDN Benchmark Test", latex)
        self.assertIn(r"\section{System and Topology Configuration}", latex)
        self.assertIn(r"\section{VoIP QoE and Workload Evaluation}", latex)
        self.assertIn(r"\end{document}", latex)

    def test_fastapi_opnet_workload_and_sla_endpoints(self):
        """FastAPI OPNET workload va SLA API endpointlarini tekshirish."""
        app = create_app("demo")
        with TestClient(app) as client:
            # 1. VoIP endpoint
            r_voip = client.get("/api/telemetry/l4/workloads/voip?delay_ms=25&loss_pct=1.0&codec=G.711")
            self.assertEqual(r_voip.status_code, 200)
            data_voip = r_voip.json()
            self.assertIn("mos", data_voip)
            self.assertIn("r_factor", data_voip)
            self.assertGreater(data_voip["mos"], 3.5)

            # 2. Video endpoint
            r_video = client.get("/api/telemetry/l4/workloads/video")
            self.assertEqual(r_video.status_code, 200)
            data_video = r_video.json()
            self.assertIn("current_quality", data_video)
            self.assertIn("buffer_sec", data_video)

            # 3. SLA endpoint
            r_sla = client.get("/api/telemetry/l4/sla")
            self.assertEqual(r_sla.status_code, 200)
            data_sla = r_sla.json()
            self.assertIn("compliance_pct", data_sla)
            self.assertIn("total_evaluations", data_sla)

            # 4. LaTeX report endpoint
            r_latex = client.get("/api/telemetry/l4/report/latex?title=Mininet+Research+Evaluation")
            self.assertEqual(r_latex.status_code, 200)
            self.assertIn("IEEEtran", r_latex.text)
            self.assertIn("Mininet Research Evaluation", r_latex.text)


if __name__ == "__main__":
    unittest.main()
