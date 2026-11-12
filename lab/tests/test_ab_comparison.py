import unittest
from lab.engines.ab_comparison import (
    ExperimentComparator,
    ComparisonReportExporter,
    MetricComparison,
    ABComparisonSummary,
)


class TestABComparison(unittest.TestCase):
    def setUp(self):
        self.cubic_data = {
            "throughput_mbps": 42.5,
            "avg_rtt_ms": 68.2,
            "retransmissions": 140,
            "jitter_ms": 7.4,
        }
        self.bbr_data = {
            "throughput_mbps": 88.0,
            "avg_rtt_ms": 24.1,
            "retransmissions": 12,
            "jitter_ms": 2.2,
        }

    def test_comparator_bbr_beats_cubic(self):
        comparator = ExperimentComparator(name_a="TCP Cubic", name_b="TCP BBR")
        summary = comparator.compare_runs(self.cubic_data, self.bbr_data)

        self.assertEqual(summary.name_a, "TCP Cubic")
        self.assertEqual(summary.name_b, "TCP BBR")
        self.assertEqual(summary.overall_winner, "TCP BBR")
        self.assertIn("TCP BBR", summary.summary_verdict)

        # Check throughput metric
        thr = summary.metrics["throughput"]
        self.assertEqual(thr.better_run, "B")
        self.assertGreater(thr.delta_pct, 100.0)

        # Check RTT metric
        rtt = summary.metrics["rtt"]
        self.assertEqual(rtt.better_run, "B")
        self.assertLess(rtt.delta_pct, 0.0)

        # Check retransmissions metric
        loss = summary.metrics["retransmissions"]
        self.assertEqual(loss.better_run, "B")
        self.assertLess(loss.delta_abs, 0)

        # Check jitter
        jit = summary.metrics["jitter"]
        self.assertEqual(jit.better_run, "B")

    def test_comparator_tie_and_variant_a_wins(self):
        # Scenario where Variant A wins
        comp_a = ExperimentComparator(name_a="Optimized A", name_b="Standard B")
        summary_a = comp_a.compare_runs(self.bbr_data, self.cubic_data)
        self.assertEqual(summary_a.overall_winner, "Optimized A")

        # Scenario tie
        comp_tie = ExperimentComparator(name_a="Run 1", name_b="Run 2")
        identical_data = {"throughput_mbps": 50.0, "avg_rtt_ms": 30.0, "retransmissions": 5, "jitter_ms": 1.0}
        summary_tie = comp_tie.compare_runs(identical_data, identical_data)
        self.assertEqual(summary_tie.overall_winner, "Teng (Tie)")

    def test_to_dict_serialization(self):
        comparator = ExperimentComparator(name_a="A", name_b="B")
        summary = comparator.compare_runs(self.cubic_data, self.bbr_data)
        d = summary.to_dict()

        self.assertIsInstance(d, dict)
        self.assertEqual(d["name_a"], "A")
        self.assertEqual(d["name_b"], "B")
        self.assertEqual(d["overall_winner"], "B")
        self.assertIn("throughput", d["metrics"])
        self.assertIn("rtt", d["metrics"])

    def test_report_exporter_markdown(self):
        comparator = ExperimentComparator(name_a="DropTail", name_b="CoDel")
        summary = comparator.compare_runs(self.cubic_data, self.bbr_data)
        md = ComparisonReportExporter.to_markdown(summary)

        self.assertIn("# A/B Tajriba Solishtirma Hisoboti", md)
        self.assertIn("DropTail", md)
        self.assertIn("CoDel", md)
        self.assertIn("| Ko'rsatkich |", md)
        self.assertIn("🏆 **CoDel**", md)

    def test_report_exporter_latex(self):
        comparator = ExperimentComparator(name_a="DropTail", name_b="CoDel")
        summary = comparator.compare_runs(self.cubic_data, self.bbr_data)
        tex = ComparisonReportExporter.to_latex_table(summary)

        self.assertIn(r"\begin{table}[htbp]", tex)
        self.assertIn(r"\caption{A/B Comparison: DropTail vs CoDel}", tex)
        self.assertIn(r"\begin{tabular}", tex)
        self.assertIn(r"\end{table}", tex)

    def test_report_exporter_html(self):
        comparator = ExperimentComparator(name_a="Cubic", name_b="BBR")
        summary = comparator.compare_runs(self.cubic_data, self.bbr_data)
        html = ComparisonReportExporter.to_html(summary)

        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("<title>A/B Comparison: Cubic vs BBR</title>", html)
        self.assertIn("🏆 <strong>G'olib:</strong> BBR", html)
        self.assertIn("<table>", html)
        self.assertIn("</table>", html)


if __name__ == "__main__":
    unittest.main()
