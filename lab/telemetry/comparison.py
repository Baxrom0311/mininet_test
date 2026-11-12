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
    def compare_algorithms(
        cls,
        algo_a: str = "cubic",
        algo_b: str = "bbr",
        bottleneck_bps: float = 20_000_000.0,  # 20 Mbps
        base_rtt_ms: float = 30.0,
        loss_pct: float = 1.0,
        buffer_size_pkts: int = 200,
    ) -> Dict[str, Any]:
        """Matematik va simulyatsiya modeli orqali CUBIC va BBR algoritmlarini turli tarmoq sharoitlarida solishtirish."""
        algo_a = algo_a.lower()
        algo_b = algo_b.lower()

        # BDP (Bandwidth-Delay Product) in bytes
        bdp_bytes = (bottleneck_bps * (base_rtt_ms / 1000.0)) / 8.0
        bdp_packets = max(10, int(bdp_bytes / 1460))

        # 1. CUBIC model:
        # CUBIC tiqilinch holatida buferni to'ldiradi (Bufferbloat). Navbat kechikishi bufer o'lchamiga bog'liq.
        # Agar yo'qotish bo'lsa (p > 0), Mathis formulasi / CUBIC throughput modeli:
        p = max(0.0001, loss_pct / 100.0)
        # Mathis limit: Throughput <= (1.22 * MSS) / (RTT * sqrt(p))
        mathis_bps = (1.22 * 1460 * 8) / ((base_rtt_ms / 1000.0) * math.sqrt(p))
        cubic_rate = min(bottleneck_bps * 0.95, mathis_bps)
        # CUBIC navbat buferini to'ldirishga moyil (Queue delay ~ buffer_size * 1460 * 8 / bottleneck_bps)
        queue_delay_cubic_ms = (buffer_size_pkts * 1460 * 8 / bottleneck_bps) * 1000.0
        cubic_rtt = base_rtt_ms + queue_delay_cubic_ms * 0.7  # O'rtacha 70% to'la
        cubic_jitter = queue_delay_cubic_ms * 0.35
        cubic_retrans = int(cubic_rate * 5.0 * p / (1460 * 8))

        # 2. BBR model (Bottleneck Bandwidth and Round-trip propagation time):
        # BBR navbatda paket yig'ilishining oldini oladi (pacing_rate = BtlBw), minimum RTT ni kuzatadi.
        # Tasodifiy paket yo'qotishga sezgir emas (20% gacha barqaror).
        if loss_pct < 20.0:
            bbr_rate = bottleneck_bps * 0.93  # Kanalni to'liq egallaydi
        else:
            bbr_rate = bottleneck_bps * max(0.1, 1.0 - (loss_pct / 100.0))

        # BBR navbatda faqat ~1.25 BDP gacha saqlaydi, kechikish ancha past
        queue_delay_bbr_ms = (bdp_packets * 0.25 * 1460 * 8 / bottleneck_bps) * 1000.0
        bbr_rtt = base_rtt_ms + queue_delay_bbr_ms
        bbr_jitter = queue_delay_bbr_ms * 0.2
        bbr_retrans = int(bbr_rate * 5.0 * (loss_pct / 100.0) / (1460 * 8))

        # Mock oqimlar yaratamiz va compare_streams ga beramiz
        stream_a = {
            "stream_id": f"sim_{algo_a}",
            "congestion_algo": algo_a,
            "delivery_rate_bps": cubic_rate,
            "rtt_ms": cubic_rtt,
            "rttvar_ms": cubic_jitter,
            "total_retrans": cubic_retrans,
            "cwnd": int((cubic_rate * (cubic_rtt / 1000.0)) / (1460 * 8)),
            "bytes_acked": int(cubic_rate * 5.0 / 8.0),
        }

        stream_b = {
            "stream_id": f"sim_{algo_b}",
            "congestion_algo": algo_b,
            "delivery_rate_bps": bbr_rate,
            "rtt_ms": bbr_rtt,
            "rttvar_ms": bbr_jitter,
            "total_retrans": bbr_retrans,
            "cwnd": int((bbr_rate * (bbr_rtt / 1000.0)) / (1460 * 8)),
            "bytes_acked": int(bbr_rate * 5.0 / 8.0),
        }

        res = cls.compare_streams(
            stream_a,
            stream_b,
            label_a=f"{algo_a.upper()} (Standart)",
            label_b=f"{algo_b.upper()} (Model-Based)",
        )

        res["simulation_parameters"] = {
            "bottleneck_bandwidth_bps": bottleneck_bps,
            "base_rtt_ms": base_rtt_ms,
            "packet_loss_pct": loss_pct,
            "buffer_size_packets": buffer_size_pkts,
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
        """FIFO/DropTail va FQ-CoDel/AQM navbat algoritmlarini solishtirish."""
        # Yuklama 100% dan oshganda FIFO buferni to'ldirib, bufferbloat keltirib chiqaradi.
        is_overloaded = load_pct > 100.0

        if is_overloaded:
            fifo_queue_depth_pkts = 200
            fifo_sojourn_time_ms = (fifo_queue_depth_pkts * 1500 * 8 / link_bps) * 1000.0
            fifo_drops = int((load_pct - 100.0) * 10)
            fifo_bloat = True

            # FQ-CoDel Target sojourn time ~ 5ms, Interval ~ 100ms
            codel_queue_depth_pkts = 15
            codel_sojourn_time_ms = 5.2
            codel_drops = int(fifo_drops * 0.8)  # AQM orqali oldindan tashlash
            codel_bloat = False
        else:
            fifo_queue_depth_pkts = 5
            fifo_sojourn_time_ms = 2.0
            fifo_drops = 0
            fifo_bloat = False

            codel_queue_depth_pkts = 4
            codel_sojourn_time_ms = 1.8
            codel_drops = 0
            codel_bloat = False

        rtt_base = 20.0
        rtt_fifo = rtt_base + fifo_sojourn_time_ms
        rtt_codel = rtt_base + codel_sojourn_time_ms

        mos_fifo = VoIPMOSCalculator.calculate(
            delay_ms=rtt_fifo / 2.0,
            jitter_ms=fifo_sojourn_time_ms * 0.3,
            loss_pct=min(20.0, fifo_drops * 0.1),
        )
        mos_codel = VoIPMOSCalculator.calculate(
            delay_ms=rtt_codel / 2.0,
            jitter_ms=codel_sojourn_time_ms * 0.1,
            loss_pct=min(5.0, codel_drops * 0.05),
        )

        return {
            "qdisc_a": qdisc_a,
            "qdisc_b": qdisc_b,
            "link_bandwidth_bps": link_bps,
            "load_pct": load_pct,
            "comparison": {
                "sojourn_time_ms": {
                    "baseline": round(fifo_sojourn_time_ms, 2),
                    "candidate": round(codel_sojourn_time_ms, 2),
                    "delta_ms": round(codel_sojourn_time_ms - fifo_sojourn_time_ms, 2),
                    "reduction_pct": round(
                        (1.0 - (codel_sojourn_time_ms / max(0.1, fifo_sojourn_time_ms))) * 100.0, 1
                    ),
                },
                "total_rtt_ms": {
                    "baseline": round(rtt_fifo, 2),
                    "candidate": round(rtt_codel, 2),
                },
                "bufferbloat_detected": {
                    "baseline": fifo_bloat,
                    "candidate": codel_bloat,
                },
                "voip_mos": {
                    "baseline": mos_fifo.mos,
                    "candidate": mos_codel.mos,
                    "delta": round(mos_codel.mos - mos_fifo.mos, 2),
                },
            },
            "recommendation": (
                f"{qdisc_b.upper()} navbat kechikishini {round((1.0 - (codel_sojourn_time_ms / max(0.1, fifo_sojourn_time_ms))) * 100.0)}% ga qisqartirdi "
                f"va Bufferbloat holatining oldini oldi. Interaktiv oqimlar (VoIP) uchun MOS {mos_fifo.mos} dan {mos_codel.mos} ga yaxshilandi."
            ),
        }
