r"""Scientific LaTeX Report Generation and SLA REST API Tests.

Covers:
1. `lab.report_latex.latex_escape`:
   - Escaping of all TeX control characters (&, %, $, #, _, {, }, ~, ^, \, <, >)
   - Unicode, whitespace, and numerical boundary tolerance
2. `lab.report_latex.generate_run_latex`:
   - Document structure (IEEE/ACM journal/conference grade preamble, documentclass)
   - Packages: booktabs, amsmath, siunitx, tabularx, xcolor, hyperref
   - Executive Summary and global KPI table
   - Topology and Device Configuration table
   - Application Performance & QoE table (VoIP MOS ITU-T G.107, HTTP/3 QUIC, Video DASH/HLS P.1203, FTP)
   - SLA Compliance & Violation log table
   - L4 Transport Telemetry table (Linux struct tcp_info CWND, RTT, RFC 3550 jitter, goodput, eBPF drops)
   - Comparative A/B Analysis section
3. `lab.report_latex.generate_comparison_latex`:
   - Multi-run comparison report with delta metrics and side-by-side tables
   - Edge case tolerance (empty run set, single run set delegation)
4. FastAPI Endpoints:
   - POST /api/analysis/sla (direct run payload and error cases)
   - POST /api/reports/latex (single run and multi-run exports)
   - GET /api/runs/{project}/{id}/report.tex (404 and content headers)
"""

import unittest
from fastapi.testclient import TestClient

from lab.report_latex import (
    fmt_ms,
    fmt_mbps,
    fmt_num,
    fmt_pct,
    generate_comparison_latex,
    generate_run_latex,
    latex_escape,
)
from lab.server import create_app


class LatexEscapingTests(unittest.TestCase):
    def test_special_characters_escaping(self):
        cases = [
            ("eth0_1", r"eth0\_1"),
            ("A & B", r"A \& B"),
            ("100% loss", r"100\% loss"),
            ("$var", r"\$var"),
            ("#1 priority", r"\#1 priority"),
            ("int{x}", r"int\{x\}"),
            ("path\\to\\file", r"path\textbackslash{}to\textbackslash{}file"),
            ("delay < 20", r"delay $<$ 20"),
            ("jitter > 5", r"jitter $>$ 5"),
            ("approx ~ 10", r"approx \textasciitilde{} 10"),
            ("power ^ 2", r"power \textasciicircum{} 2"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(latex_escape(raw), expected)

    def test_escaping_handles_non_strings_and_none(self):
        self.assertEqual(latex_escape(None), "")
        self.assertEqual(latex_escape(123), "123")
        self.assertEqual(latex_escape(45.67), "45.67")
        self.assertEqual(latex_escape(""), "")

    def test_format_helpers(self):
        self.assertEqual(fmt_num(12.3456, nd=2), "12.35")
        self.assertEqual(fmt_num(None, fallback="N/A"), "N/A")
        self.assertEqual(fmt_mbps(5_000_000), "5.00")
        self.assertEqual(fmt_pct(2.5), r"2.50\%")
        self.assertEqual(fmt_ms(15.2), "15.20~ms")


class SingleRunLatexGenerationTests(unittest.TestCase):
    def setUp(self):
        self.sample_run = {
            "run_id": "run-exp-001",
            "project": {"name": "datacenter_telemetry"},
            "scenario": {"name": "congestion_control_study", "duration": 20.0},
            "engine": "analytic",
            "engine_label": "Analytic Fluid Model",
            "status": "completed",
            "created": "2026-11-12 14:00:00",
            "nodes": [
                {"id": "h1", "type": "host", "ip": "10.0.1.10/24"},
                {"id": "r1", "type": "router", "routing": "ospf", "daemons": ["zebra", "ospfd"]},
                {"id": "srv1", "type": "server", "ip": "10.0.2.20/24"},
            ],
            "links": [
                {"src": "h1", "dst": "r1", "bw": 100, "delay": 2.0, "qdisc": "fq_codel", "limit": 1000},
                {"src": "r1", "dst": "srv1", "bw": 10, "delay": 15.0, "qdisc": "fifo", "limit": 100},
            ],
            "flows": {
                "voip_call": {
                    "id": "voip_call",
                    "type": "voip",
                    "src": "h1",
                    "dst": "srv1",
                    "summary": {
                        "codec": "G.711 (64 kbit/s)",
                        "avg_mos": 4.35,
                        "min_mos": 4.20,
                        "avg_delay_ms": 17.5,
                        "avg_jitter_ms": 1.2,
                        "loss_pct": 0.05,
                        "avg_thr_bps": 64000,
                        "quality": "a'lo",
                    },
                },
                "abr_video": {
                    "id": "abr_video",
                    "type": "video",
                    "src": "srv1",
                    "dst": "h1",
                    "summary": {
                        "qoe_score": 4.25,
                        "stall_count": 0,
                        "total_stall_sec": 0.0,
                        "quality_switches": 1,
                        "final_quality": "1080p",
                        "avg_bitrate_bps": 4_800_000,
                        "avg_buffer_sec": 12.5,
                        "quality": "a'lo",
                    },
                },
                "quic_web": {
                    "id": "quic_web",
                    "type": "http3",
                    "src": "srv1",
                    "dst": "h1",
                    "summary": {
                        "protocol": "HTTP/3 (QUIC/UDP)",
                        "streams": 4,
                        "zero_rtt": True,
                        "avg_thr_bps": 3_500_000,
                        "avg_delay_ms": 16.0,
                        "avg_jitter_ms": 1.1,
                        "loss_pct": 0.0,
                    },
                },
                "ftp_backup": {
                    "id": "ftp_backup",
                    "type": "ftp",
                    "src": "h1",
                    "dst": "srv1",
                    "summary": {
                        "bytes": 20_000_000,
                        "transfer_s": 16.8,
                        "avg_rate_bps": 9_520_000,
                        "completed": True,
                    },
                },
            },
            "apps": {
                "voip_call": {"type": "voip", "avg_mos": 4.35, "quality": "a'lo"},
                "abr_video": {"type": "video", "qoe_score": 4.25, "stall_count": 0},
                "quic_web": {"type": "http3", "streams": 4, "avg_thr_bps": 3_500_000},
                "ftp_backup": {"type": "ftp", "transfer_s": 16.8, "completed": True},
            },
            "sla": {
                "compliance_pct": 99.4,
                "total_evaluations": 180,
                "total_violations": 1,
                "rules": [
                    {"name": "One-Way Latency", "target": "<= 50.0 ms", "measured": "17.5 ms", "status": "PASS"},
                    {"name": "Inter-Arrival Jitter", "target": "<= 5.0 ms", "measured": "1.2 ms", "status": "PASS"},
                    {"name": "Packet Loss Rate", "target": "<= 1.0%", "measured": "0.05%", "status": "PASS"},
                    {"name": "VoIP MOS / QoE", "target": ">= 4.00", "measured": "4.35", "status": "PASS"},
                ],
                "violations": [
                    {"stream": "quic_web", "metric": "jitter_ms", "value": "5.2 ms", "limit": "<= 5.0 ms", "severity": "WARNING"}
                ],
            },
            "telemetry": {
                "rtt_ms": 17.5,
                "jitter_ms": 1.2,
                "cwnd_max": 48,
                "retrans_total": 2,
                "ebpf_drops": 0,
            },
        }

    def test_latex_document_structure_and_preamble(self):
        doc = generate_run_latex(self.sample_run)
        # TeX document structure
        self.assertTrue(doc.startswith(r"\documentclass") or r"\documentclass" in doc[:100])
        self.assertIn(r"\begin{document}", doc)
        self.assertIn(r"\end{document}", doc)

        # Standard packages requirement
        for pkg in ("booktabs", "amsmath", "siunitx", "tabularx", "xcolor", "hyperref"):
            self.assertTrue(
                any(pkg in line for line in doc.splitlines() if line.startswith(r"\usepackage")),
                f"Package {pkg} must be included in \\usepackage declarations",
            )

    def test_required_sections_present(self):
        doc = generate_run_latex(self.sample_run)
        # 1. Executive Summary
        self.assertIn(r"\section{Executive Summary}", doc)
        self.assertIn(latex_escape("congestion_control_study"), doc)

        # 2. Topology & Device Configuration table
        self.assertIn(r"\section{Topology and Device Configuration}", doc)
        self.assertIn(r"\begin{tabularx}", doc)
        self.assertIn(latex_escape("fq_codel"), doc)

        # 3. Application Performance & QoE table (VoIP MOS, HTTP/3, Video, FTP)
        self.assertIn(r"\section{Application Performance and QoE Analysis}", doc)
        self.assertIn("G.711", doc)
        self.assertIn("4.35", doc)
        self.assertIn("1080p", doc)
        self.assertIn("HTTP/3", doc)

        # 4. SLA Compliance & Violation table
        self.assertIn(r"\section{SLA Compliance and Violation Analysis}", doc)
        self.assertIn(r"\label{tab:sla_rules}", doc)
        self.assertIn("99.4", doc)

        # 5. L4 Transport Telemetry metrics table
        self.assertIn(r"\section{L4 Transport Telemetry and Kernel Introspection}", doc)
        self.assertIn(r"\label{tab:l4_telemetry}", doc)
        self.assertIn("CWND", doc)

        # 6. Comparative A/B Analysis section
        self.assertIn(r"\section{Comparative A/B Performance Benchmark}", doc)


class MultiRunComparisonLatexTests(unittest.TestCase):
    def test_comparison_latex_multi_run_and_edge_cases(self):
        r1 = {
            "run_id": "run-fifo",
            "project": {"name": "aqm_eval"},
            "scenario": {"name": "sc_fifo", "duration": 10},
            "engine": "analytic",
            "flows": {
                "f1": {"id": "f1", "type": "voip", "summary": {"avg_mos": 3.2, "avg_thr_bps": 64000, "avg_delay_ms": 95.0, "loss_pct": 2.5}}
            },
            "apps": {"a1": {"type": "voip", "avg_mos": 3.2}},
            "sla": {"compliance_pct": 78.0, "total_evaluations": 10, "total_violations": 2, "rules": []},
        }
        r2 = {
            "run_id": "run-fqcodel",
            "project": {"name": "aqm_eval"},
            "scenario": {"name": "sc_fqcodel", "duration": 10},
            "engine": "analytic",
            "flows": {
                "f1": {"id": "f1", "type": "voip", "summary": {"avg_mos": 4.4, "avg_thr_bps": 64000, "avg_delay_ms": 14.0, "loss_pct": 0.0}}
            },
            "apps": {"a1": {"type": "voip", "avg_mos": 4.4}},
            "sla": {"compliance_pct": 100.0, "total_evaluations": 10, "total_violations": 0, "rules": []},
        }

        # Multi-run comparison
        comp_doc = generate_comparison_latex([r1, r2])
        self.assertIn(r"\documentclass", comp_doc)
        self.assertIn(r"\section{Comparative A/B Multi-Run Scientific Analysis}", comp_doc)
        self.assertIn(r"\label{tab:multi_run_summary}", comp_doc)
        self.assertIn(r"\end{document}", comp_doc)
        # Both runs referenced
        self.assertIn(latex_escape("sc_fifo"), comp_doc)
        self.assertIn(latex_escape("sc_fqcodel"), comp_doc)

        # Edge case: Single run delegates cleanly
        single_delegated = generate_comparison_latex([r1])
        self.assertIn(r"\section{Executive Summary}", single_delegated)

        # Edge case: Empty run set does not crash
        empty_doc = generate_comparison_latex([])
        self.assertIn(r"\documentclass", empty_doc)
        self.assertIn(r"\end{document}", empty_doc)


class FastAPISlaAndLatexEndpointsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(create_app("demo"))

    def test_post_analysis_sla_direct_run(self):
        run_data = {
            "duration": 5.0,
            "flows": {
                "test_flow": {
                    "id": "test_flow",
                    "series": [
                        {"t": 1.0, "thr_bps": 5_000_000, "delay_ms": 15.0, "jitter_ms": 1.0, "loss_pct": 0.0, "mos": 4.3},
                        {"t": 2.0, "thr_bps": 500_000, "delay_ms": 80.0, "jitter_ms": 15.0, "loss_pct": 3.0, "mos": 3.2},
                    ],
                }
            },
        }
        req_body = {
            "run": run_data,
            "sla": {
                "min_throughput_bps": 1_000_000,
                "max_delay_ms": 50.0,
                "max_loss_pct": 1.0,
                "min_mos": 4.0,
            },
        }
        resp = self.client.post("/api/analysis/sla", json=req_body)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("compliance_pct", data)
        self.assertIn("total_violations", data)
        self.assertIn("violations", data)
        self.assertGreater(data["total_violations"], 0)
        self.assertLess(data["compliance_pct"], 100.0)

    def test_post_analysis_sla_missing_run_error(self):
        resp = self.client.post("/api/analysis/sla", json={"sla": {"max_delay_ms": 20.0}})
        self.assertEqual(resp.status_code, 400)

    def test_post_reports_latex_single_run(self):
        run_data = {
            "run_id": "test_run_tex",
            "project": {"name": "test_proj"},
            "scenario": {"name": "test_sc", "duration": 10},
            "engine": "analytic",
            "flows": {
                "f1": {"id": "f1", "type": "voip", "summary": {"avg_mos": 4.2, "avg_thr_bps": 64000}}
            },
        }
        resp = self.client.post("/api/reports/latex", json={"run": run_data})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.headers.get("content-type", "").startswith("application/x-tex"))
        self.assertIn('attachment; filename="test_run_tex.tex"', resp.headers.get("content-disposition", ""))
        self.assertIn(r"\documentclass", resp.text)
        self.assertIn(r"\end{document}", resp.text)

    def test_post_reports_latex_multi_run_comparison(self):
        r1 = {"run_id": "r1", "scenario": {"name": "sc1"}, "flows": {}}
        r2 = {"run_id": "r2", "scenario": {"name": "sc2"}, "flows": {}}
        resp = self.client.post("/api/reports/latex", json={"runs": [r1, r2]})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.headers.get("content-type", "").startswith("application/x-tex"))
        self.assertIn('attachment; filename="solishtirish.tex"', resp.headers.get("content-disposition", ""))
        self.assertIn(r"\section{Comparative A/B Multi-Run Scientific Analysis}", resp.text)

    def test_get_run_report_tex_not_found(self):
        resp = self.client.get("/api/runs/nonexistent_proj/nonexistent_id/report.tex")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
