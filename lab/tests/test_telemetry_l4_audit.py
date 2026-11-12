"""Comprehensive Verification and Audit Test Suite for L4 Transport Observatory,
Telemetry, Linux Qdisc (CAKE, fq_codel, pfifo), Application Workloads (ITU-T G.107 VoIP, ABR Video),
SLA & A/B Engine, LaTeX Publication Reports, and WebSocket Telemetry.
"""

from __future__ import annotations

import unittest
from fastapi.testclient import TestClient

from lab.server import create_app
from lab.telemetry.tcp_info_collector import TCPInfoCollector, TCPInfoSample, parse_rate_string
from lab.telemetry.queue_tracker import QueueTracker, QueueStats, BufferbloatAssessment
from lab.telemetry.opnet_workloads import (
    AdaptiveVideoSession,
    SLAMonitor,
    SLARule,
    VoIPMOSCalculator,
    generate_latex_report,
)
from lab.telemetry.comparison import TelemetryComparator
from lab.telemetry.manager import TelemetryManager, telemetry_manager


class TestL4TelemetryAudit(unittest.TestCase):
    """L4 Transport Observatory and Socket Metrics Verification."""

    def setUp(self):
        self.collector = TCPInfoCollector()

    def test_tcp_metrics_extraction_full(self):
        """TCP soket parametrlari: snd_cwnd, ssthresh, rtt, rttvar, delivery_rate, pacing_rate, ca_state."""
        sample_output = """
ESTAB 0 0 10.0.1.15:52134 10.0.2.25:443
\t cubic open wscale:7,7 rto:200 rtt:8.45/1.25 ato:40 mss:1460 rcvspace:65535 ssthresh:48 cwnd:64 bytes_acked:584000 delivery_rate 42.5Mbps pacing_rate 51.0Mbps send 44.0Mbps retrans:0/0
        """
        samples = self.collector.parse_ss_output(sample_output, now=2000.0)
        self.assertEqual(len(samples), 1)
        s = samples[0]
        self.assertEqual(s.snd_cwnd, 64)
        self.assertEqual(s.snd_ssthresh, 48)
        self.assertAlmostEqual(s.rtt_ms, 8.45)
        self.assertAlmostEqual(s.rttvar_ms, 1.25)
        self.assertEqual(s.delivery_rate_bps, 42.5e6)
        self.assertEqual(s.pacing_rate_bps, 51.0e6)
        self.assertEqual(s.ca_state, "open")
        self.assertEqual(s.congestion_algo, "cubic")

        d = s.to_dict()
        self.assertEqual(d["snd_cwnd"], 64)
        self.assertEqual(d["delivery_rate_bps"], 42.5e6)
        self.assertEqual(d["pacing_rate_bps"], 51.0e6)
        self.assertEqual(d["ca_state"], "open")

    def test_bbr_socket_metrics_parsing(self):
        """Linux Kernel BBR chiqishi: bbr:(bw:25Mbps,mrtt:10.2) va min_rtt to'g'ri o'qilishi kerak."""
        bbr_output = """
ESTAB 0 0 10.0.1.10:49152 10.0.2.20:80
\t bbr open rtt:12.4/0.8 bbr:(bw:25.0Mbps,mrtt:10.2) pacing_rate 30.0Mbps cwnd:32
        """
        samples = self.collector.parse_ss_output(bbr_output)
        self.assertEqual(len(samples), 1)
        s = samples[0]
        self.assertEqual(s.congestion_algo, "bbr")
        self.assertEqual(s.delivery_rate_bps, 25.0e6)
        self.assertAlmostEqual(s.min_rtt_ms, 10.2)
        self.assertAlmostEqual(s.rtt_ms, 12.4)

    def test_tcp_zero_rtt_and_divide_by_zero_resilience(self):
        """Nol RTT yoki kichik RTT da bo'lishda xatolik (ZeroDivisionError) chiqmasligi kerak."""
        zero_rtt_output = """
ESTAB 0 0 127.0.0.1:4000 127.0.0.1:5000
\t cubic cwnd:10 rtt:0.0/0.0 retrans:0
        """
        samples = self.collector.parse_ss_output(zero_rtt_output)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].rtt_ms, 0.0)

        # Synthetic generator zero/low RTT resilience
        syn = self.collector.generate_synthetic_stream("test_stream", t=1.0, base_rtt_ms=0.0)
        self.assertGreater(syn["delivery_rate_bps"], 0.0)
        self.assertGreater(syn["rtt_ms"], 0.0)


class TestLinuxQdiscCAKEAndQueueTracker(unittest.TestCase):
    """Linux Qdisc CAKE, fq_codel, pfifo va Bufferbloat monitoring audit tekshiruvi."""

    def setUp(self):
        self.tracker = QueueTracker()

    def test_cake_qdisc_parsing_and_drop_tracking(self):
        """Linux sch_cake chiqishini, Cobalt AQM drop turlarini (unforced, forced, tail, ack) to'liq tahlil qilish."""
        cake_output = """
qdisc cake 8001: dev eth0 root refcnt 2 bandwidth 20Mbit diffserv3 triple-isolate nonat nowash no-ack-filter split-gso rtt 100ms raw
 Sent 2048000 bytes 1400 pkt (dropped 25, overlimits 0 requeues 0)
 backlog 5840b 4p requeues 0
 memory used: 32Kb of 4Mb
 capacity estimate: 20Mbit
 min netlen: 60
 max netlen: 1514
 avg netlen: 128
 min tail dropped: 0
 max tail dropped: 2
 min unforced dropped: 5
 max unforced dropped: 15
 min forced dropped: 0
 max forced dropped: 3
 min ecn marked: 0
 max ecn marked: 8
 min ack dropped: 0
 max ack dropped: 0
 av_delay 2.8ms
 peak_delay 4.5ms
        """
        stats_list = self.tracker.parse_tc_qdisc_output(cake_output, dev="eth0")
        self.assertEqual(len(stats_list), 1)
        qs = stats_list[0]

        self.assertEqual(qs.kind, "cake")
        self.assertEqual(qs.dev, "eth0")
        self.assertEqual(qs.rate_bps, 20_000_000.0)
        self.assertEqual(qs.params.get("diffserv3"), True)
        self.assertEqual(qs.params.get("triple-isolate"), True)
        self.assertAlmostEqual(qs.target_ms, 100.0)

        # Drop tracking
        self.assertEqual(qs.dropped, 25)
        self.assertEqual(qs.early_drops, 20)  # min 5 + max 15 unforced
        self.assertEqual(qs.ecn_marks, 8)
        self.assertEqual(qs.extra_stats.get("cake_unforced_drops"), 20)
        self.assertEqual(qs.extra_stats.get("cake_forced_drops"), 3)
        self.assertEqual(qs.extra_stats.get("cake_tail_drops"), 2)
        self.assertAlmostEqual(qs.extra_stats.get("av_delay_ms"), 2.8)
        self.assertAlmostEqual(qs.ldelay_ms, 2.8)
        self.assertTrue(qs.dropping)

        # Bufferbloat assessment for CAKE
        assess = self.tracker.assess_bufferbloat(qs)
        self.assertEqual(assess.qdisc_kind, "cake")
        self.assertEqual(assess.early_drops, 20)
        self.assertEqual(assess.ecn_marks, 8)
        self.assertIn("Cobalt AQM", assess.recommendation)
        self.assertIn("Fair Queueing", assess.recommendation)

    def test_cake_clean_state_assessment(self):
        """CAKE qdisc navbati toza va minimal kechikishda bo'lganda adolatli tavsiya shakllantirishi kerak."""
        cake_clean = """
qdisc cake 1: dev eth1 root refcnt 2 bandwidth 50Mbit diffserv3 triple-isolate rtt 100ms
 Sent 100000 bytes 100 pkt (dropped 0, overlimits 0 requeues 0)
 backlog 0b 0p requeues 0
 av_delay 1.1ms
        """
        stats = self.tracker.parse_tc_qdisc_output(cake_clean, dev="eth1")[0]
        assess = self.tracker.assess_bufferbloat(stats)
        self.assertFalse(assess.detected)
        self.assertEqual(assess.severity, "normal")
        self.assertIn("Fair Queueing", assess.recommendation)

    def test_simulate_cake_sample_helper(self):
        """QueueTracker.simulate_cake_sample to'g'ri strukturaga ega ekanligini tekshirish."""
        sample = self.tracker.simulate_cake_sample(
            dev="r1-eth3",
            bandwidth_bps=25_000_000.0,
            unforced_drops=6,
            forced_drops=1,
            ecn_marks=4,
            av_delay_ms=3.1,
        )
        self.assertEqual(sample.kind, "cake")
        self.assertEqual(sample.dropped, 7)
        self.assertEqual(sample.early_drops, 6)
        self.assertEqual(sample.ecn_marks, 4)
        self.assertEqual(sample.extra_stats["cake_unforced_drops"], 6)
        self.assertEqual(sample.extra_stats["cake_forced_drops"], 1)


class TestApplicationWorkloadsAndQoE(unittest.TestCase):
    """ITU-T G.107 VoIP E-Model va DASH/HLS Adaptiv Video Oqimi audit tekshiruvi."""

    def test_voip_itu_t_g107_formula_and_codecs(self):
        """G.711, G.729 va Opus kodeklarining R-Factor va MOS hisobini tekshirish."""
        # 1. G.711 (PCMU/A): Ie=0, Bpl=4.3
        res_g711 = VoIPMOSCalculator.calculate(delay_ms=15.0, jitter_ms=1.0, loss_pct=0.0, codec="G.711")
        self.assertAlmostEqual(res_g711.r_factor, 92.8, delta=0.5)
        self.assertGreaterEqual(res_g711.mos, 4.35)
        self.assertIn("A'lo", res_g711.rating)

        # 2. G.729: Ie=10.0, Bpl=19.0
        res_g729 = VoIPMOSCalculator.calculate(delay_ms=15.0, jitter_ms=1.0, loss_pct=0.0, codec="g729")
        self.assertEqual(res_g729.codec, "G.729")
        self.assertAlmostEqual(res_g729.r_factor, 82.8, delta=0.5)
        self.assertGreaterEqual(res_g729.mos, 4.10)

        # 3. Opus: Ie=5.0, Bpl=15.0, R0=94.0
        res_opus = VoIPMOSCalculator.calculate(delay_ms=15.0, jitter_ms=1.0, loss_pct=0.0, codec="opus")
        self.assertEqual(res_opus.codec, "Opus")
        self.assertAlmostEqual(res_opus.r_factor, 88.6, delta=0.5)
        self.assertGreaterEqual(res_opus.mos, 4.25)

    def test_voip_edge_cases_and_clamping(self):
        """Extreme delay (1000ms), 100% loss va manfiy qiymatlarda MOS [1.0, 4.5] chegarasida bo'lishi kerak."""
        # 100% loss + high delay -> minimum MOS 1.0, R=0
        res_worst = VoIPMOSCalculator.calculate(delay_ms=800.0, jitter_ms=50.0, loss_pct=100.0)
        self.assertEqual(res_worst.mos, 1.0)
        self.assertEqual(res_worst.r_factor, 0.0)
        self.assertIn("Yaroqsiz", res_worst.rating)

        # Manfiy kiruvchi qiymatlar
        res_neg = VoIPMOSCalculator.calculate(delay_ms=-20.0, jitter_ms=-5.0, loss_pct=-10.0)
        self.assertGreaterEqual(res_neg.mos, 4.3)
        self.assertLessEqual(res_neg.mos, 4.5)

        # None qiymatlar
        res_none = VoIPMOSCalculator.calculate(delay_ms=None, jitter_ms=None, loss_pct=None)
        self.assertGreaterEqual(res_none.mos, 4.3)

    def test_adaptive_video_abr_and_p1203_qoe(self):
        """ABR Video: ultra past tezlikda 240p ga tushishi, bufer tugaganda stall va P.1203 hisobi."""
        session = AdaptiveVideoSession("audit_video_session")

        # 1. Normal boshlanish: 8 Mbps -> 1080p
        p1 = session.update(measured_bandwidth_bps=8_000_000)
        self.assertEqual(p1["quality"], "1080p")
        self.assertGreaterEqual(p1["qoe_score"], 4.0)

        # 2. Kanal keskin toraydi: 150 Kbps (240p talabidan ham past)
        # Sifat 240p ga tushishi kerak (eski sifatda qolib ketmasdan)
        for _ in range(8):
            p_low = session.update(measured_bandwidth_bps=150_000)

        self.assertEqual(p_low["quality"], "240p")
        self.assertGreater(session.stall_count, 0)
        self.assertGreater(session.total_stall_sec, 0.0)
        self.assertLess(p_low["qoe_score"], 3.0)
        self.assertGreaterEqual(p_low["qoe_score"], 1.0)


class TestSLAAandABComparisonEngine(unittest.TestCase):
    """SLA va A/B Protokol / Algoritmlar solishtirish dvigateli audit tekshiruvi."""

    def test_compare_algorithms_symmetric(self):
        """CUBIC vs BBR va BBR vs CUBIC simmetrik solishtirilganda to'g'ri model ko'rsatkichlari berilishi kerak."""
        # 1. CUBIC (a) vs BBR (b)
        res1 = TelemetryComparator.compare_algorithms(
            algo_a="cubic",
            algo_b="bbr",
            bottleneck_bps=20_000_000.0,
            base_rtt_ms=25.0,
            loss_pct=2.0,
        )
        self.assertEqual(res1["overall_winner"], "candidate")
        self.assertEqual(res1["algo_a"], "CUBIC")
        self.assertEqual(res1["algo_b"], "BBR")

        # 2. BBR (a) vs CUBIC (b)
        res2 = TelemetryComparator.compare_algorithms(
            algo_a="bbr",
            algo_b="cubic",
            bottleneck_bps=20_000_000.0,
            base_rtt_ms=25.0,
            loss_pct=2.0,
        )
        self.assertEqual(res2["overall_winner"], "baseline")
        self.assertEqual(res2["algo_a"], "BBR")
        self.assertEqual(res2["algo_b"], "CUBIC")

    def test_compare_algorithms_reno_vs_cubic(self):
        """Reno va CUBIC solishtirilganda CUBIC ning yo'qotishdan keyin tezroq tiklanishi aniqlanishi kerak."""
        res = TelemetryComparator.compare_algorithms(
            algo_a="reno",
            algo_b="cubic",
            bottleneck_bps=10_000_000.0,
            base_rtt_ms=40.0,
            loss_pct=1.0,
        )
        self.assertIn(res["overall_winner"], ["candidate", "tie"])
        self.assertEqual(res["algo_a"], "RENO")
        self.assertEqual(res["algo_b"], "CUBIC")

    def test_compare_queuing_cake_support(self):
        """CAKE qdisc navbati solishtirilganda sojourn kechikishini keskin kamaytirishi tekshiruvi."""
        res = TelemetryComparator.compare_queuing(
            qdisc_a="pfifo",
            qdisc_b="cake",
            link_bps=20_000_000.0,
            load_pct=150.0,
        )
        comp = res["comparison"]
        self.assertTrue(comp["bufferbloat_detected"]["baseline"])
        self.assertFalse(comp["bufferbloat_detected"]["candidate"])
        self.assertGreater(comp["sojourn_time_ms"]["reduction_pct"], 85.0)
        self.assertIn("CAKE", res["recommendation"])


class TestLaTeXReportAndAPI(unittest.TestCase):
    """IEEE Conference publication-ready LaTeX Generator va FastAPI endpointlar tekshiruvi."""

    def test_latex_special_characters_escaping(self):
        """Sarlavha va matnlardagi TeX nazorat belgilarining (&, %, _, $, #, {, }, \\, <, >) to'liq tozalanishi."""
        raw_title = "Evaluation of TCP_Cubic & BBR with 10% packet_loss #1 in Lab{SDN}"
        report = generate_latex_report(title=raw_title, author="Test_Author & Researcher")

        self.assertIn(r"\title{Evaluation of TCP\_Cubic \& BBR with 10\% packet\_loss \#1 in Lab\{SDN\}}", report)
        self.assertIn(r"\author{\IEEEauthorblockN{Test\_Author \& Researcher}", report)
        self.assertIn(r"\documentclass[conference]{IEEEtran}", report)
        self.assertIn(r"\begin{equation}", report)
        self.assertIn(r"R = R_0 - I_d - I_{e,eff}", report)
        self.assertIn(r"\end{document}", report)

    def test_fastapi_latex_report_endpoint(self):
        """GET /api/telemetry/l4/report/latex to'g'ri text/plain sarlavhasi va LaTeX kodi bilan javob berishi kerak."""
        app = create_app("demo")
        with TestClient(app) as client:
            resp = client.get("/api/telemetry/l4/report/latex?title=Benchmark_Report")
            self.assertEqual(resp.status_code, 200)
            self.assertIn("text/plain", resp.headers["content-type"])
            self.assertIn(r"\documentclass[conference]{IEEEtran}", resp.text)
            self.assertIn("Benchmark\\_Report", resp.text)

    def test_fastapi_video_workload_qoe_endpoint(self):
        """GET /api/telemetry/l4/workloads/video javobida qoe_score mavjudligi."""
        app = create_app("demo")
        with TestClient(app) as client:
            resp = client.get("/api/telemetry/l4/workloads/video")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("qoe_score", data)
            self.assertGreaterEqual(data["qoe_score"], 1.0)
            self.assertLessEqual(data["qoe_score"], 5.0)


if __name__ == "__main__":
    unittest.main()
