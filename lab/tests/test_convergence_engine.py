import unittest
from lab.engines.convergence_engine import (
    QueueDynamicsSimulator,
    RoutingConvergenceTracker,
)


class TestConvergenceEngine(unittest.TestCase):
    def test_routing_convergence_standard(self):
        tracker = RoutingConvergenceTracker(carrier_detect=True)
        report = tracker.calculate_failover(
            event_type="link_down",
            target="s1-s2",
            num_routers=10,
            num_links=20,
            traffic_rate_pps=5000,
            protocol="OSPF",
            has_lfa_backup=False,
        )

        self.assertEqual(report.protocol, "OSPF")
        self.assertGreater(report.total_convergence_ms, 0.0)
        self.assertLess(report.total_convergence_ms, 150.0)  # Carrier detect bilan < 150ms
        self.assertGreater(report.transient_lost_packets, 0)
        self.assertEqual(report.alternate_mode, "standard")

    def test_routing_convergence_lfa_fast_reroute(self):
        tracker = RoutingConvergenceTracker(carrier_detect=True)
        report = tracker.calculate_failover(
            event_type="link_down",
            target="s1-s2",
            num_routers=10,
            num_links=20,
            traffic_rate_pps=5000,
            protocol="OSPF",
            has_lfa_backup=True,
        )

        # LFA (Loop-Free Alternate) 50ms dan kam konvergentsiya berishi kerak
        self.assertEqual(report.alternate_mode, "lfa_backup")
        self.assertLess(report.total_convergence_ms, 50.0)
        self.assertEqual(report.spf_calculation_ms, 0.0)

    def test_queue_dynamics_tail_drop(self):
        sim = QueueDynamicsSimulator(buffer_capacity_pkts=50, link_rate_pps=1000)
        # Port tezligidan (1000 pps) 2 barobar ko'p trafik yuboramiz (2000 pps)
        heavy_traffic = [2000] * 10
        res = sim.simulate(heavy_traffic, algorithm="fifo")

        self.assertEqual(res.algorithm, "fifo")
        self.assertGreater(res.total_dropped, 0)
        self.assertGreater(res.drop_rate_pct, 10.0)
        self.assertGreater(res.max_queuing_delay_ms, 40.0)

    def test_queue_dynamics_codel_bufferbloat_mitigation(self):
        sim = QueueDynamicsSimulator(buffer_capacity_pkts=200, link_rate_pps=1000)
        heavy_traffic = [1800] * 10
        # CoDel navbat kechikishini nazorat qiladi
        res_fifo = sim.simulate(heavy_traffic, algorithm="fifo")
        res_codel = sim.simulate(heavy_traffic, algorithm="codel")

        self.assertLess(res_codel.avg_queuing_delay_ms, res_fifo.avg_queuing_delay_ms)


if __name__ == "__main__":
    unittest.main()
