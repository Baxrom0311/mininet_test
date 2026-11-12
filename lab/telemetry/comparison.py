"""NetLab A/B Telemetry & Protocol Comparison Engine.
Provides comparative analysis across:
- TCP Congestion Control Algorithms (CUBIC vs BBR vs Reno)
- Queue Management Disciplines (DropTail/FIFO vs CoDel/RED/FQ-CoDel)
- Active Transport Streams side-by-side delta metrics
"""

from dataclasses import asdict, dataclass
import math
from typing import Any, Dict, List, Optional

from lab.telemetry.opnet_workloads import VoIPMOSCalculator


@dataclass
class MetricComparison:
    metric: str
    unit: str
    baseline: float
    candidate: float
    delta: float
    relative_pct: Optional[float]
    preferred_direction: str  # "higher" or "lower"
    winner: str  # "baseline", "candidate", or "tie"


class TelemetryComparator:
    """Transport va Navbat telemetriyasi bo'yicha A/B taqqoslash mexanizmi."""

    @classmethod
    def compare_streams(
        cls,
        stream_a: Dict[str, Any],
        stream_b: Dict[str, Any],
        label_a: str = "Oqim A (Baseline)",
        label_b: str = "Oqim B (Candidate)",
    ) -> Dict[str, Any]:
        """Ikkita faol L4 oqimining haqiqiy telemetriya ko'rsatkichlarini solishtirish."""
        # 1. Throughput
        rate_a = float(stream_a.get("delivery_rate_bps") or 0.0)
        rate_b = float(stream_b.get("delivery_rate_bps") or 0.0)

        # 2. RTT
        rtt_a = float(stream_a.get("rtt_ms") or 1.0)
        rtt_b = float(stream_b.get("rtt_ms") or 1.0)

        # 3. Jitter / RTTVAR
        jit_a = float(stream_a.get("rttvar_ms") or 0.5)
        jit_b = float(stream_b.get("rttvar_ms") or 0.5)

        # 4. Retransmissions
        ret_a = int(stream_a.get("total_retrans", stream_a.get("retrans", 0)))
        ret_b = int(stream_b.get("total_retrans", stream_b.get("retrans", 0)))

        # 5. CWND
        cwnd_a = int(stream_a.get("cwnd", stream_a.get("snd_cwnd", 10)))
        cwnd_b = int(stream_b.get("cwnd", stream_b.get("snd_cwnd", 10)))

        # Hisoblar
        metrics: List[MetricComparison] = []

        def add_comp(name: str, unit: str, val_a: float, val_b: float, higher_is_better: bool = True):
            delta = val_b - val_a
            rel = (delta / val_a * 100.0) if val_a != 0 else None
            pref = "higher" if higher_is_better else "lower"
            if abs(delta) < 1e-4:
                winner = "tie"
            elif (delta > 0 and higher_is_better) or (delta < 0 and not higher_is_better):
                winner = "candidate"
            else:
                winner = "baseline"

            metrics.append(
                MetricComparison(
                    metric=name,
                    unit=unit,
                    baseline=round(val_a, 2),
                    candidate=round(val_b, 2),
                    delta=round(delta, 2),
                    relative_pct=round(rel, 1) if rel is not None else None,
                    preferred_direction=pref,
                    winner=winner,
                )
            )

        add_comp("Yetkazib berish tezligi (Throughput)", "bps", rate_a, rate_b, higher_is_better=True)
        add_comp("Kechikish (Smoothed RTT)", "ms", rtt_a, rtt_b, higher_is_better=False)
        add_comp("Kechikish tebranishi (Jitter)", "ms", jit_a, jit_b, higher_is_better=False)
        add_comp("Qayta yuborilgan paketlar (Retransmissions)", "pkts", float(ret_a), float(ret_b), higher_is_better=False)
        add_comp("Tiqilinch oynasi (CWND)", "segments", float(cwnd_a), float(cwnd_b), higher_is_better=True)

        # VoIP MOS ta'siri (ITU-T G.107)
        # Yo'qotish foizini retransmit / total acked orqali taxminlash
        acked_a = max(1, stream_a.get("bytes_acked", 14600) // 1460)
        acked_b = max(1, stream_b.get("bytes_acked", 14600) // 1460)
        loss_pct_a = min(50.0, (ret_a / (ret_a + acked_a)) * 100.0)
        loss_pct_b = min(50.0, (ret_b / (ret_b + acked_b)) * 100.0)

        mos_a_res = VoIPMOSCalculator.calculate(delay_ms=rtt_a / 2.0, jitter_ms=jit_a, loss_pct=loss_pct_a)
        mos_b_res = VoIPMOSCalculator.calculate(delay_ms=rtt_b / 2.0, jitter_ms=jit_b, loss_pct=loss_pct_b)

        add_comp("VoIP QoE Sifati (MOS)", "score (1-4.5)", mos_a_res.mos, mos_b_res.mos, higher_is_better=True)

        # Xulosa xulosasi (Verdict)
        cand_wins = sum(1 for m in metrics if m.winner == "candidate")
        base_wins = sum(1 for m in metrics if m.winner == "baseline")

        if cand_wins > base_wins:
            verdict_uz = f"{label_b} {cand_wins}/{len(metrics)} ko'rsatkichlar bo'yicha {label_a} dan ustun keldi."
            verdict_en = f"{label_b} outperformed {label_a} on {cand_wins}/{len(metrics)} key metrics."
            overall_winner = "candidate"
        elif base_wins > cand_wins:
            verdict_uz = f"{label_a} {base_wins}/{len(metrics)} ko'rsatkichlar bo'yicha {label_b} dan ustun keldi."
            verdict_en = f"{label_a} outperformed {label_b} on {base_wins}/{len(metrics)} key metrics."
            overall_winner = "baseline"
        else:
            verdict_uz = f"{label_a} va {label_b} umumiy barqarorlikda teng natija ko'rsatdi."
            verdict_en = f"{label_a} and {label_b} performed equally overall."
            overall_winner = "tie"

        return {
            "label_a": label_a,
            "label_b": label_b,
            "stream_id_a": stream_a.get("stream_id", "A"),
            "stream_id_b": stream_b.get("stream_id", "B"),
            "algo_a": stream_a.get("congestion_algo", "CUBIC").upper(),
            "algo_b": stream_b.get("congestion_algo", "BBR").upper(),
            "metrics": [asdict(m) for m in metrics],
            "overall_winner": overall_winner,
            "verdict": {
                "uz": verdict_uz,
                "en": verdict_en,
            },
            "voip_eval_a": mos_a_res.to_dict(),
            "voip_eval_b": mos_b_res.to_dict(),
        }

    @classmethod
    def _model_algorithm_metrics(
        cls,
        algo: str,
        bottleneck_bps: float,
        base_rtt_ms: float,
        loss_pct: float,
        buffer_size_pkts: int,
    ) -> Dict[str, Any]:
        """Turli xil TCP tiqilinch nazorati algoritmlari (CUBIC, BBR, Reno) uchun analitik metrikalar."""
        algo = (algo or "cubic").lower().strip()
        eff_bottleneck = max(1000.0, float(bottleneck_bps or 20_000_000.0))
        eff_rtt_ms = max(0.1, float(base_rtt_ms or 30.0))
        eff_loss = max(0.0, min(100.0, float(loss_pct if loss_pct is not None else 1.0)))
        eff_buffer = max(1, int(buffer_size_pkts or 200))
        p = max(0.0001, eff_loss / 100.0)

        # BDP (Bandwidth-Delay Product)
        bdp_bytes = (eff_bottleneck * (eff_rtt_ms / 1000.0)) / 8.0
        bdp_packets = max(10, int(bdp_bytes / 1460))

        if algo == "bbr":
            # BBR model (BtlBw and RTprop):
            # Tasodifiy paket yo'qotishga sezgir emas (20% gacha barqaror)
            if eff_loss < 20.0:
                rate = eff_bottleneck * 0.94
            else:
                rate = eff_bottleneck * max(0.05, 1.0 - (eff_loss / 100.0))

            # BBR navbatda minimal paket ushlaydi (~1.15-1.25 BDP), Bufferbloat hosil qilmaydi
            q_delay_ms = (bdp_packets * 0.20 * 1460 * 8 / eff_bottleneck) * 1000.0
            rtt = eff_rtt_ms + q_delay_ms
            jitter = max(0.5, q_delay_ms * 0.2)
            retrans = int(rate * 5.0 * (eff_loss / 100.0) / (1460 * 8))
            cwnd = max(10, int((rate * (rtt / 1000.0)) / (1460 * 8)))
            ca_state = "open"

        elif algo in ("reno", "newreno"):
            # Reno AIMD model: Mathis formulasi bilan cheklanadi
            mathis_bps = (1.22 * 1460 * 8) / ((eff_rtt_ms / 1000.0) * math.sqrt(p))
            rate = min(eff_bottleneck * 0.88, mathis_bps)
            # Fills buffer until drop (DropTail bufferbloat)
            q_delay_ms = (eff_buffer * 1460 * 8 / eff_bottleneck) * 1000.0
            rtt = eff_rtt_ms + q_delay_ms * 0.75
            jitter = max(1.0, q_delay_ms * 0.4)
            retrans = int(rate * 5.0 * p / (1460 * 8))
            cwnd = max(10, int((rate * (rtt / 1000.0)) / (1460 * 8)))
            ca_state = "recovery" if eff_loss > 0 else "open"

        else:
            # Default CUBIC model (RFC 8312):
            # CUBIC oynasi C*(t-K)^3 bo'yicha tezroq tiklanadi, beta=0.7
            mathis_bps = (1.22 * 1460 * 8) / ((eff_rtt_ms / 1000.0) * math.sqrt(p))
            rate = min(eff_bottleneck * 0.95, mathis_bps * 1.15)
            # Navbat buferini to'ldirishga moyil
            q_delay_ms = (eff_buffer * 1460 * 8 / eff_bottleneck) * 1000.0
            rtt = eff_rtt_ms + q_delay_ms * 0.70
            jitter = max(0.8, q_delay_ms * 0.35)
            retrans = int(rate * 5.0 * p / (1460 * 8))
            cwnd = max(10, int((rate * (rtt / 1000.0)) / (1460 * 8)))
            ca_state = "recovery" if eff_loss > 0 else "open"

        return {
            "stream_id": f"sim_{algo}",
            "congestion_algo": algo,
            "delivery_rate_bps": round(rate, 2),
            "rtt_ms": round(rtt, 2),
            "rttvar_ms": round(jitter, 2),
            "total_retrans": max(0, retrans),
            "cwnd": cwnd,
            "bytes_acked": int(rate * 5.0 / 8.0),
            "ca_state": ca_state,
        }

    @classmethod
    def compare_algorithms(
        cls,
        algo_a: str = "cubic",
        algo_b: str = "bbr",
        bottleneck_bps: float = 20_000_000.0,  # 20 Mbps
        base_rtt_ms: float = 30.0,
        loss_pct: float = 1.0,
        buffer_size_pkts: int = 200,
    ) -> Dict[str, Any]:
        """Matematik va simulyatsiya modeli orqali turli TCP algoritmlarini (CUBIC, BBR, Reno) solishtirish."""
        clean_a = (algo_a or "cubic").lower().strip()
        clean_b = (algo_b or "bbr").lower().strip()
        eff_bottleneck = max(1000.0, float(bottleneck_bps or 20_000_000.0))
        eff_rtt_ms = max(0.1, float(base_rtt_ms or 30.0))
        eff_loss = max(0.0, min(100.0, float(loss_pct if loss_pct is not None else 1.0)))
        eff_buffer = max(1, int(buffer_size_pkts or 200))

        bdp_bytes = (eff_bottleneck * (eff_rtt_ms / 1000.0)) / 8.0

        stream_a = cls._model_algorithm_metrics(clean_a, eff_bottleneck, eff_rtt_ms, eff_loss, eff_buffer)
        stream_b = cls._model_algorithm_metrics(clean_b, eff_bottleneck, eff_rtt_ms, eff_loss, eff_buffer)

        label_a = f"{clean_a.upper()} ({'Model-Based' if clean_a == 'bbr' else 'Loss-Based'})"
        label_b = f"{clean_b.upper()} ({'Model-Based' if clean_b == 'bbr' else 'Loss-Based'})"

        res = cls.compare_streams(
            stream_a,
            stream_b,
            label_a=label_a,
            label_b=label_b,
        )

        res["simulation_parameters"] = {
            "bottleneck_bandwidth_bps": eff_bottleneck,
            "base_rtt_ms": eff_rtt_ms,
            "packet_loss_pct": eff_loss,
            "buffer_size_packets": eff_buffer,
            "bdp_bytes": int(bdp_bytes),
        }

        return res

    @classmethod
    def compare_queuing(
        cls,
        qdisc_a: str = "pfifo_fast",
        qdisc_b: str = "fq_codel",
        link_bps: float = 10_000_000.0,
        load_pct: float = 120.0,
    ) -> Dict[str, Any]:
        """FIFO/DropTail va FQ-CoDel/CAKE/AQM navbat algoritmlarini solishtirish."""
        eff_link_bps = max(1000.0, float(link_bps or 10_000_000.0))
        eff_load_pct = max(1.0, float(load_pct if load_pct is not None else 100.0))
        is_overloaded = eff_load_pct > 100.0
        clean_b = (qdisc_b or "fq_codel").lower().strip()

        if is_overloaded:
            fifo_queue_depth_pkts = 200
            fifo_sojourn_time_ms = (fifo_queue_depth_pkts * 1500 * 8 / eff_link_bps) * 1000.0
            fifo_drops = int((eff_load_pct - 100.0) * 10)
            fifo_bloat = True

            # AQM Target sojourn time (CAKE ~3.2ms, FQ-CoDel ~5.2ms)
            if clean_b == "cake":
                aqm_queue_depth_pkts = 10
                aqm_sojourn_time_ms = 3.2
                aqm_drops = int(fifo_drops * 0.70)
            else:
                aqm_queue_depth_pkts = 15
                aqm_sojourn_time_ms = 5.2
                aqm_drops = int(fifo_drops * 0.80)
            aqm_bloat = False
        else:
            fifo_queue_depth_pkts = 5
            fifo_sojourn_time_ms = 2.0
            fifo_drops = 0
            fifo_bloat = False

            aqm_queue_depth_pkts = 4
            aqm_sojourn_time_ms = 1.8 if clean_b != "cake" else 1.2
            aqm_drops = 0
            aqm_bloat = False

        rtt_base = 20.0
        rtt_fifo = rtt_base + fifo_sojourn_time_ms
        rtt_aqm = rtt_base + aqm_sojourn_time_ms

        mos_fifo = VoIPMOSCalculator.calculate(
            delay_ms=rtt_fifo / 2.0,
            jitter_ms=fifo_sojourn_time_ms * 0.3,
            loss_pct=min(20.0, fifo_drops * 0.1),
        )
        mos_aqm = VoIPMOSCalculator.calculate(
            delay_ms=rtt_aqm / 2.0,
            jitter_ms=aqm_sojourn_time_ms * 0.1,
            loss_pct=min(5.0, aqm_drops * 0.05),
        )

        reduction_pct = round(
            (1.0 - (aqm_sojourn_time_ms / max(0.1, fifo_sojourn_time_ms))) * 100.0, 1
        )

        return {
            "qdisc_a": qdisc_a,
            "qdisc_b": qdisc_b,
            "link_bandwidth_bps": eff_link_bps,
            "load_pct": eff_load_pct,
            "comparison": {
                "sojourn_time_ms": {
                    "baseline": round(fifo_sojourn_time_ms, 2),
                    "candidate": round(aqm_sojourn_time_ms, 2),
                    "delta_ms": round(aqm_sojourn_time_ms - fifo_sojourn_time_ms, 2),
                    "reduction_pct": reduction_pct,
                },
                "total_rtt_ms": {
                    "baseline": round(rtt_fifo, 2),
                    "candidate": round(rtt_aqm, 2),
                },
                "bufferbloat_detected": {
                    "baseline": fifo_bloat,
                    "candidate": aqm_bloat,
                },
                "voip_mos": {
                    "baseline": mos_fifo.mos,
                    "candidate": mos_aqm.mos,
                    "delta": round(mos_aqm.mos - mos_fifo.mos, 2),
                },
            },
            "recommendation": (
                f"{qdisc_b.upper()} navbat kechikishini {round(reduction_pct)}% ga qisqartirdi "
                f"va Bufferbloat holatining oldini oldi. Interaktiv oqimlar (VoIP) uchun MOS {mos_fifo.mos} dan {mos_aqm.mos} ga yaxshilandi."
            ),
        }
