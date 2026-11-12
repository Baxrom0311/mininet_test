import unittest
from lab.education.grader import NetAcadGrader, LAB_RUBRICS


class TestEducationGrader(unittest.TestCase):
    def test_rubrics_exist(self):
        self.assertGreaterEqual(len(LAB_RUBRICS), 4)
        r1 = NetAcadGrader.find_rubric("lab1")
        self.assertIsNotNone(r1)
        self.assertEqual(r1["lab_id"], "lab1")

    def test_grading_lab1_subnetting(self):
        grader = NetAcadGrader()
        # Test with a mock topology
        topo = {
            "name": "lab1_test",
            "devices": {
                "R1": {
                    "config": {
                        "interfaces": {
                            "R1-eth1": {"ip": "192.168.1.1/24"},
                            "R1-eth2": {"ip": "10.0.0.1/30"},
                        }
                    }
                },
                "PC1": {
                    "config": {
                        "interfaces": {"PC1-eth1": {"ip": "192.168.1.10/24"}},
                        "gateway": "192.168.1.1",
                    }
                }
            }
        }
        report = grader.grade("lab1", topo)
        self.assertGreater(report.max_score, 0)
        self.assertIsInstance(report.percentage, float)
        self.assertIsInstance(report.checklist, list)
        self.assertIsNotNone(report.summary_feedback)


if __name__ == "__main__":
    unittest.main()
