"""Unit and Integration Test Suite for Faza 10:
Network Digital Twin, What-If Predictive Scenario Solver & Cisco NetAcad Auto-Grader.

Tests cover:
1. Network Digital Twin (TopologyGraph, Dijkstra SPF, baseline flow routing).
2. What-If Predictive Scenario Solver:
   - Link cut (alternate SPF path, convergence delay, risk level).
   - Node failure (stranded nodes/subnets, Dijkstra recalculation).
   - Traffic surge (link saturation, bufferbloat risk, drop rate).
   - Worst-case failure ranking and WhatIfReport formatting.
3. Interactive Cisco NetAcad style Auto-Grader:
   - Lab 1: Basic IP & Subnetting.
   - Lab 2: Single-Area OSPF Configuration.
   - Lab 3: VLANs and 802.1Q Trunking.
   - Lab 4: Router ACL & Firewall.
   - Failing submission scoring & remediation feedback.
4. FastAPI REST Endpoints:
   - /api/twin/topology, /api/twin/scenarios, /api/twin/what_if, /api/twin/what-if/surge,
     /api/twin/what-if/link-down, /api/twin/what-if/worst-case.
   - /api/education/labs, /api/education/rubrics, /api/education/rubrics/{lab_id},
     /api/education/grade.
"""

from __future__ import annotations

import unittest
from fastapi.testclient import TestClient

from lab.server import create_app
from lab.twin.what_if_analyzer import (
    FailureType,
    RiskLevel,
    TopologyGraph,
    TopologyLink,
    TopologyNode,
    TrafficFlow,
    WhatIfAnalyzer,
    WhatIfReport,
    what_if_analyzer,
)
from lab.education import (
    AutoGraderEngine,
    GradeReport,
    LAB_RUBRICS,
    NetAcadGrader,
    auto_grader,
    get_lab_template,
    list_available_labs,
)


class TestDigitalTwinWhatIfAnalysis(unittest.TestCase):
    """Network Digital Twin va What-If stsenariy yechuvchisi sinovlari."""

    def setUp(self):
        self.twin = WhatIfAnalyzer()

    def test_baseline_topology_and_initial_routing(self):
        """Boshlang'ich topologiya grafigi va oqimlar holati tekshiruvi."""
        status = self.twin.get_topology_status()
        self.assertGreaterEqual(status["node_count"], 6)
        self.assertGreaterEqual(status["link_count"], 6)
        self.assertGreaterEqual(status["flow_count"], 2)
        self.assertIn("links", status)
        self.assertIn("nodes", status)

    def test_traffic_multiplier_surge(self):
        """Trafik oqimi 1.5x va 5.0x / 10.0x ortganda saturatsiya va paket yo'qotish."""
        # Kichik yuklama (1.5x)
        res_1_5 = self.twin.simulate_traffic_multiplier(1.5)
        self.assertEqual(res_1_5["failure_type"], "traffic_surge")
        self.assertEqual(res_1_5["metrics"]["surge_factor"], 1.5)

        # Katta yuklama (5.0x) - ayrim kanallar saturatsiyaga uchrab risk oshadi
        res_5x = self.twin.simulate_traffic_multiplier(5.0)
        self.assertGreater(res_5x["metrics"]["max_utilization_pct"], 80.0)
        self.assertIn(res_5x["risk_level"], ("HIGH", "CRITICAL", "MEDIUM"))
        self.assertGreater(len(res_5x["predicted_congestion_links"]), 0)

        # 10.0x favqulodda burst
        res_10x = self.twin.predict_traffic_surge(surge_factor=10.0)
        self.assertEqual(res_10x.risk_level, RiskLevel.CRITICAL)
        self.assertTrue(any(c["drop_rate_pct"] > 0 for c in res_10x.predicted_congestion_links))

    def test_link_cut_failure_and_rerouting(self):
        """Kanal uzilishi, muqobil yo'l topilishi va kechikish o'zgarishi."""
        report = self.twin.predict_link_cut("link_c1_c2")
        self.assertEqual(report.failure_type, FailureType.LINK_CUT)
        self.assertIn("link_c1_c2", report.affected_links)
        self.assertGreater(len(report.rerouted_flows), 0)

        # simulate_link_failure qulay metod tekshiruvi
        first_link_id = list(self.twin.graph.links.keys())[0]
        res = self.twin.simulate_link_failure(first_link_id)
        self.assertEqual(res["failure_type"], "link_cut")
        self.assertIn(first_link_id, res["affected_links"])
        self.assertIn("rerouted_flows", res)

    def test_node_failure_and_spf_graph(self):
        """Tugun (Core-1 router) ishdan chiqqanda Dijkstra SPF qayta hisoblash va uzilgan tugunlar."""
        report = self.twin.predict_node_failure("Core-1")
        self.assertEqual(report.failure_type, FailureType.NODE_FAILURE)
        self.assertGreater(len(report.affected_links), 0)
        self.assertIn(report.risk_level, (RiskLevel.HIGH, RiskLevel.CRITICAL))
        self.assertIsInstance(report.stranded_nodes, list)

    def test_whatif_evaluate_scenarios(self):
        """Universal evaluate metodi orqali turli nosozliklarni baholash."""
        # Link cut
        rep1 = self.twin.evaluate({"failure_type": "link_cut", "target": "link_c1_c2"})
        self.assertEqual(rep1.failure_type, FailureType.LINK_CUT)

        # Traffic surge
        rep2 = self.twin.evaluate({"failure_type": "traffic_surge", "surge_factor": 3.0})
        self.assertEqual(rep2.failure_type, FailureType.TRAFFIC_SURGE)

        # Node failure
        rep3 = self.twin.evaluate({"failure_type": "node_failure", "target_node": "Dist-1"})
        self.assertEqual(rep3.failure_type, FailureType.NODE_FAILURE)

    def test_worst_case_failures_ranking(self):
        """Eng xavfli kanallar uzilishini darajalash (worst case ranking)."""
        rankings = self.twin.rank_worst_case_failures()
        self.assertGreater(len(rankings), 0)
        self.assertEqual(rankings[0]["rank"], 1)
        # Reyting kamayish tartibida saralangan bo'lishi kerak
        self.assertGreaterEqual(rankings[0]["impact_score"], rankings[-1]["impact_score"])

    def test_whatif_report_structure(self):
        """WhatIfReport to_dict() formati va barcha maydonlarning mavjudligi."""
        rep = self.twin.predict_traffic_surge(surge_factor=2.0)
        d = rep.to_dict()
        required_keys = [
            "scenario_id",
            "failure_type",
            "affected_links",
            "rerouted_flows",
            "predicted_congestion_links",
            "risk_level",
            "recommendations",
            "stranded_nodes",
            "reroute_delay_ms",
            "metrics",
        ]
        for key in required_keys:
            self.assertIn(key, d)


class TestNetAcadAutoGrader(unittest.TestCase):
    """Cisco NetAcad uslubidagi avtomatik baholash tizimi sinovlari."""

    def setUp(self):
        self.grader = NetAcadGrader()

    def test_rubrics_catalog_and_templates(self):
        """Mavjud rubrikalar katalogi va shablonlar tekshiruvi."""
        self.assertGreaterEqual(len(LAB_RUBRICS), 4)
        available_labs = list_available_labs()
        lab_ids = [l["lab_id"] for l in available_labs]
        self.assertIn("lab1", lab_ids)
        self.assertIn("lab2", lab_ids)
        self.assertIn("lab3", lab_ids)
        self.assertIn("lab4", lab_ids)

        r1 = NetAcadGrader.find_rubric("lab1")
        self.assertIsNotNone(r1)
        self.assertEqual(r1["lab_id"], "lab1")

    def test_grader_lab1_basic_ip_subnetting(self):
        """Lab 1: IP addressing, subnet mask (/24, /30), va default gateway tekshiruvi."""
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

    def test_grader_lab2_ospf_configuration(self):
        """Lab 2: Single-Area OSPF konfiguratsiyasi, router-id va p2p interfeys."""
        submission = {
            "devices": {
                "r1": {
                    "type": "router",
                    "config": {
                        "daemons": ["zebra", "ospfd"],
                        "startup": (
                            "router ospf\n"
                            " ospf router-id 1.1.1.1\n"
                            " network 192.168.1.0/24 area 0\n"
                            " network 10.0.0.0/30 area 0\n"
                            "interface eth1\n"
                            " ip ospf network point-to-point\n"
                            " ip ospf hello-interval 1\n"
                            " ip ospf dead-interval 4\n"
                        ),
                        "interfaces": {
                            "eth0": {"ip": "192.168.1.1/24"},
                            "eth1": {"ip": "10.0.0.1/30"},
                        },
                    },
                },
                "r2": {
                    "type": "router",
                    "config": {
                        "daemons": ["zebra", "ospfd"],
                        "startup": (
                            "router ospf\n"
                            " ospf router-id 2.2.2.2\n"
                            " network 192.168.2.0/24 area 0\n"
                            " network 10.0.0.0/30 area 0\n"
                            "interface eth1\n"
                            " ip ospf network point-to-point\n"
                            " ip ospf hello-interval 1\n"
                            " ip ospf dead-interval 4\n"
                        ),
                        "interfaces": {
                            "eth0": {"ip": "192.168.2.1/24"},
                            "eth1": {"ip": "10.0.0.2/30"},
                        },
                    },
                },
            }
        }
        report = self.grader.grade("lab2", submission)
        self.assertGreaterEqual(report.percentage, 70.0)
        self.assertTrue(report.passed)
        self.assertEqual(report.lab_id, "lab2")

    def test_grader_lab3_vlans_and_trunking(self):
        """Lab 3: Switch VLAN 10/20, access portlar, 802.1Q trunk va subinterfeyslar."""
        submission = {
            "devices": {
                "sw1": {
                    "type": "switch",
                    "config": {
                        "ports": {
                            "port1": {"mode": "access", "vlan": 10},
                            "port2": {"mode": "access", "vlan": 20},
                            "port3": {"mode": "trunk", "trunk": True, "allowed": [10, 20]},
                        }
                    },
                },
                "r1": {
                    "type": "router",
                    "config": {
                        "interfaces": {
                            "eth1.10": {"ip": "192.168.10.1/24"},
                            "eth1.20": {"ip": "192.168.20.1/24"},
                        }
                    },
                },
                "h1": {
                    "type": "host",
                    "config": {"interfaces": {"eth0": {"ip": "192.168.10.10/24"}}},
                },
                "h2": {
                    "type": "host",
                    "config": {"interfaces": {"eth0": {"ip": "192.168.20.20/24"}}},
                },
            }
        }
        report = self.grader.grade("lab3", submission)
        self.assertGreaterEqual(report.percentage, 70.0)
        self.assertTrue(report.passed)
        self.assertEqual(report.lab_id, "lab3")

    def test_grader_lab4_firewall_and_acl(self):
        """Lab 4: Firewall qoidalari (port 23 drop, 22 permit, 80 permit, default deny)."""
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
        self.assertEqual(report.lab_id, "lab4")

    def test_grader_failing_submission_feedback(self):
        """Talaba xato topshirganida (0 ball yoki past ball) tushuntirish va status."""
        bad_submission = {
            "devices": {
                "r1": {"type": "router", "config": {}},
            }
        }
        report = self.grader.grade("lab4", bad_submission)
        self.assertLess(report.percentage, 70.0)
        self.assertFalse(report.passed)
        self.assertIn("not fully met", report.summary_feedback)
        self.assertTrue(any(c["status"] == "FAIL" for c in report.checklist))

    def test_grade_report_structure(self):
        """GradeReport to_dict() formati to'liq tekshiruvi."""
        rep = self.grader.grade("lab1", {"devices": {}})
        d = rep.to_dict()
        for field in ("lab_id", "total_score", "max_score", "percentage", "passed", "checklist", "summary_feedback"):
            self.assertIn(field, d)


class TestTwinAndEducationFastAPIEndpoints(unittest.TestCase):
    """FastAPI Twin va Education REST endpointlar integratsion testlari."""

    @classmethod
    def setUpClass(cls):
        app = create_app()
        cls.client = TestClient(app)

    def test_twin_endpoints(self):
        """Digital Twin REST endpointlari (/api/twin/*)."""
        # 1. Topology
        resp_topo = self.client.get("/api/twin/topology")
        self.assertEqual(resp_topo.status_code, 200)
        self.assertIn("links", resp_topo.json())

        # 2. Scenarios
        resp_scen = self.client.get("/api/twin/scenarios")
        self.assertEqual(resp_scen.status_code, 200)
        self.assertIn("scenarios", resp_scen.json())

        # 3. What-If general evaluate
        resp_eval = self.client.post(
            "/api/twin/what_if",
            json={"failure_type": "traffic_surge", "surge_factor": 2.0},
        )
        self.assertEqual(resp_eval.status_code, 200)
        self.assertEqual(resp_eval.json()["failure_type"], "traffic_surge")

        # 4. Surge endpoint
        resp_surge = self.client.post("/api/twin/what-if/surge", json={"multiplier": 2.5})
        self.assertEqual(resp_surge.status_code, 200)
        self.assertEqual(resp_surge.json()["metrics"]["surge_factor"], 2.5)

        # 5. Link Down endpoint
        first_link_id = list(what_if_analyzer.graph.links.keys())[0]
        resp_ld = self.client.post("/api/twin/what-if/link-down", json={"link_id": first_link_id})
        self.assertEqual(resp_ld.status_code, 200)
        self.assertEqual(resp_ld.json()["failure_type"], "link_cut")

        # 6. Worst Case endpoint
        resp_wc = self.client.get("/api/twin/what-if/worst-case")
        self.assertEqual(resp_wc.status_code, 200)
        self.assertIn("rankings", resp_wc.json())

    def test_education_endpoints(self):
        """Auto-Grader REST endpointlari (/api/education/*)."""
        # 1. Available labs
        resp_labs = self.client.get("/api/education/labs")
        self.assertEqual(resp_labs.status_code, 200)
        labs = resp_labs.json()["labs"]
        self.assertGreaterEqual(len(labs), 2)

        # 2. Rubrics
        resp_rubrics = self.client.get("/api/education/rubrics")
        self.assertEqual(resp_rubrics.status_code, 200)
        self.assertIn("rubrics", resp_rubrics.json())

        # 3. Rubric detail
        resp_rubric_lab1 = self.client.get("/api/education/rubrics/lab1")
        self.assertEqual(resp_rubric_lab1.status_code, 200)
        self.assertEqual(resp_rubric_lab1.json()["lab_id"], "lab1")

        # Rubric 404
        resp_rubric_404 = self.client.get("/api/education/rubrics/nonexistent_lab_999")
        self.assertEqual(resp_rubric_404.status_code, 404)

        # 4. Grade submission
        payload = {
            "lab_id": "lab1",
            "submission": {
                "devices": {
                    "h1": {
                        "type": "host",
                        "config": {"interfaces": {"eth0": {"ip": "192.168.1.10/24", "gateway": "192.168.1.1"}}},
                    },
                    "r1": {
                        "type": "router",
                        "config": {"interfaces": {"eth0": {"ip": "192.168.1.1/24"}}},
                    },
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
