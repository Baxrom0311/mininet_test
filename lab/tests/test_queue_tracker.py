import unittest
from fastapi.testclient import TestClient

from lab.server import create_app
from lab.telemetry.manager import TelemetryManager, telemetry_manager
from lab.telemetry.queue_tracker import (
    BufferbloatAssessment,
    QueueStats,
    QueueTracker,
    parse_rate_bps,
    parse_size_bytes,
    parse_time_ms,
)


class QueueTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = QueueTracker()

    def test_unit_parsing(self):
        self.assertEqual(parse_rate_bps("10Mbit"), 10_000_000.0)
        self.assertEqual(parse_rate_bps("100Kbit"), 100_000.0)
        self.assertEqual(parse_rate_bps("1Gbit"), 1_000_000_000.0)
        self.assertEqual(parse_rate_bps("10Mbps"), 80_000_000.0)
        self.assertEqual(parse_rate_bps("125000bps"), 125_000.0)

        self.assertEqual(parse_size_bytes("1500b"), 1500)
        self.assertEqual(parse_size_bytes("10Kb"), 10240)
        self.assertEqual(parse_size_bytes("1Mb"), 1024 * 1024)
        self.assertEqual(parse_size_bytes("1000p"), 1000)

        self.assertEqual(parse_time_ms("5ms"), 5.0)
        self.assertEqual(parse_time_ms("100.0ms"), 100.0)
        self.assertEqual(parse_time_ms("50us"), 0.05)
        self.assertEqual(parse_time_ms("1s"), 1000.0)

    def test_pfifo_parser(self):
        sample_output = """
qdisc pfifo 1: dev eth0 root refcnt 2 limit 1000p
 Sent 1048576 bytes 1024 pkt (dropped 15, overlimits 0 requeues 0) 
 backlog 14600b 10p requeues 0
        """
        stats_list = self.tracker.parse_tc_qdisc_output(sample_output, dev="eth0")
        self.assertEqual(len(stats_list), 1)
        qs = stats_list[0]
        self.assertEqual(qs.kind, "pfifo")
        self.assertEqual(qs.handle, "1:")
        self.assertEqual(qs.dev, "eth0")
        self.assertEqual(qs.limit, 1000)
        self.assertEqual(qs.limit_unit, "p")
        self.assertEqual(qs.bytes_sent, 1048576)
        self.assertEqual(qs.packets_sent, 1024)
        self.assertEqual(qs.dropped, 15)
        self.assertEqual(qs.overlimits, 0)
        self.assertEqual(qs.backlog_bytes, 14600)
        self.assertEqual(qs.backlog_packets, 10)
        self.assertAlmostEqual(qs.occupancy_pct, 1.0)  # 10 / 1000 * 100%

    def test_bfifo_parser(self):
        sample_output = """
qdisc bfifo 1: dev eth1 root refcnt 2 limit 150000b
 Sent 2048000 bytes 1500 pkt (dropped 8, overlimits 0 requeues 0) 
 backlog 30000b 20p requeues 0
        """
        stats_list = self.tracker.parse_tc_qdisc_output(sample_output, dev="eth1")
        self.assertEqual(len(stats_list), 1)
        qs = stats_list[0]
        self.assertEqual(qs.kind, "bfifo")
        self.assertEqual(qs.limit, 150000)
        self.assertEqual(qs.limit_unit, "b")
        self.assertEqual(qs.backlog_bytes, 30000)
        self.assertEqual(qs.backlog_packets, 20)
        self.assertAlmostEqual(qs.occupancy_pct, 20.0)  # 30000 / 150000 * 100%

    def test_red_parser(self):
        sample_output = """
qdisc red 10: dev eth0 root refcnt 2 limit 60Kb min 15Kb max 45Kb avpkt 1000 burst 20 ecn probability 0.02
 Sent 5242880 bytes 3500 pkt (dropped 42, overlimits 120 requeues 0) 
 backlog 15000b 10p requeues 0
  marked 15 early 30 pdrop 12 other 0
        """
        stats_list = self.tracker.parse_tc_qdisc_output(sample_output, dev="eth0")
        self.assertEqual(len(stats_list), 1)
        qs = stats_list[0]
        self.assertEqual(qs.kind, "red")
        self.assertEqual(qs.handle, "10:")
        self.assertTrue(qs.ecn)
        self.assertEqual(qs.params.get("min_bytes"), 15 * 1024)
        self.assertEqual(qs.params.get("max_bytes"), 45 * 1024)
        self.assertAlmostEqual(qs.params.get("probability"), 0.02)
        self.assertEqual(qs.dropped, 42)
        self.assertEqual(qs.overlimits, 120)
        self.assertEqual(qs.ecn_marks, 15)
        self.assertEqual(qs.early_drops, 30)
        self.assertEqual(qs.extra_stats.get("pdrop"), 12)

    def test_codel_parser(self):
        sample_output = """
qdisc codel 1: dev eth0 root refcnt 2 limit 1000p target 5.0ms interval 100.0ms ecn
 Sent 1024000 bytes 800 pkt (dropped 10, overlimits 0 requeues 0) 
 backlog 4500b 3p requeues 0
  count 2 lastcount 1 ldelay 4.2ms dropping drop_next 50ms
  maxpacket 1500 ecn_mark 5 drop_overlimit 0
        """
        stats_list = self.tracker.parse_tc_qdisc_output(sample_output, dev="eth0")
        self.assertEqual(len(stats_list), 1)
        qs = stats_list[0]
        self.assertEqual(qs.kind, "codel")
        self.assertEqual(qs.target_ms, 5.0)
        self.assertEqual(qs.interval_ms, 100.0)
        self.assertTrue(qs.ecn)
        self.assertTrue(qs.dropping)
        self.assertAlmostEqual(qs.ldelay_ms, 4.2)
        self.assertAlmostEqual(qs.estimated_queue_delay_ms, 4.2)
        self.assertEqual(qs.ecn_marks, 5)
        self.assertEqual(qs.extra_stats.get("count"), 2)
        self.assertEqual(qs.extra_stats.get("lastcount"), 1)

    def test_fq_codel_parser(self):
        sample_output = """
qdisc fq_codel 8001: dev eth0 root refcnt 2 limit 10240p flows 1024 quantum 1514 target 5ms interval 100ms memory_limit 32Mb ecn drop_batch 64 
 Sent 28948301 bytes 204519 pkt (dropped 12, overlimits 0 requeues 0) 
 backlog 3000b 2p requeues 0
  maxpacket 1780 drop_overlimit 0 new_flow_count 1143 ecn_mark 8
  new_flows_len 1 old_flows_len 2
  ce_mark 0 memory_used 0
        """
        stats_list = self.tracker.parse_tc_qdisc_output(sample_output, dev="eth0")
        self.assertEqual(len(stats_list), 1)
        qs = stats_list[0]
        self.assertEqual(qs.kind, "fq_codel")
        self.assertEqual(qs.handle, "8001:")
        self.assertEqual(qs.limit, 10240)
        self.assertEqual(qs.params.get("flows"), 1024)
        self.assertEqual(qs.params.get("quantum"), 1514)
        self.assertEqual(qs.target_ms, 5.0)
        self.assertEqual(qs.interval_ms, 100.0)
        self.assertEqual(qs.ecn_marks, 8)
        self.assertEqual(qs.extra_stats.get("new_flow_count"), 1143)
        self.assertEqual(qs.extra_stats.get("new_flows_len"), 1)
        self.assertEqual(qs.extra_stats.get("old_flows_len"), 2)

    def test_tbf_parser(self):
        sample_output = """
qdisc tbf 10: dev eth0 root refcnt 2 rate 10Mbit burst 10Kb lat 50.0ms 
 Sent 1024000 bytes 1000 pkt (dropped 25, overlimits 540 requeues 0) 
 backlog 15000b 10p requeues 0
        """
        stats_list = self.tracker.parse_tc_qdisc_output(sample_output, dev="eth0")
        self.assertEqual(len(stats_list), 1)
        qs = stats_list[0]
        self.assertEqual(qs.kind, "tbf")
        self.assertEqual(qs.handle, "10:")
        self.assertEqual(qs.rate_bps, 10_000_000.0)
        self.assertEqual(qs.params.get("latency_ms"), 50.0)
        self.assertEqual(qs.dropped, 25)
        self.assertEqual(qs.overlimits, 540)
        self.assertEqual(qs.backlog_bytes, 15000)
        self.assertEqual(qs.backlog_packets, 10)

    def test_class_show_parser(self):
        sample_output = """
class htb 1:10 parent 1: prio 1 rate 10Mbit ceil 10Mbit burst 15Kb cburst 15Kb 
 Sent 500000 bytes 400 pkt (dropped 2, overlimits 10 requeues 0) 
 backlog 1500b 1p requeues 0
 lended: 350 borrowed: 50 giants: 0
 tokens: 1234 ctokens: 1234
        """
        classes = self.tracker.parse_tc_class_output(sample_output, dev="eth0")
        self.assertEqual(len(classes), 1)
        c = classes[0]
        self.assertTrue(c.is_class)
        self.assertEqual(c.kind, "htb")
        self.assertEqual(c.handle, "1:10")
        self.assertEqual(c.parent, "1:")
        self.assertEqual(c.rate_bps, 10_000_000.0)
        self.assertEqual(c.params.get("ceil_bps"), 10_000_000.0)
        self.assertEqual(c.bytes_sent, 500000)
        self.assertEqual(c.dropped, 2)
        self.assertEqual(c.extra_stats.get("lended"), 350)
        self.assertEqual(c.extra_stats.get("borrowed"), 50)
        self.assertEqual(c.extra_stats.get("tokens"), 1234)

    def test_bufferbloat_detection(self):
        # Scenario 1: Severe bufferbloat on FIFO (high backlog, long delay)
        s_bloat = self.tracker.simulate_sample(
            dev="eth0",
            kind="pfifo",
            backlog_packets=90,
            backlog_bytes=131400,
            limit=100,
            rate_bps=10_000_000.0,
            dropped=10,
        )
        assess = self.tracker.assess_bufferbloat(s_bloat)
        self.assertTrue(assess.detected)
        self.assertIn(assess.severity, ("high", "critical"))
        self.assertGreaterEqual(assess.score, 65.0)
        self.assertGreater(assess.queue_delay_ms, 50.0)
        self.assertIn("fq_codel yoki CoDel", assess.recommendation)

        # Scenario 2: Pristine fq_codel (low delay, AQM operating nominally)
        s_clean = self.tracker.simulate_sample(
            dev="eth1",
            kind="fq_codel",
            backlog_packets=2,
            backlog_bytes=3000,
            limit=10240,
            ldelay_ms=3.5,
            dropping=False,
        )
        assess_clean = self.tracker.assess_bufferbloat(s_clean)
        self.assertFalse(assess_clean.detected)
        self.assertEqual(assess_clean.severity, "normal")
        self.assertLess(assess_clean.score, 25.0)

    def test_simulate_step_and_reports(self):
        tracker = QueueTracker()
        sim_samples = tracker.simulate_step(elapsed=5.0)
        self.assertEqual(len(sim_samples), 4)

        report = tracker.get_bufferbloat_report()
        self.assertIn("summary", report)
        self.assertIn("assessments", report)
        self.assertGreaterEqual(report["summary"]["monitored_interfaces"], 4)

        summary = tracker.get_summary()
        self.assertIn("total_queues", summary)
        self.assertIn("total_backlog_bytes", summary)
        self.assertGreater(summary["total_queues"], 0)

    def test_manager_integration(self):
        mgr = TelemetryManager()
        discovered = mgr.poll_once()
        self.assertTrue(any(d.get("type") == "queue" for d in discovered))

        queues = mgr.get_queues()
        self.assertGreaterEqual(len(queues), 1)

        summary = mgr.get_queue_summary()
        self.assertIn("total_queues", summary)

        global_stats = mgr.get_global_stats()
        self.assertIn("total_queues_count", global_stats)
        self.assertIn("bufferbloat_detected_count", global_stats)

    def test_fastapi_endpoints(self):
        app = create_app("demo")
        with TestClient(app) as client:
            # Poll once via simulate or endpoint
            client.post("/api/telemetry/simulate", json={"steps": 1})

            # GET /api/telemetry/l4/queues
            res = client.get("/api/telemetry/l4/queues")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertIn("queues", data)
            self.assertIn("bufferbloat", data)
            self.assertIn("summary", data)

            # GET /api/telemetry/queues alias
            res_alias = client.get("/api/telemetry/queues")
            self.assertEqual(res_alias.status_code, 200)

            # GET /api/telemetry/l4/bufferbloat
            res_bb = client.get("/api/telemetry/l4/bufferbloat")
            self.assertEqual(res_bb.status_code, 200)
            self.assertIn("assessments", res_bb.json())

            # GET /api/telemetry/l4/queues/{dev}
            # First get a dev name from queues list
            if data["queues"]:
                dev_name = data["queues"][0]["dev"]
                res_dev = client.get(f"/api/telemetry/l4/queues/{dev_name}")
                self.assertEqual(res_dev.status_code, 200)
                self.assertEqual(res_dev.json()["dev"], dev_name)


if __name__ == "__main__":
    unittest.main()
