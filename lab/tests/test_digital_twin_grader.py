import unittest
from fastapi.testclient import TestClient

from lab.server import create_app
from lab.twin.what_if_analyzer import WhatIfAnalyzer, what_if_analyzer
from lab.education import NetAcadGrader, auto_grader, get_lab_template, list_available_labs


class TestDigitalTwin(unittest.TestCase):
    def setUp(self):
        self.twin = WhatIfAnalyzer()

    def test_topology_and_initial_routing(self):
        status = self.twin.get_topology_status()
        self.assertGreaterEqual(status["node_count"], 4)
        self.assertGreaterEqual(status["link_count"], 4)
        self.assertGreaterEqual(status["flow_count"], 1)

    def test_traffic_multiplier_surge(self):
        # 1.5x da simulyatsiya
        res_1_5 = self.twin.simulate_traffic_multiplier(1.5)
        self.assertEqual(res_1_5["failure_type"], "traffic_surge")
        self.assertEqual(res_1_5["metrics"]["surge_factor"], 1.5)

        # 5x da ayrim kanallar saturatsiyaga uchrab, xavf darajasi oshishi kerak
        res_5x = self.twin.simulate_traffic_multiplier(5.0)
        self.assertGreater(res_5x["metrics"]["max_utilization_pct"], 80.0)
        self.assertIn(res_5x["risk_level"], ("HIGH", "CRITICAL", "MEDIUM"))

    def test_link_failure_and_rerouting(self):
        # Mavjud birinchi liniyani uzamiz
        first_link_id = list(self.twin.graph.links.keys())[0]
        res = self.twin.simulate_link_failure(first_link_id)
        self.assertEqual(res["failure_type"], "link_cut")
        self.assertIn(first_link_id, res["affected_links"])
        self.assertIn("rerouted_flows", res)

    def test_worst_case_failures_ranking(self):
        rankings = self.twin.rank_worst_case_failures()
        self.assertGreater(len(rankings), 0)
        # Eng yuqori impact_score birinchi chiqishi kerak
        self.assertGreaterEqual(rankings[0]["impact_score"], rankings[-1]["impact_score"])
        self.assertEqual(rankings[0]["rank"], 1)


class TestAutoGrader(unittest.TestCase):
    def setUp(self):
        self.grader = NetAcadGrader()

    def test_grader_lab1_submission(self):
        submission = {
            "devices": {
                "h1": {
                    "type": "host",
                    "config": {
                        "interfaces": {
                            "eth0": {"ip": "192.168.1.10/24", "gateway": "192.168.1.1"}
                        }
                    },
                },
                "h2": {
                    "type": "host",
                    "config": {
                        "interfaces": {
                            "eth0": {"ip": "192.168.2.10/24", "gateway": "192.168.2.1"}
                        }
                    },
                },
                "r1": {
                    "type": "router",
                    "config": {
                        "interfaces": {
                            "eth0": {"ip": "192.168.1.1/24"},
                            "eth1": {"ip": "10.0.0.1/30"},
                        }
                    },
                },
                "r2": {
                    "type": "router",
                    "config": {
                        "interfaces": {
                            "eth0": {"ip": "192.168.2.1/24"},
                            "eth1": {"ip": "10.0.0.2/30"},
                        }
                    },
                },
            }
        }
        report = self.grader.grade("lab1", submission)

        self.assertGreaterEqual(report.percentage, 70.0)
        self.assertTrue(report.passed)
        self.assertEqual(report.lab_id, "lab1")
        self.assertIn("Congratulations", report.summary_feedback)
        self.assertGreater(len(report.checklist), 0)

    def test_grader_lab4_firewall(self):
        submission = {
            "devices": {
                "r1": {
                    "type": "router",
                    "config": {
                        "firewall": {
                            "default_policy": "drop",
                            "rules": [
                                {"action": "drop", "dport": 23, "proto": "tcp"},
                                {"action": "accept", "dport": 22, "proto": "tcp"},
                                {"action": "accept", "dport": 80, "proto": "tcp"},
                                {"action": "accept", "state": "established,related"},
                            ],
                        }
                    },
                }
            }
        }
        report = self.grader.grade("lab4", submission)
        self.assertGreaterEqual(report.percentage, 70.0)
        self.assertTrue(report.passed)


class TestTwinAndEducationEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = create_app()
        cls.client = TestClient(app)

    def test_twin_endpoints(self):
        # 1. Topology
        resp_topo = self.client.get("/api/twin/topology")
        self.assertEqual(resp_topo.status_code, 200)
        self.assertIn("links", resp_topo.json())

        # 2. Surge
        resp_surge = self.client.post("/api/twin/what-if/surge", json={"multiplier": 2.5})
        self.assertEqual(resp_surge.status_code, 200)
        self.assertEqual(resp_surge.json()["metrics"]["surge_factor"], 2.5)

        # 3. Link Down
        first_link_id = list(what_if_analyzer.graph.links.keys())[0]
        resp_ld = self.client.post("/api/twin/what-if/link-down", json={"link_id": first_link_id})
        self.assertEqual(resp_ld.status_code, 200)
        self.assertEqual(resp_ld.json()["failure_type"], "link_cut")

        # 4. Worst Case
        resp_wc = self.client.get("/api/twin/what-if/worst-case")
        self.assertEqual(resp_wc.status_code, 200)
        self.assertIn("rankings", resp_wc.json())

    def test_education_endpoints(self):
        # 1. Available labs
        resp_labs = self.client.get("/api/education/labs")
        self.assertEqual(resp_labs.status_code, 200)
        labs = resp_labs.json()["labs"]
        self.assertGreaterEqual(len(labs), 2)

        # 2. Grade submission
        payload = {
            "lab_id": "lab1",
            "submission": {
                "devices": {
                    "h1": {"type": "host", "config": {"interfaces": {"eth0": {"ip": "192.168.1.10/24", "gateway": "192.168.1.1"}}}},
                    "r1": {"type": "router", "config": {"interfaces": {"eth0": {"ip": "192.168.1.1/24"}}}},
                }
            },
        }
        resp_grade = self.client.post("/api/education/grade", json=payload)
        self.assertEqual(resp_grade.status_code, 200)
        data = resp_grade.json()
        self.assertTrue(data["ok"])
        self.assertIn("report", data)


if __name__ == "__main__":
    unittest.main()
