"""OPNET-Grade Workloads & SLA Compliance Engine Unit Tests.

Covers:
1. Video (HLS/DASH Adaptive Bitrate Streaming):
   - Application validation (presets, buffer targets, segment duration)
   - Scenario expansion into TCP flows with proper roles, groups, DSCP, and direction
   - Post-processing: throughput-driven bitrate adaptation across VIDEO_LADDER
   - Buffer occupancy simulation and rebuffering stall events (count, duration, log)
   - ITU-T P.1203 QoE score calculation (1.0 to 5.0) and switching penalties
2. HTTP/3 (QUIC / UDP):
   - Validation of multiplexed stream counts (1..128)
   - Scenario expansion into multiplexed UDP streams with 0-RTT connection start
   - Multi-stream aggregation in post-processing (throughput, delay, jitter, loss)
3. SLA Compliance Engine:
   - evaluate_sla rule evaluation across throughput, delay, jitter, loss, and MOS/QoE
   - Severity categorization ('warning' vs 'critical')
   - Compliance percentage calculation, violation logs, and breakdown summaries
"""

import unittest

from lab.apps import (
    MODEL_TYPES,
    VIDEO_LADDER,
    VIDEO_PROFILES,
    VIDEO_QUALITIES,
    calculate_p1203,
    evaluate_sla,
    expand,
    postprocess,
    validate_app,
)


class VideoWorkloadTests(unittest.TestCase):
    def test_video_model_registered_and_ladder_presets(self):
        self.assertIn("video", MODEL_TYPES)
        self.assertEqual(VIDEO_QUALITIES, ("1080p", "720p", "480p", "360p"))
        self.assertEqual(len(VIDEO_LADDER), 4)
        for quality in ("1080p", "720p", "480p", "360p"):
            self.assertIn(quality, VIDEO_PROFILES)
            prof = VIDEO_PROFILES[quality]
            self.assertGreater(prof["bitrate_bps"], 0)
            self.assertGreater(prof["p1203_base"], 1.0)
            self.assertLessEqual(prof["p1203_base"], 5.0)

    def test_video_validation_rules(self):
        # Valid configs
        self.assertEqual(validate_app({"type": "video", "quality": "1080p", "target_buffer": 25}, 30), [])
        self.assertEqual(validate_app({"type": "video", "quality": "720p"}, 30), [])
        self.assertEqual(validate_app({"type": "video", "quality": "360p", "segment_duration": 4.0}, 30), [])
        self.assertEqual(validate_app({"type": "video"}, 30), [])

        # Invalid quality
        errs = validate_app({"type": "video", "quality": "4k_ultra_hd"}, 30)
        self.assertTrue(any("video sifati" in e for e in errs))

        # Invalid buffer target
        self.assertTrue(validate_app({"type": "video", "target_buffer": 0}, 30))
        self.assertTrue(validate_app({"type": "video", "target_buffer": -5}, 30))
        self.assertTrue(validate_app({"type": "video", "target_buffer": "invalid"}, 30))

        # Invalid segment duration
        self.assertTrue(validate_app({"type": "video", "segment_duration": -1}, 30))
        self.assertTrue(validate_app({"type": "video", "segment_duration": 0}, 30))

    def test_video_expansion_download_and_upload(self):
        sc_download = {
            "duration": 30,
            "apps": [
                {
                    "id": "vid1",
                    "type": "video",
                    "src": "client1",
                    "dst": "media_srv",
                    "direction": "download",
                    "quality": "1080p",
                    "target_buffer": 20,
                    "dscp": 34,
                    "dport": 8080,
                    "start": 2,
                    "stop": 28,
                    "label": "4K/1080p VoD Streaming",
                }
            ],
        }
        exp_dl = expand(sc_download)
        apps_dl = exp_dl["apps"]
        self.assertEqual(len(apps_dl), 1)
        sub = apps_dl[0]
        self.assertEqual(sub["id"], "vid1.stream")
        self.assertEqual(sub["type"], "tcp")
        # Download: Server is transmitter (src) -> Client is receiver (dst)
        self.assertEqual(sub["src"], "media_srv")
        self.assertEqual(sub["dst"], "client1")
        self.assertEqual(sub["group"], "vid1")
        self.assertEqual(sub["role"], "video")
        self.assertEqual(sub["dscp"], 34)
        self.assertEqual(sub["dport"], 8080)
        self.assertEqual(sub["start"], 2.0)
        self.assertEqual(sub["stop"], 28.0)

        # Upload: Client transmits to Server
        sc_upload = {
            "duration": 20,
            "apps": [
                {
                    "id": "upl1",
                    "type": "video",
                    "src": "client1",
                    "dst": "media_srv",
                    "direction": "upload",
                }
            ],
        }
        exp_ul = expand(sc_upload)
        sub_ul = exp_ul["apps"][0]
        self.assertEqual(sub_ul["src"], "client1")
        self.assertEqual(sub_ul["dst"], "media_srv")

    def test_video_postprocessing_abr_adaptation_and_stalls(self):
        # Scenario with video application
        sc = {
            "duration": 14,
            "apps": [
                {
                    "id": "vid1",
                    "type": "video",
                    "src": "h1",
                    "dst": "srv1",
                    "quality": "720p",
                    "target_buffer": 20.0,
                    "initial_buffer": 2.0,
                    "start": 0,
                    "stop": 14,
                }
            ],
        }

        # Simulated series:
        # t=0..2: initial playback (2.5 Mbps)
        # t=2..11: link outage / zero throughput -> buffer drains to 0, triggers stalls
        # t=11..14: recovery with high bandwidth (6 Mbps) -> adapts up to 1080p
        series_points = []
        for i in range(14):
            t_s = float(i)
            t_e = float(i + 1)
            if i < 2:
                bw, dl, jt, ls = 2_500_000, 15.0, 1.0, 0.0
            elif i < 11:
                bw, dl, jt, ls = 0, 200.0, 40.0, 50.0
            else:
                bw, dl, jt, ls = 6_000_000, 12.0, 0.8, 0.0
            series_points.append({
                "t_start": t_s,
                "t_end": t_e,
                "t": t_e,
                "thr_bps": bw,
                "delay_ms": dl,
                "jitter_ms": jt,
                "loss_pct": ls,
            })

        raw_result = {
            "duration": 14.0,
            "flows": {
                "vid1.stream": {
                    "id": "vid1.stream",
                    "group": "vid1",
                    "role": "video",
                    "start": 0.0,
                    "stop": 14.0,
                    "series": series_points,
                }
            },
        }

        processed = postprocess(raw_result, sc)
        self.assertIn("vid1", processed["flows"])
        flow_vid = processed["flows"]["vid1"]
        summary = flow_vid["summary"]

        # Check that postprocess evaluated video metrics
        self.assertIn("qoe_score", summary)
        self.assertIn("mos", summary)
        self.assertIn("stall_count", summary)
        self.assertIn("total_stall_sec", summary)
        self.assertIn("quality_switches", summary)
        self.assertIn("quality_distribution", summary)

        # Quality switches occurred because bandwidth dropped sharply then recovered
        self.assertGreater(summary["quality_switches"], 0)
        # Stalls were detected during the outage
        self.assertGreater(summary["stall_count"], 0)
        self.assertGreater(summary["total_stall_sec"], 0.0)
        self.assertIn(summary["final_quality"], VIDEO_QUALITIES)

        # Check time series points have video properties
        vid_series = flow_vid["series"]
        self.assertEqual(len(vid_series), 14)
        p_chg = [p for p in vid_series if p["quality"] in ("480p", "360p")]
        self.assertTrue(len(p_chg) > 0, "Video must have adapted to lower resolution")
        self.assertTrue(any(p["stall"] for p in vid_series), "Buffer stall must have been logged")

        # Check apps summary entry
        self.assertIn("vid1", processed["apps"])
        self.assertEqual(processed["apps"]["vid1"]["type"], "video")

    def test_p1203_qoe_scoring_calculations(self):
        # 1. Clean 1080p stream with no stalls
        qoe_perfect = calculate_p1203("1080p", total_duration=60.0, stall_count=0, total_stall_sec=0.0, quality_switches=0)
        self.assertGreaterEqual(qoe_perfect, 4.5)
        self.assertLessEqual(qoe_perfect, 5.0)

        # 2. 720p baseline
        qoe_720 = calculate_p1203("720p", total_duration=60.0, stall_count=0, total_stall_sec=0.0)
        self.assertAlmostEqual(qoe_720, 4.0, places=1)

        # 3. Frequent rebuffering stalls degrade QoE heavily
        qoe_stalled = calculate_p1203("720p", total_duration=60.0, stall_count=5, total_stall_sec=15.0, quality_switches=2)
        self.assertLess(qoe_stalled, 2.5)
        self.assertGreaterEqual(qoe_stalled, 1.0)

        # 4. Extreme degradation is clamped to 1.0 minimum
        qoe_broken = calculate_p1203("360p", total_duration=20.0, stall_count=10, total_stall_sec=18.0, quality_switches=8)
        self.assertEqual(qoe_broken, 1.0)

        # 5. Bitrate numeric input support
        qoe_high_bitrate = calculate_p1203(5_500_000, total_duration=30.0)
        self.assertGreaterEqual(qoe_high_bitrate, 4.5)

        # 6. Quality time distribution dict support
        dist = {"1080p": 20.0, "720p": 10.0, "480p": 0.0, "360p": 0.0}
        qoe_dist = calculate_p1203(dist, total_duration=30.0)
        self.assertGreater(qoe_dist, 4.2)


class Http3WorkloadTests(unittest.TestCase):
    def test_http3_model_registered_and_validation(self):
        self.assertIn("http3", MODEL_TYPES)

        # Valid configs
        self.assertEqual(validate_app({"type": "http3", "streams": 4, "rate": "5Mbps"}, 30), [])
        self.assertEqual(validate_app({"type": "http3", "streams": 1, "page": "1MB"}, 30), [])
        self.assertEqual(validate_app({"type": "http3", "streams": 64}, 30), [])

        # Invalid stream counts
        self.assertTrue(validate_app({"type": "http3", "streams": 0}, 30))
        self.assertTrue(validate_app({"type": "http3", "streams": -2}, 30))
        self.assertTrue(validate_app({"type": "http3", "streams": 256}, 30))
        self.assertTrue(validate_app({"type": "http3", "streams": "nan"}, 30))

        # Invalid page size or rate
        self.assertTrue(validate_app({"type": "http3", "page": "invalid_bytes"}, 30))

    def test_http3_expansion_multiplexing(self):
        sc = {
            "duration": 20,
            "apps": [
                {
                    "id": "web3",
                    "type": "http3",
                    "src": "client_node",
                    "dst": "server_node",
                    "streams": 4,
                    "rate": "8Mbps",
                    "size": 1250,
                    "dscp": 46,
                    "dport": 443,
                    "start": 1,
                    "stop": 15,
                    "label": "QUIC Web3 Session",
                }
            ],
        }
        exp = expand(sc)
        apps = exp["apps"]
        self.assertEqual(len(apps), 4, "HTTP/3 with 4 streams must expand into 4 sub-flows")

        for idx, sub in enumerate(apps, start=1):
            self.assertEqual(sub["id"], f"web3.s{idx}")
            self.assertEqual(sub["type"], "udp")
            self.assertEqual(sub["group"], "web3")
            self.assertEqual(sub["role"], f"quic_s{idx}")
            self.assertEqual(sub["quic_stream_id"], idx)
            self.assertTrue(sub["zero_rtt"])
            self.assertEqual(sub["dscp"], 46)
            self.assertEqual(sub["dport"], 443)
            self.assertEqual(sub["size"], 1250)
            self.assertEqual(sub["start"], 1.0)
            self.assertEqual(sub["stop"], 15.0)

    def test_http3_postprocessing_stream_aggregation(self):
        sc = {
            "duration": 5,
            "apps": [
                {
                    "id": "h3_app",
                    "type": "http3",
                    "src": "c1",
                    "dst": "s1",
                    "streams": 2,
                    "start": 0,
                    "stop": 5,
                }
            ],
        }
        raw_result = {
            "duration": 5.0,
            "flows": {
                "h3_app.s1": {
                    "id": "h3_app.s1",
                    "group": "h3_app",
                    "series": [
                        {"t_start": 0.0, "t_end": 1.0, "t": 1.0, "thr_bps": 2_000_000, "delay_ms": 10.0, "jitter_ms": 1.0, "loss_pct": 0.0},
                        {"t_start": 1.0, "t_end": 2.0, "t": 2.0, "thr_bps": 2_100_000, "delay_ms": 12.0, "jitter_ms": 1.5, "loss_pct": 0.0},
                    ],
                },
                "h3_app.s2": {
                    "id": "h3_app.s2",
                    "group": "h3_app",
                    "series": [
                        {"t_start": 0.0, "t_end": 1.0, "t": 1.0, "thr_bps": 2_000_000, "delay_ms": 10.5, "jitter_ms": 1.2, "loss_pct": 0.0},
                        {"t_start": 1.0, "t_end": 2.0, "t": 2.0, "thr_bps": 1_900_000, "delay_ms": 11.5, "jitter_ms": 1.4, "loss_pct": 0.0},
                    ],
                },
            },
        }
        res = postprocess(raw_result, sc)
        self.assertIn("h3_app", res["flows"])
        flow_h3 = res["flows"]["h3_app"]
        sm = flow_h3["summary"]
        self.assertEqual(sm["protocol"], "HTTP/3 (QUIC/UDP)")
        self.assertEqual(sm["streams"], 2)
        self.assertTrue(sm["zero_rtt"])
        self.assertTrue(sm["multiplexed"])
        self.assertAlmostEqual(sm["avg_thr_bps"], 4_000_000, delta=150_000)
        self.assertGreater(sm["avg_delay_ms"], 10.0)

        # Aggregated series check
        series = flow_h3["series"]
        self.assertEqual(len(series), 2)
        self.assertEqual(series[0]["streams_active"], 2)
        self.assertEqual(series[0]["thr_bps"], 4_000_000)


class SLAMonitoringEngineTests(unittest.TestCase):
    def test_evaluate_sla_fully_compliant(self):
        run = {
            "duration": 5.0,
            "flows": {
                "f1": {
                    "id": "f1",
                    "summary": {"avg_thr_bps": 5_000_000, "avg_delay_ms": 12.0, "avg_jitter_ms": 1.0, "loss_pct": 0.0},
                    "series": [
                        {"t": 1.0, "thr_bps": 5_000_000, "delay_ms": 12.0, "jitter_ms": 1.0, "loss_pct": 0.0, "mos": 4.3},
                        {"t": 2.0, "thr_bps": 5_200_000, "delay_ms": 13.0, "jitter_ms": 0.9, "loss_pct": 0.0, "mos": 4.35},
                    ],
                }
            },
        }
        sla_spec = {
            "min_throughput_bps": 1_000_000,
            "max_delay_ms": 50.0,
            "max_jitter_ms": 5.0,
            "max_loss_pct": 1.0,
            "min_mos": 4.0,
            "target_compliance_pct": 99.0,
        }
        res = evaluate_sla(run, sla_spec)
        self.assertEqual(res["compliance_pct"], 100.0)
        self.assertEqual(res["total_violations"], 0)
        self.assertEqual(len(res["violations"]), 0)
        self.assertTrue(res["is_compliant"])
        self.assertGreater(res["total_evaluations"], 0)
        self.assertEqual(res["compliant_evaluations"], res["total_evaluations"])

    def test_evaluate_sla_violations_and_severity_classification(self):
        run = {
            "duration": 4.0,
            "flows": {
                "f_bad": {
                    "id": "f_bad",
                    "series": [
                        # Point 1: Good
                        {"t": 1.0, "thr_bps": 10_000_000, "delay_ms": 20.0, "jitter_ms": 2.0, "loss_pct": 0.0, "mos": 4.3},
                        # Point 2: Delay and Jitter warnings
                        {"t": 2.0, "thr_bps": 8_000_000, "delay_ms": 65.0, "jitter_ms": 12.0, "loss_pct": 0.0, "mos": 4.1},
                        # Point 3: Critical Delay (>2x limit) and Critical Loss (>3x limit)
                        {"t": 3.0, "thr_bps": 200_000, "delay_ms": 150.0, "jitter_ms": 30.0, "loss_pct": 15.0, "mos": 2.1},
                    ],
                }
            },
        }
        sla_spec = {
            "min_throughput_bps": 1_000_000,
            "max_delay_ms": 50.0,
            "max_jitter_ms": 10.0,
            "max_loss_pct": 1.0,
            "min_mos": 4.0,
            "target_compliance_pct": 95.0,
        }
        res = evaluate_sla(run, sla_spec)
        self.assertLess(res["compliance_pct"], 90.0)
        self.assertFalse(res["is_compliant"])
        self.assertGreater(res["total_violations"], 0)

        # Check violation entries
        viols = res["violations"]
        severities = {v["severity"] for v in viols}
        self.assertIn("warning", severities)
        self.assertIn("critical", severities)

        metrics = {v["metric"] for v in viols}
        self.assertIn("delay_ms", metrics)
        self.assertIn("jitter_ms", metrics)
        self.assertIn("loss_pct", metrics)
        self.assertIn("throughput_bps", metrics)
        self.assertIn("mos", metrics)

        # Breakdown dictionaries present
        self.assertIn("delay", res["summary_by_metric"])
        self.assertIn("loss", res["summary_by_metric"])
        self.assertIn("f_bad", res["summary_by_flow"])

    def test_evaluate_sla_edge_cases(self):
        # Empty run dictionary
        res_empty = evaluate_sla({}, {})
        self.assertEqual(res_empty["compliance_pct"], 100.0)
        self.assertEqual(res_empty["total_violations"], 0)
        self.assertTrue(res_empty["is_compliant"])

        # Summary-only flows without time series
        run_summary = {
            "flows": {
                "f_sum": {
                    "id": "f_sum",
                    "summary": {
                        "avg_thr_bps": 500_000,  # below 1Mbps threshold
                        "avg_delay_ms": 100.0,   # above 40ms threshold
                        "loss_pct": 0.0,
                    },
                }
            }
        }
        res_sum = evaluate_sla(run_summary, {"min_throughput": 1_000_000, "max_delay": 40.0})
        self.assertGreater(res_sum["total_violations"], 0)
        self.assertLess(res_sum["compliance_pct"], 100.0)


if __name__ == "__main__":
    unittest.main()
