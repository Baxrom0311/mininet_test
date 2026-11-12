import unittest
from lab.twin.what_if_analyzer import WhatIfAnalyzer, FailureType


class TestWhatIfAnalyzer(unittest.TestCase):
    def test_baseline_topology(self):
        analyzer = WhatIfAnalyzer()
        self.assertGreater(len(analyzer.graph.links), 5)
        self.assertGreater(len(analyzer.graph.nodes), 4)

    def test_traffic_surge_simulation(self):
        analyzer = WhatIfAnalyzer()
        report = analyzer.predict_traffic_surge(surge_factor=3.0)
        self.assertEqual(report.failure_type, FailureType.TRAFFIC_SURGE)
        self.assertIsNotNone(report.affected_links)

    def test_link_cut_simulation(self):
        analyzer = WhatIfAnalyzer()
        report = analyzer.predict_link_cut("link-c1-c2")
        self.assertEqual(report.failure_type, FailureType.LINK_CUT)
        self.assertIsNotNone(report.rerouted_flows)

    def test_node_failure_simulation(self):
        analyzer = WhatIfAnalyzer()
        report = analyzer.predict_node_failure("Core-1")
        self.assertEqual(report.failure_type, FailureType.NODE_FAILURE)
        self.assertIsNotNone(report.rerouted_flows)


if __name__ == "__main__":
    unittest.main()
