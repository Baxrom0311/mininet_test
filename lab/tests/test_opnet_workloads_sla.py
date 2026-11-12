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


class TestAppsVideoAndHTTP3Workload(unittest.TestCase):
    def setUp(self):
        from lab.topogen import generate
        self.project = generate("ring", routers=3, hosts=1, flows=0)

    def test_model_types_membership(self):
        from lab.apps import MODEL_TYPES
        self.assertIn("video", MODEL_TYPES)
        self.assertIn("http3", MODEL_TYPES)

    def test_video_validation(self):
        from lab.apps import validate_app
        self.assertEqual(validate_app({"type": "video", "quality": "1080p"}, 10), [])
        self.assertEqual(validate_app({"type": "video", "quality": "720p", "target_buffer": 20}, 10), [])
        self.assertEqual(validate_app({"type": "video", "quality": "480p"}, 10), [])
        self.assertEqual(validate_app({"type": "video", "quality": "360p"}, 10), [])
        # Invalid quality
        self.assertTrue(len(validate_app({"type": "video", "quality": "4k"}, 10)) > 0)
        # Invalid target buffer
        self.assertTrue(len(validate_app({"type": "video", "target_buffer": -1}, 10)) > 0)

    def test_http3_validation(self):
        from lab.apps import validate_app
        self.assertEqual(validate_app({"type": "http3", "streams": 4, "rate": "4Mbps"}, 10), [])
        self.assertEqual(validate_app({"type": "http3", "streams": 8}, 10), [])
        # Invalid streams
        self.assertTrue(len(validate_app({"type": "http3", "streams": 0}, 10)) > 0)
        # Invalid rate
        self.assertTrue(len(validate_app({"type": "http3", "rate": "0bps"}, 10)) > 0)

    def test_video_expansion_and_scenario_validation(self):
        from lab.apps import expand
        from lab.model import validate_scenario
        sc = {
            "duration": 10,
            "events": [],
            "apps": [{"id": "vid1", "type": "video", "src": "h0x0", "dst": "h1x0", "quality": "1080p", "start": 1, "stop": 9}],
        }
        self.assertEqual(validate_scenario(self.project, sc), [])
        primitive = expand(sc)
        self.assertEqual(validate_scenario(self.project, primitive), [])
        self.assertEqual(len(primitive["apps"]), 1)
        expanded = primitive["apps"][0]
        self.assertEqual(expanded["id"], "vid1.stream")
        self.assertEqual(expanded["type"], "tcp")
        self.assertEqual(expanded["group"], "vid1")
        self.assertEqual(expanded["role"], "video")

    def test_http3_expansion_and_scenario_validation(self):
        from lab.apps import expand
        from lab.model import validate_scenario
        sc = {
            "duration": 10,
            "events": [],
            "apps": [{"id": "h3_app", "type": "http3", "src": "h0x0", "dst": "h1x0", "streams": 4, "rate": "6Mbps", "start": 1, "stop": 8}],
        }
        self.assertEqual(validate_scenario(self.project, sc), [])
        primitive = expand(sc)
        self.assertEqual(validate_scenario(self.project, primitive), [])
        self.assertEqual(len(primitive["apps"]), 4)
        for i, s_app in enumerate(primitive["apps"], start=1):
            self.assertEqual(s_app["id"], f"h3_app.s{i}")
            self.assertEqual(s_app["type"], "udp")
            self.assertEqual(s_app["group"], "h3_app")
            self.assertTrue(s_app["zero_rtt"])

    def test_p1203_qoe_calculation(self):
        from lab.apps import calculate_p1203
        qoe_top = calculate_p1203("1080p", total_duration=30, stall_count=0, total_stall_sec=0)
        self.assertGreaterEqual(qoe_top, 4.5)
        self.assertLessEqual(qoe_top, 5.0)

        qoe_low = calculate_p1203("360p", total_duration=30, stall_count=0, total_stall_sec=0)
        self.assertLess(qoe_low, qoe_top)

        qoe_stalled = calculate_p1203("1080p", total_duration=30, stall_count=3, total_stall_sec=10.0)
        self.assertLess(qoe_stalled, 3.0)
        self.assertGreaterEqual(qoe_stalled, 1.0)

    def test_video_postprocessing_with_stalls_and_adaptation(self):
        from lab.apps import postprocess, VIDEO_QUALITIES
        sc = {
            "duration": 4,
            "apps": [{"id": "vod", "type": "video", "src": "h0x0", "dst": "h1x0", "quality": "720p", "initial_buffer": 1.0, "start": 0, "stop": 4}],
        }
        series = [
            {"t": 1.0, "thr_bps": 500_000, "delay_ms": 25.0, "jitter_ms": 2.0, "loss_pct": 0.0},
            {"t": 2.0, "thr_bps": 100_000, "delay_ms": 60.0, "jitter_ms": 5.0, "loss_pct": 2.0},
            {"t": 3.0, "thr_bps": 50_000, "delay_ms": 120.0, "jitter_ms": 15.0, "loss_pct": 8.0},
            {"t": 4.0, "thr_bps": 5_000_000, "delay_ms": 15.0, "jitter_ms": 1.0, "loss_pct": 0.0},
        ]
        flows = {"vod.stream": {"id": "vod.stream", "group": "vod", "series": series}}
        res = postprocess({"duration": 4, "flows": flows}, sc)

        self.assertIn("vod", res["apps"])
        vod_app = res["apps"]["vod"]
        self.assertEqual(vod_app["type"], "video")
        self.assertIn("qoe_score", vod_app)
        self.assertIn("stall_count", vod_app)
        self.assertIn("total_stall_sec", vod_app)
        self.assertGreater(vod_app["stall_count"], 0)
        self.assertGreater(vod_app["total_stall_sec"], 0.0)

        vod_flow = res["flows"]["vod"]
        for pt in vod_flow["series"]:
            self.assertIn(pt["quality"], VIDEO_QUALITIES)
            self.assertIn("buffer_sec", pt)

    def test_http3_postprocessing(self):
        from lab.apps import postprocess
        sc = {
            "duration": 2,
            "apps": [{"id": "h3", "type": "http3", "src": "h0x0", "dst": "h1x0", "streams": 2, "start": 0, "stop": 2}],
        }
        flows = {
            "h3.s1": {"id": "h3.s1", "group": "h3", "series": [{"t": 1.0, "thr_bps": 1_000_000, "delay_ms": 10.0, "jitter_ms": 1.0, "loss_pct": 0.0}]},
            "h3.s2": {"id": "h3.s2", "group": "h3", "series": [{"t": 1.0, "thr_bps": 1_500_000, "delay_ms": 12.0, "jitter_ms": 1.2, "loss_pct": 0.0}]},
        }
        res = postprocess({"duration": 2, "flows": flows}, sc)
        self.assertIn("h3", res["apps"])
        h3_sum = res["apps"]["h3"]
        self.assertEqual(h3_sum["streams"], 2)
        self.assertEqual(h3_sum["avg_thr_bps"], 2_500_000)
        self.assertTrue(h3_sum["zero_rtt"])


class TestSLAComplianceAndEndpoint(unittest.TestCase):
    def test_evaluate_sla_compliance_and_violations(self):
        from lab.apps import evaluate_sla
        run = {
            "flows": {
                "f1": {
                    "series": [
                        {"t": 1.0, "thr_bps": 3_000_000, "delay_ms": 25.0, "jitter_ms": 3.0, "loss_pct": 0.1},
                        {"t": 2.0, "thr_bps": 400_000, "delay_ms": 120.0, "jitter_ms": 25.0, "loss_pct": 5.0},
                    ]
                }
            },
            "apps": {
                "voip1": {"avg_mos": 2.8}
            }
        }
        sla_spec = {
            "min_throughput_bps": 1_000_000,
            "max_delay_ms": 50.0,
            "max_jitter_ms": 10.0,
            "max_loss_pct": 1.0,
            "min_mos": 4.0,
        }
        res = evaluate_sla(run, sla_spec)
        self.assertLess(res["compliance_pct"], 100.0)
        self.assertGreater(res["total_violations"], 0)
        self.assertFalse(res["is_compliant"])
        self.assertIn("summary_by_metric", res)
        self.assertIn("summary_by_flow", res)

        # Check violation events structure
        v0 = res["violations"][0]
        self.assertIn("timestamp", v0)
        self.assertIn("flow_id", v0)
        self.assertIn("metric", v0)
        self.assertIn("value", v0)
        self.assertIn("threshold", v0)
        self.assertIn("severity", v0)

    def test_api_analysis_sla_endpoint(self):
        from lab.server import create_app
        app = create_app("demo")
        with TestClient(app) as client:
            run_payload = {
                "run": {
                    "flows": {
                        "test_flow": {
                            "series": [
                                {"t": 1.0, "thr_bps": 2_000_000, "delay_ms": 20.0, "jitter_ms": 2.0, "loss_pct": 0.0},
                                {"t": 2.0, "thr_bps": 2_000_000, "delay_ms": 25.0, "jitter_ms": 2.5, "loss_pct": 0.0},
                            ]
                        }
                    }
                },
                "sla": {
                    "min_throughput_bps": 1_000_000,
                    "max_delay_ms": 50.0,
                    "max_loss_pct": 1.0,
                }
            }
            resp = client.post("/api/analysis/sla", json=run_payload)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["compliance_pct"], 100.0)
            self.assertEqual(data["total_violations"], 0)
            self.assertTrue(data["is_compliant"])

            # 400 when missing run and project
            bad_resp = client.post("/api/analysis/sla", json={"sla": {}})
            self.assertEqual(bad_resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
