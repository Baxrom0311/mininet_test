"""Routing Convergence & Queue Dynamics Engine:
1. RoutingConvergenceTracker: OSPF/BGP/ISIS konvergentsiya vaqti (failover latency),
   SPF hisoblash vaqti va FIB yangilanishini o'lchovchi dvigatel.
2. QueueDynamicsSimulator: Apparat navbatlari (FIFO/Tail-Drop, RED, CoDel) va
   Bufferbloat hodisalarini modellashtiruvchi dvigatel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import random
from typing import Any, Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────
# 1. Routing Convergence Tracker (Failover & SPF Latency)
# ─────────────────────────────────────────────────────────────

@dataclass
class ConvergenceReport:
    event_type: str  # "link_down", "link_up", "node_failure"
    target: str  # e.g. "s1-s2" yoki "r1"
    protocol: str  # "OSPF", "BGP", "IS-IS"
    failure_detection_ms: float
    flooding_propagation_ms: float
    spf_calculation_ms: float
    fib_installation_ms: float
    total_convergence_ms: float
    transient_lost_packets: int
    new_path: List[str]
    alternate_mode: str  # "standard", "lfa_backup", "ecmp"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "target": self.target,
            "protocol": self.protocol,
            "failure_detection_ms": round(self.failure_detection_ms, 2),
            "flooding_propagation_ms": round(self.flooding_propagation_ms, 2),
            "spf_calculation_ms": round(self.spf_calculation_ms, 2),
            "fib_installation_ms": round(self.fib_installation_ms, 2),
            "total_convergence_ms": round(self.total_convergence_ms, 2),
            "transient_lost_packets": self.transient_lost_packets,
            "new_path": self.new_path,
            "alternate_mode": self.alternate_mode,
        }


class RoutingConvergenceTracker:
    """Marshrut konvergentsiyasini (Link-State LSA flooding, Dijkstra hisobi,
    FIB push) millisoniyalarda o'lchovchi sinf."""

    def __init__(self, carrier_detect: bool = True, bfd_enabled: bool = False):
        self.carrier_detect = carrier_detect  # Linux netlink carrier detect (1-5 ms)
        self.bfd_enabled = bfd_enabled  # Bidirectional Forwarding Detection (30-50 ms)

    def calculate_failover(
        self,
        event_type: str,
        target: str,
        num_routers: int,
        num_links: int,
        traffic_rate_pps: int = 1000,
        protocol: str = "OSPF",
        has_lfa_backup: bool = False,
    ) -> ConvergenceReport:
        proto = protocol.upper()

        # 1. Uzilishni aniqlash kechikishi (Detection Delay)
        if self.carrier_detect:
            detect_ms = 2.0 + random.uniform(0.5, 2.5)  # Apparat liniya uzilishi signali
        elif self.bfd_enabled:
            detect_ms = 30.0 + random.uniform(5.0, 15.0)  # BFD 3x interval
        else:
            # Standart OSPF DeadInterval yoki Hello timeout
            detect_ms = 4000.0 if proto == "OSPF" else 3000.0

        # LFA (Loop-Free Alternate / Fast Reroute) bo'lsa:
        # Apparat 50ms dan kam vaqtda zaxira yo'lga o'tadi
        if has_lfa_backup and event_type == "link_down":
            total_ms = detect_ms + 10.0  # Apparat darajasidagi tezkor yo'naltirish
            lost_pkts = int(math.ceil((total_ms / 1000.0) * traffic_rate_pps))
            return ConvergenceReport(
                event_type=event_type,
                target=target,
                protocol=proto,
                failure_detection_ms=detect_ms,
                flooding_propagation_ms=0.0,
                spf_calculation_ms=0.0,
                fib_installation_ms=5.0,
                total_convergence_ms=total_ms,
                transient_lost_packets=lost_pkts,
                new_path=["src", "lfa_backup", "dst"],
                alternate_mode="lfa_backup",
            )

        # 2. LSA / LSP Flooding tarqalish kechikishi: Diametr * HopRTT
        diameter = max(1, int(math.ceil(math.log2(max(2, num_routers)))))
        hop_delay = 2.5  # ms
        flooding_ms = diameter * hop_delay + (num_routers * 0.15)

        # 3. Dijkstra SPF hisoblash vaqti: O(E + V log V)
        # 10 tugun: ~0.5ms, 100 tugun: ~5ms, 1000 tugun: ~45ms
        spf_ops = num_links + num_routers * math.log2(max(2, num_routers))
        spf_ms = max(0.2, (spf_ops / 1500.0))

        # 4. Kernel / FIB marshrut yangilash kechikishi:
        fib_ms = 1.5 + (num_routers * 0.1)

        # Jami konvergentsiya vaqti
        total_ms = detect_ms + flooding_ms + spf_ms + fib_ms
        lost_pkts = int(math.ceil((total_ms / 1000.0) * traffic_rate_pps))

        return ConvergenceReport(
            event_type=event_type,
            target=target,
            protocol=proto,
            failure_detection_ms=detect_ms,
            flooding_propagation_ms=flooding_ms,
            spf_calculation_ms=spf_ms,
            fib_installation_ms=fib_ms,
            total_convergence_ms=total_ms,
            transient_lost_packets=lost_pkts,
            new_path=["src", "r_converged", "dst"],
            alternate_mode="standard",
        )


# ─────────────────────────────────────────────────────────────
# 2. Hardware Queue Dynamics & Bufferbloat Simulator
# ─────────────────────────────────────────────────────────────

@dataclass
class QueueStepReport:
    timestamp_s: float
    packets_in_queue: int
    queue_occupancy_pct: float
    queuing_delay_ms: float
    dropped_packets: int
    algorithm: str


@dataclass
class QueueSummary:
    algorithm: str
    buffer_capacity_pkts: int
    total_arrived: int
    total_transmitted: int
    total_dropped: int
    drop_rate_pct: float
    avg_queuing_delay_ms: float
    max_queuing_delay_ms: float
    bufferbloat_detected: bool
    history: List[QueueStepReport]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "buffer_capacity_pkts": self.buffer_capacity_pkts,
            "total_arrived": self.total_arrived,
            "total_transmitted": self.total_transmitted,
            "total_dropped": self.total_dropped,
            "drop_rate_pct": round(self.drop_rate_pct, 2),
            "avg_queuing_delay_ms": round(self.avg_queuing_delay_ms, 2),
            "max_queuing_delay_ms": round(self.max_queuing_delay_ms, 2),
            "bufferbloat_detected": self.bufferbloat_detected,
        }


class QueueDynamicsSimulator:
    """Apparat navbatlarini (Tail-Drop, RED, CoDel) modellashtiruvchi
    va Bufferbloat kechikishini aniqlovchi sinf."""

    def __init__(self, buffer_capacity_pkts: int = 100, link_rate_pps: int = 1000):
        self.capacity = buffer_capacity_pkts
        self.rate_pps = link_rate_pps

    def simulate(
        self,
        arrival_rates_pps: List[int],
        algorithm: str = "fifo",
        step_duration_s: float = 0.1,
    ) -> QueueSummary:
        algo = algorithm.lower()
        curr_queue = 0
        total_arrived = 0
        total_transmitted = 0
        total_dropped = 0
        steps: List[QueueStepReport] = []
        delays: List[float] = []

        # RED parametrlari
        min_th = self.capacity * 0.3
        max_th = self.capacity * 0.8
        max_p = 0.10

        # CoDel parametrlari
        target_delay_ms = 5.0
        codel_dropping = False

        for idx, arrival_pps in enumerate(arrival_rates_pps):
            t = idx * step_duration_s
            arrived = int(arrival_pps * step_duration_s)
            service_capacity = int(self.rate_pps * step_duration_s)
            total_arrived += arrived

            dropped = 0
            enqueued = 0

            if algo == "fifo" or algo == "tail_drop":
                # Oddiy Tail-Drop: to'lsa tashlanadi
                available_space = self.capacity - curr_queue
                if arrived <= available_space:
                    enqueued = arrived
                else:
                    enqueued = available_space
                    dropped = arrived - available_space
                curr_queue += enqueued

            elif algo == "red":
                # Random Early Detection (RED)
                for _ in range(arrived):
                    if curr_queue >= self.capacity:
                        dropped += 1
                    elif curr_queue > max_th:
                        dropped += 1
                    elif curr_queue > min_th:
                        p_drop = max_p * (curr_queue - min_th) / (max_th - min_th)
                        if random.random() < p_drop:
                            dropped += 1
                        else:
                            enqueued += 1
                            curr_queue += 1
                    else:
                        enqueued += 1
                        curr_queue += 1

            elif algo == "codel":
                # Controlled Delay (CoDel): sojourn delay > target_delay bo'lsa paketlarni tashlaydi
                target_pkts = max(2, int(target_delay_ms * self.rate_pps / 1000.0))
                for _ in range(arrived):
                    if curr_queue >= self.capacity:
                        dropped += 1
                    elif curr_queue > target_pkts and (random.random() < 0.40):
                        dropped += 1
                    else:
                        enqueued += 1
                        curr_queue += 1

            # Kechikish va to'liqlik navbatdagi paketlar bo'yicha o'lchanadi
            occ_pct = (curr_queue / self.capacity) * 100.0
            delay_ms = (curr_queue / self.rate_pps) * 1000.0
            delays.append(delay_ms)

            # Xizmat ko'rsatish (uzatish)
            transmitted = min(curr_queue, service_capacity)
            curr_queue = max(0, curr_queue - transmitted)
            total_transmitted += transmitted
            total_dropped += dropped

            steps.append(
                QueueStepReport(
                    timestamp_s=round(t, 2),
                    packets_in_queue=curr_queue,
                    queue_occupancy_pct=round(occ_pct, 1),
                    queuing_delay_ms=round(delay_ms, 2),
                    dropped_packets=dropped,
                    algorithm=algo,
                )
            )

        drop_rate = (total_dropped / max(1, total_arrived)) * 100.0
        avg_delay = sum(delays) / max(1, len(delays))
        max_delay = max(delays) if delays else 0.0

        # Bufferbloat aniqlash: Agar o'rtacha navbat kechikishi > 30ms bo'lsa
        bufferbloat = avg_delay > 30.0

        return QueueSummary(
            algorithm=algo,
            buffer_capacity_pkts=self.capacity,
            total_arrived=total_arrived,
            total_transmitted=total_transmitted,
            total_dropped=total_dropped,
            drop_rate_pct=drop_rate,
            avg_queuing_delay_ms=avg_delay,
            max_queuing_delay_ms=max_delay,
            bufferbloat_detected=bufferbloat,
            history=steps,
        )
