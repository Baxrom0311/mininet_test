"""A/B Experiment Comparison & Multi-Format Report Exporter:
1. ExperimentComparator: Ikki yoki undan ortiq simulyatsiya natijasini
   (Cubic vs BBR, DropTail vs CoDel, OSPF vs IS-IS) ko'p parametrli taqqoslaydi.
2. ComparisonReportExporter: Natijalarni Markdown, HTML, LaTeX va JSON
   formatlariga professional eksport qiladi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class MetricComparison:
    metric_name: str
    run_a_value: float
    run_b_value: float
    unit: str
    delta_abs: float
    delta_pct: float
    better_run: str  # "A", "B", yoki "equal"
    interpretation: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "run_a_value": round(self.run_a_value, 2),
            "run_b_value": round(self.run_b_value, 2),
            "unit": self.unit,
            "delta_abs": round(self.delta_abs, 2),
            "delta_pct": round(self.delta_pct, 2),
            "better_run": self.better_run,
            "interpretation": self.interpretation,
        }


@dataclass
class ABComparisonSummary:
    name_a: str
    name_b: str
    metrics: Dict[str, MetricComparison]
    overall_winner: str
    summary_verdict: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name_a": self.name_a,
            "name_b": self.name_b,
            "overall_winner": self.overall_winner,
            "summary_verdict": self.summary_verdict,
            "metrics": {k: v.to_dict() for k, v in self.metrics.items()},
        }


class ExperimentComparator:
    """Ikki xil parametr yoki algoritm bilan bajarilgan tajribalarni solishtiradi."""

    def __init__(self, name_a: str = "Variant A", name_b: str = "Variant B"):
        self.name_a = name_a
        self.name_b = name_b

    def compare_runs(
        self,
        data_a: Dict[str, Any],
        data_b: Dict[str, Any],
        sla_max_rtt_ms: float = 50.0,
        sla_min_thr_mbps: float = 10.0,
    ) -> ABComparisonSummary:
        metrics: Dict[str, MetricComparison] = {}
        wins_a = 0
        wins_b = 0

        # 1. Throughput (yuqori bo'lgani yaxshi)
        thr_a = float(data_a.get("throughput_mbps", 0.0))
        thr_b = float(data_b.get("throughput_mbps", 0.0))
        thr_delta = thr_b - thr_a
        thr_pct = (thr_delta / thr_a * 100.0) if thr_a > 0 else 0.0
        better_thr = "B" if thr_b > thr_a else ("A" if thr_a > thr_b else "equal")
        if better_thr == "B":
            wins_b += 1
        elif better_thr == "A":
            wins_a += 1

        metrics["throughput"] = MetricComparison(
            metric_name="O'tkazuvchanlik (Throughput)",
            run_a_value=thr_a,
            run_b_value=thr_b,
            unit="Mbps",
            delta_abs=thr_delta,
            delta_pct=thr_pct,
            better_run=better_thr,
            interpretation=f"{self.name_b} o'tkazuvchanlikni {abs(thr_pct):.1f}% ga {'oshirdi' if thr_delta > 0 else 'kamaytirdi'}",
        )

        # 2. RTT / Latency (past bo'lgani yaxshi)
        rtt_a = float(data_a.get("avg_rtt_ms", 1.0))
        rtt_b = float(data_b.get("avg_rtt_ms", 1.0))
        rtt_delta = rtt_b - rtt_a
        rtt_pct = (rtt_delta / rtt_a * 100.0) if rtt_a > 0 else 0.0
        better_rtt = "B" if rtt_b < rtt_a else ("A" if rtt_a < rtt_b else "equal")
        if better_rtt == "B":
            wins_b += 1
        elif better_rtt == "A":
            wins_a += 1

        metrics["rtt"] = MetricComparison(
            metric_name="O'rtacha RTT (Kechikish)",
            run_a_value=rtt_a,
            run_b_value=rtt_b,
            unit="ms",
            delta_abs=rtt_delta,
            delta_pct=rtt_pct,
            better_run=better_rtt,
            interpretation=f"{self.name_b} kechikishni {abs(rtt_pct):.1f}% ga {'kamaytirdi (yaxshiroq)' if rtt_delta < 0 else 'oshirdi'}",
        )

        # 3. Retransmissions / Packet Loss (past bo'lgani yaxshi)
        loss_a = float(data_a.get("retransmissions", 0))
        loss_b = float(data_b.get("retransmissions", 0))
        loss_delta = loss_b - loss_a
        loss_pct = (loss_delta / loss_a * 100.0) if loss_a > 0 else 0.0
        better_loss = "B" if loss_b < loss_a else ("A" if loss_a < loss_b else "equal")
        if better_loss == "B":
            wins_b += 1
        elif better_loss == "A":
            wins_a += 1

        metrics["retransmissions"] = MetricComparison(
            metric_name="Qayta yuborishlar (Retransmissions)",
            run_a_value=loss_a,
            run_b_value=loss_b,
            unit="paket",
            delta_abs=loss_delta,
            delta_pct=loss_pct,
            better_run=better_loss,
            interpretation=f"{self.name_b} qayta yuborishlarni {abs(loss_delta):.0f} taga {'kamaytirdi' if loss_delta < 0 else 'oshirdi'}",
        )

        # 4. Jitter (past bo'lgani yaxshi)
        jit_a = float(data_a.get("jitter_ms", 0.0))
        jit_b = float(data_b.get("jitter_ms", 0.0))
        jit_delta = jit_b - jit_a
        jit_pct = (jit_delta / jit_a * 100.0) if jit_a > 0 else 0.0
        better_jit = "B" if jit_b < jit_a else ("A" if jit_a < jit_b else "equal")
        if better_jit == "B":
            wins_b += 1
        elif better_jit == "A":
            wins_a += 1

        metrics["jitter"] = MetricComparison(
            metric_name="Jitter (Tebranish)",
            run_a_value=jit_a,
            run_b_value=jit_b,
            unit="ms",
            delta_abs=jit_delta,
            delta_pct=jit_pct,
            better_run=better_jit,
            interpretation=f"{self.name_b} jitter tebranishini {abs(jit_delta):.2f} ms ga {'yaxshiladi' if jit_delta < 0 else 'yomonlashtirdi'}",
        )

        # G'olibni aniqlash
        if wins_b > wins_a:
            winner = self.name_b
            verdict = f"{self.name_b} variant {self.name_a} ga nisbatan {wins_b}/{len(metrics)} ta asosiy ko'rsatkich bo'yicha ustun keldi."
        elif wins_a > wins_b:
            winner = self.name_a
            verdict = f"{self.name_a} variant {self.name_b} ga nisbatan {wins_a}/{len(metrics)} ta asosiy ko'rsatkich bo'yicha ustun keldi."
        else:
            winner = "Teng (Tie)"
            verdict = f"Ikkala variant ham teng ko'rsatkichlarga ega."

        return ABComparisonSummary(
            name_a=self.name_a,
            name_b=self.name_b,
            metrics=metrics,
            overall_winner=winner,
            summary_verdict=verdict,
        )


class ComparisonReportExporter:
    """Solishtirma natijalarni turli ilmiy formatlarga eksport qiladi."""

    @staticmethod
    def to_markdown(summary: ABComparisonSummary) -> str:
        lines = [
            f"# A/B Tajriba Solishtirma Hisoboti",
            f"**Variant A:** `{summary.name_a}` | **Variant B:** `{summary.name_b}`",
            f"**Umumiy Xulosa:** 🏆 **{summary.overall_winner}**",
            f"> {summary.summary_verdict}",
            "",
            "## 1. Ko'rsatkichlar Taqqoslash Jadvali",
            "",
            f"| Ko'rsatkich | {summary.name_a} | {summary.name_b} | Delta (Farq) | G'olib |",
            "|---|---|---|---|---|",
        ]
        for m in summary.metrics.values():
            delta_str = f"{'+' if m.delta_abs > 0 else ''}{m.delta_abs:.2f} {m.unit} ({m.delta_pct:+.1f}%)"
            winner_badge = f"**{summary.name_a if m.better_run == 'A' else summary.name_b}**" if m.better_run != "equal" else "Teng"
            lines.append(
                f"| {m.metric_name} | {m.run_a_value:.2f} {m.unit} | {m.run_b_value:.2f} {m.unit} | {delta_str} | {winner_badge} |"
            )

        lines.extend([
            "",
            "## 2. Tavsiyalar va Xulosa",
            f"- Eng yaxshi natija: **{summary.overall_winner}**",
        ])
        for m in summary.metrics.values():
            lines.append(f"- **{m.metric_name}**: {m.interpretation}")

        return "\n".join(lines)

    @staticmethod
    def to_latex_table(summary: ABComparisonSummary) -> str:
        """Ilmiy maqolalar uchun LaTeX jadval formati."""
        lines = [
            r"\begin{table}[htbp]",
            r"\centering",
            rf"\caption{{A/B Comparison: {summary.name_a} vs {summary.name_b}}}",
            r"\begin{tabular}{lcccc}",
            r"\hline",
            rf"\textbf{{Metric}} & \textbf{{{summary.name_a}}} & \textbf{{{summary.name_b}}} & \textbf{{Delta (\%)}} & \textbf{{Winner}} \\",
            r"\hline",
        ]
        for m in summary.metrics.values():
            w = summary.name_a if m.better_run == "A" else (summary.name_b if m.better_run == "B" else "Tie")
            lines.append(
                rf"{m.metric_name} & {m.run_a_value:.2f} & {m.run_b_value:.2f} & {m.delta_pct:+.1f}\% & \textbf{{{w}}} \\"
            )
        lines.extend([
            r"\hline",
            r"\end{tabular}",
            r"\end{table}",
        ])
        return "\n".join(lines)

    @staticmethod
    def to_html(summary: ABComparisonSummary) -> str:
        """Veb ko'rish uchun mustaqil chiroyli HTML hisoboti."""
        rows = []
        for m in summary.metrics.values():
            w = summary.name_a if m.better_run == "A" else (summary.name_b if m.better_run == "B" else "Teng")
            is_b_win = m.better_run == "B"
            color = "#10b981" if is_b_win else ("#ef4444" if m.better_run == "A" else "#6b7280")
            rows.append(
                f"""<tr>
                    <td><strong>{m.metric_name}</strong></td>
                    <td>{m.run_a_value:.2f} {m.unit}</td>
                    <td>{m.run_b_value:.2f} {m.unit}</td>
                    <td style="color:{color};font-weight:bold;">{m.delta_pct:+.1f}%</td>
                    <td><span style="background:{color}22;color:{color};padding:2px 8px;border-radius:4px;font-weight:bold;">{w}</span></td>
                </tr>"""
            )

        html = f"""<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="utf-8">
<title>A/B Comparison: {summary.name_a} vs {summary.name_b}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 24px; }}
.card {{ background: #1e293b; border-radius: 12px; padding: 20px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.3); max-width: 800px; margin: 0 auto; }}
h1 {{ margin-top: 0; color: #38bdf8; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #334155; }}
th {{ background: #0f172a; color: #94a3b8; font-size: 13px; text-transform: uppercase; }}
.verdict {{ background: #0284c722; border-left: 4px solid #38bdf8; padding: 12px; border-radius: 4px; margin: 16px 0; }}
</style>
</head>
<body>
<div class="card">
    <h1>A/B Tajriba Solishtiruvi</h1>
    <p><strong>Variant A:</strong> {summary.name_a} &nbsp;|&nbsp; <strong>Variant B:</strong> {summary.name_b}</p>
    <div class="verdict">🏆 <strong>G'olib:</strong> {summary.overall_winner}<br>{summary.summary_verdict}</div>
    <table>
        <thead><tr><th>Ko'rsatkich</th><th>{summary.name_a}</th><th>{summary.name_b}</th><th>Delta</th><th>G'olib</th></tr></thead>
        <tbody>
            {''.join(rows)}
        </tbody>
    </table>
</div>
</body>
</html>"""
        return html
